#!/usr/bin/env python3
"""Coordinacion entre las sesiones de Claude Code abiertas sobre este repo.

Varias terminales trabajan a la vez sobre el mismo repositorio y la MISMA
produccion. Lo que ya ha salido mal (2026-10-13): un `git checkout` en el
directorio principal le cambio la rama a otra sesion, y un merge con
«0 running» pillo un analisis en curso y bloqueo la API diez minutos.

Este script es a la vez la herramienta de linea de comandos y los hooks:

  SessionStart      hook-inicio   registra la sesion y le pinta el tablero
  UserPromptSubmit  hook-latido   marca que la sesion sigue viva
  PreToolUse(Bash)  hook-bash     bloquea checkout en el directorio principal,
                                  merge con produccion ocupada o con otro
                                  despliegue en vuelo, rastreos durante un
                                  despliegue y `docker rm -f` sin `-v`
  SessionEnd        hook-fin      da de baja la sesion y suelta sus candados

  tablero                         quien hay, en que worktree y haciendo que
  tarea <id> <texto>              declarar en que trabaja una sesion
  candado ver|tomar|soltar <nombre> [<id>]
  prod                            ¿esta ocupada produccion? (jobs, candados, analisis)
  numero-decision <id>            reserva el siguiente numero de decision libre

El estado vive en `<git-common-dir>/claude-sesiones/`: lo comparten todos los
worktrees de esta maquina y no se versiona. Solo stdlib: corre antes de que
exista ningun entorno virtual.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DORMIDA_S = 3 * 3600          # sin latido: se pinta como dormida
OLVIDADA_S = 48 * 3600        # sin latido: se borra del tablero
TTL_DEPLOY_S = 20 * 60        # un merge despliega en 2-3 min; margen amplio
FORZAR = "SESIONES_FORZAR=1"  # prefijo para saltarse el guardia (solo si el usuario lo pide)
AQUI = Path(__file__).resolve().parent


def _git(*args: str, cwd: str | None = None) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _dir_estado(cwd: str | None = None) -> Path:
    comun = _git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd)
    base = Path(comun) if comun else AQUI.parent.parent / ".git"
    d = base / "claude-sesiones"
    (d / "candados").mkdir(parents=True, exist_ok=True)
    return d


def _worktree_principal(cwd: str | None = None) -> str:
    for linea in _git("worktree", "list", "--porcelain", cwd=cwd).splitlines():
        if linea.startswith("worktree "):
            return linea[len("worktree "):]
    return ""


def _leer(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _escribir(p: Path, datos: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1))
    tmp.replace(p)


def _hace(ts: float) -> str:
    s = int(time.time() - ts)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}min"
    return f"{s // 3600}h"


# --------------------------------------------------------------------- sesiones

def _sesiones(d: Path) -> list[dict]:
    vivas = []
    for p in d.glob("*.json"):
        s = _leer(p)
        if not s or time.time() - s.get("latido", 0) > OLVIDADA_S:
            p.unlink(missing_ok=True)
            continue
        vivas.append(s)
    return sorted(vivas, key=lambda s: s.get("inicio", 0))


def _registrar(d: Path, sid: str, cwd: str, **extra) -> dict:
    p = d / f"{sid}.json"
    s = _leer(p) or {"id": sid, "inicio": time.time(), "tarea": ""}
    s.update(cwd=cwd, worktree=_git("rev-parse", "--show-toplevel", cwd=cwd) or cwd,
             rama=_git("branch", "--show-current", cwd=cwd), latido=time.time(), **extra)
    _escribir(p, s)
    return s


# --------------------------------------------------------------------- candados

def _candado(d: Path, nombre: str) -> dict | None:
    """El candado vigente, o None. Uno caducado o de una sesion olvidada no cuenta."""
    p = d / "candados" / f"{nombre}.json"
    c = _leer(p)
    if not c:
        return None
    if time.time() > c.get("expira", 0) or not (d / f"{c.get('sesion')}.json").exists():
        p.unlink(missing_ok=True)
        return None
    return c


def _tomar(d: Path, nombre: str, sid: str, ttl: int, motivo: str = "") -> dict | None:
    """Toma el candado. Devuelve el del OTRO si lo tiene otra sesion."""
    actual = _candado(d, nombre)
    if actual and actual["sesion"] != sid:
        return actual
    _escribir(d / "candados" / f"{nombre}.json",
              {"sesion": sid, "desde": time.time(), "expira": time.time() + ttl, "motivo": motivo})
    return None


def _soltar(d: Path, nombre: str, sid: str | None = None) -> None:
    c = _candado(d, nombre)
    if c and (sid is None or c["sesion"] == sid):
        (d / "candados" / f"{nombre}.json").unlink(missing_ok=True)


# ------------------------------------------------------------------- produccion

def _prod_ocupada() -> tuple[bool | None, str]:
    """(ocupada, detalle). None = no se pudo comprobar (y eso NO es «libre»)."""
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "vps", "bash -s"],
            stdin=open(AQUI / "ocupado-prod.sh"), capture_output=True, text=True, timeout=40)
    except Exception as e:
        return None, f"ssh vps fallo: {e}"
    partes = r.stdout.split()
    if r.returncode != 0 or len(partes) != 3:
        return None, f"ssh vps no devolvio «jobs candados analisis»: {r.stdout.strip() or r.stderr.strip()[:200]}"
    jobs, candados, analisis = partes
    detalle = f"jobs en marcha={jobs}, candados advisory={candados}, analisis por docker exec={analisis}"
    return (partes != ["0", "0", "0"]), detalle


def _despliegue_en_vuelo(cwd: str) -> tuple[bool | None, str]:
    try:
        r = subprocess.run(
            ["gh", "run", "list", "--workflow=deploy.yml", "--limit", "3",
             "--json", "status,displayTitle,createdAt"],
            cwd=cwd, capture_output=True, text=True, timeout=20)
        runs = json.loads(r.stdout)
    except Exception as e:
        return None, f"gh run list fallo: {e}"
    vivos = [x for x in runs if x.get("status") not in ("completed", None)]
    if vivos:
        return True, "; ".join(f"«{x['displayTitle']}» ({x['status']})" for x in vivos)
    return False, ""


# ------------------------------------------------------------------- decisiones

_DECISION = re.compile(r"^(\d+)[a-z]?\.\s+\*\*", re.M)
RESERVA_S = 14 * 86400


def numero_decision(d: Path, sid: str) -> int:
    """El siguiente numero de decision que no usa NADIE: ni master, ni ningun
    worktree de esta maquina, ni otra sesion que lo haya reservado y aun no lo
    haya escrito. Dos ramas largas cogiendo «el siguiente hueco» a la vez
    chocaron tres veces el 2026-10-13."""
    usados: set[int] = set()
    principal = _worktree_principal()
    _git("fetch", "-q", "origin", "master", cwd=principal)
    for texto in [_git("grep", "-h", "-E", r"^[0-9]+[a-z]?\. \*\*", "origin/master", "--",
                       "docs/decisiones/", "CLAUDE.md", cwd=principal)]:
        usados |= {int(n) for n in _DECISION.findall(texto)}
    for linea in _git("worktree", "list", "--porcelain").splitlines():
        if linea.startswith("worktree "):
            raiz = Path(linea[9:])  # CLAUDE.md: ramas anteriores a docs/decisiones/
            for f in [*raiz.joinpath("docs", "decisiones").glob("*.md"), raiz / "CLAUDE.md"]:
                if not f.exists():
                    continue
                usados |= {int(n) for n in _DECISION.findall(f.read_text(errors="ignore"))}
    p = d / "reservas-decisiones.json"
    reservas = {k: v for k, v in _leer(p).items() if time.time() - v["ts"] < RESERVA_S}
    usados |= {int(k) for k in reservas}
    n = max(usados or {0}) + 1
    reservas[str(n)] = {"sesion": sid, "ts": time.time()}
    _escribir(p, reservas)
    return n


# ---------------------------------------------------------------------- tablero

def tablero(d: Path, yo: str | None = None, cwd: str | None = None) -> str:
    principal = _worktree_principal(cwd)
    ses = _sesiones(d)
    out = ["SESIONES DE CLAUDE EN ESTE REPO"]
    for s in ses:
        estado = "dormida" if time.time() - s["latido"] > DORMIDA_S else "activa"
        marca = " <- TU" if s["id"] == yo else ""
        wt = s.get("worktree", "?")
        if wt == principal:
            wt += " (DIRECTORIO PRINCIPAL)"
        out.append(f"- {s['id'][:8]} [{estado}, latido hace {_hace(s['latido'])}]{marca}\n"
                   f"    worktree: {wt}  rama: {s.get('rama') or '?'}\n"
                   f"    tarea: {s.get('tarea') or '(sin declarar)'}")
    if not ses:
        out.append("- (ninguna)")
    out.append("\nWORKTREES")
    bloque: dict = {}
    for linea in _git("worktree", "list", "--porcelain", cwd=cwd).splitlines() + [""]:
        if linea.startswith("worktree "):
            bloque = {"ruta": linea[9:]}
        elif linea.startswith("branch "):
            bloque["rama"] = linea[7:].replace("refs/heads/", "")
        elif not linea and bloque:
            sucios = _git("status", "--porcelain", cwd=bloque["ruta"]).splitlines()
            due = [s["id"][:8] for s in ses if s.get("worktree") == bloque["ruta"]]
            out.append(f"- {bloque['ruta']}  [{bloque.get('rama', 'detached')}]"
                       f"  {len(sucios)} cambios sin commit"
                       f"  sesiones: {', '.join(due) or 'ninguna'}")
            bloque = {}
    c = _candado(d, "deploy")
    out.append("\nCANDADO DE DESPLIEGUE: " + (
        f"lo tiene {c['sesion'][:8]} desde hace {_hace(c['desde'])} ({c.get('motivo', '')})" if c else "libre"))
    return "\n".join(out)


# ------------------------------------------------------------------------ hooks

def _stdin() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def hook_inicio() -> None:
    e = _stdin()
    sid, cwd = e.get("session_id", "sin-id"), e.get("cwd") or os.getcwd()
    d = _dir_estado(cwd)
    s = _registrar(d, sid, cwd)
    principal = _worktree_principal(cwd)
    otras = [x for x in _sesiones(d) if x["id"] != sid and time.time() - x["latido"] < DORMIDA_S]
    avisos = []
    if s["worktree"] == principal:
        avisos.append(
            "Estas en el DIRECTORIO PRINCIPAL del repo. Para cualquier cambio de codigo crea tu "
            "worktree antes de tocar nada:\n"
            f"  git -C {principal} fetch -q origin && "
            f"git -C {principal} worktree add -b <rama> ../crawler-masivo-<tema> origin/master\n"
            "y trabaja ahi con rutas absolutas. En el principal nunca checkout/switch "
            "(el hook lo bloquea): le cambia la rama a las demas sesiones.")
    if otras:
        avisos.append(
            f"Hay {len(otras)} sesion(es) activa(s) mas. Antes de mergear o lanzar algo largo, "
            "coordina con ellas (ListAgents / SendMessage) y no toques sus worktrees.")
    ctx = (f"{tablero(d, sid, cwd)}\n\nTu id de sesion: {sid}\n"
           f"Declara en que trabajas en cuanto lo sepas (lo ven las demas terminales):\n"
           f"  python3 {AQUI / 'sesiones.py'} tarea {sid} \"<tarea en una linea>\"\n"
           + ("\n" + "\n".join(avisos) if avisos else ""))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": ctx}}))


def hook_latido() -> None:
    e = _stdin()
    if e.get("session_id"):
        cwd = e.get("cwd") or os.getcwd()
        _registrar(_dir_estado(cwd), e["session_id"], cwd)


def hook_fin() -> None:
    e = _stdin()
    sid = e.get("session_id")
    if not sid:
        return
    d = _dir_estado(e.get("cwd"))
    for p in (d / "candados").glob("*.json"):
        if _leer(p).get("sesion") == sid:
            p.unlink(missing_ok=True)
    (d / f"{sid}.json").unlink(missing_ok=True)


def _denegar(motivo: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": motivo}}))
    sys.exit(0)


def _dir_efectivo(cmd: str, cwd: str) -> str:
    """El directorio donde corre el git del comando: `git -C X` o el ultimo `cd X`."""
    m = re.findall(r"git\s+-C\s+(\S+)", cmd) or re.findall(r"(?:^|&&|;)\s*cd\s+(\S+)", cmd)
    if not m:
        return cwd
    ruta = os.path.expanduser(m[-1].strip("'\""))
    return ruta if os.path.isabs(ruta) else os.path.normpath(os.path.join(cwd, ruta))


# `docker rm` / `docker container rm` hasta el final de esa orden (`;`, `&&`, `|`, salto, comilla).
_DOCKER_RM = re.compile(r"\bdocker\s+(?:container\s+)?rm\b([^;&|\n'\"]*)")


def rm_sin_volumenes(cmd: str) -> bool:
    """¿Hay un `docker rm -f` que no lleve `-v`?

    El `--rm` de `docker run` borra el volumen anonimo del contenedor al pararse,
    pero un `docker rm -f` explicito se lo salta y el volumen se queda huerfano:
    77 datadirs de Postgres (53 GB) de pruebas con pgvector efimero en el Mac
    (2026-10-21). `-v` solo borra los anonimos, nunca uno con nombre, asi que
    pedirlo siempre no pone en riesgo ningun dato."""
    for m in _DOCKER_RM.finditer(cmd):
        cortas = "".join(t[1:] for t in m.group(1).split() if re.fullmatch(r"-[a-zA-Z]+", t))
        largas = set(re.findall(r"--[\w-]+", m.group(1)))
        if ("f" in cortas or "--force" in largas) and not ("v" in cortas or "--volumes" in largas):
            return True
    return False


def hook_bash() -> None:
    e = _stdin()
    cmd = (e.get("tool_input") or {}).get("command", "")
    sid, cwd = e.get("session_id", "sin-id"), e.get("cwd") or os.getcwd()
    d = _dir_estado(cwd)
    _registrar(d, sid, cwd)
    forzado = FORZAR in cmd

    # 1. Nada de cambiar de rama en el directorio principal.
    if re.search(r"\bgit\b(\s+-C\s+\S+)?\s+(checkout|switch)\b", cmd) and " -- " not in cmd:
        donde = _git("rev-parse", "--show-toplevel", cwd=_dir_efectivo(cmd, cwd))
        if donde and donde == _worktree_principal(cwd) and not forzado:
            _denegar("Bloqueado por .claude/hooks/sesiones.py: checkout/switch en el DIRECTORIO "
                     "PRINCIPAL cambia la rama de todas las sesiones que trabajan ahi. Crea un "
                     "worktree propio (git worktree add -b <rama> ../crawler-masivo-<tema> "
                     "origin/master). Para restaurar un fichero usa `git checkout -- <fichero>`.")

    # 2. Un merge despliega y recrea api y worker: produccion libre y nadie mas desplegando.
    if re.search(r"\bgh\s+pr\s+merge\b", cmd) and not forzado:
        otro = _candado(d, "deploy")
        if otro and otro["sesion"] != sid:
            _denegar(f"Bloqueado: la sesion {otro['sesion'][:8]} tiene el candado de despliegue "
                     f"desde hace {_hace(otro['desde'])} ({otro.get('motivo', '')}). Espera a que "
                     "termine su despliegue o coordina con ella (SendMessage).")
        vuelo, det = _despliegue_en_vuelo(cwd)
        if vuelo is not False:
            _denegar("Bloqueado: " + (f"hay un despliegue en vuelo: {det}" if vuelo
                                      else f"no se pudo comprobar si hay un despliegue en vuelo ({det})"))
        ocupada, det = _prod_ocupada()
        if ocupada is not False:
            _denegar("Bloqueado: " + (f"produccion esta ocupada ({det}). Un merge despliega y "
                                      "recrea api y worker: mataria lo que este corriendo."
                                      if ocupada else f"no se pudo comprobar produccion: {det}")
                     + f" Si el usuario lo autoriza expresamente, repite con el prefijo {FORZAR}.")
        _tomar(d, "deploy", sid, TTL_DEPLOY_S, motivo=cmd[:120])

    # 3. Lanzar un rastreo durante un despliegue es matarlo.
    if re.search(r"lanzar_job\.py|/api/jobs\b.*(-X\s*POST|--data|-d\s)|(-X\s*POST|--data|-d\s).*/api/jobs\b",
                 cmd) and not forzado:
        otro = _candado(d, "deploy")
        if otro and otro["sesion"] != sid:
            _denegar(f"Bloqueado: la sesion {otro['sesion'][:8]} esta desplegando "
                     f"(desde hace {_hace(otro['desde'])}). El despliegue recrea el worker y "
                     "mataria el rastreo. Espera a que termine.")
        vuelo, det = _despliegue_en_vuelo(cwd)
        if vuelo:
            _denegar(f"Bloqueado: hay un despliegue en vuelo ({det}); recrearia el worker a mitad "
                     "del rastreo. Espera a que termine.")

    # 4. Un contenedor borrado a la fuerza sin -v deja su volumen anonimo (Postgres: >1 GB).
    if rm_sin_volumenes(cmd) and not forzado:
        _denegar("Bloqueado por .claude/hooks/sesiones.py: `docker rm -f` sin `-v` deja huerfano "
                 "el volumen anonimo del contenedor (un Postgres efimero, mas de 1 GB cada vez). "
                 "Usa `docker rm -fv`: `-v` solo borra los volumenes anonimos, nunca uno con nombre.")


# -------------------------------------------------------------------------- cli

def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    orden, args = argv[0], argv[1:]
    hooks = {"hook-inicio": hook_inicio, "hook-latido": hook_latido,
             "hook-bash": hook_bash, "hook-fin": hook_fin}
    if orden in hooks:
        try:
            hooks[orden]()
        except SystemExit:
            raise
        except Exception as ex:  # un hook roto no puede dejar la terminal inservible
            print(f"sesiones.py {orden}: {ex}", file=sys.stderr)
        return 0
    d = _dir_estado()
    if orden == "tablero":
        print(tablero(d))
    elif orden == "tarea" and len(args) >= 2:
        p = d / f"{args[0]}.json"
        s = _leer(p) or {"id": args[0], "inicio": time.time(), "latido": time.time()}
        s["tarea"] = " ".join(args[1:])
        _escribir(p, s)
        print(f"tarea de {args[0][:8]}: {s['tarea']}")
    elif orden == "candado" and args and args[0] in ("ver", "tomar", "soltar"):
        nombre = args[1] if len(args) > 1 else "deploy"
        if args[0] == "ver":
            print(_candado(d, nombre) or "libre")
        elif args[0] == "tomar" and len(args) > 2:
            otro = _tomar(d, nombre, args[2], TTL_DEPLOY_S)
            print(f"ocupado por {otro['sesion']}" if otro else "tomado")
            return 1 if otro else 0
        elif args[0] == "soltar":
            _soltar(d, nombre, args[2] if len(args) > 2 else None)
            print("soltado")
        else:
            print("uso: candado tomar <nombre> <id-sesion>")
            return 2
    elif orden == "numero-decision" and args:
        print(numero_decision(d, args[0]))
    elif orden == "prod":
        ocupada, det = _prod_ocupada()
        print({True: "OCUPADA", False: "libre", None: "NO SE SABE"}[ocupada], "-", det)
        return 0 if ocupada is False else 1
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
