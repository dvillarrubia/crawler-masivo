"""Lo que el servidor MCP puede CAMBIAR (decision 151): lanzar rastreos y
canarios, cancelarlos, y crear, pausar o reprogramar rastreos programados.

Cada accion va en dos pasos: sin `confirmar` devuelve lo que haria (cliente,
perfil, semillas, configuracion efectiva y lo que ya esta rastreando en
produccion) y no toca nada; con `confirmar=True` lo hace. Lo que dice la
decision 148 para la escritura: un `simular` antes, para que quien habla con
el asistente vea lo que va a pasar antes de que pase. Borrar no esta: ni
rastreos ni programaciones (se pausan).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mcp_servidor.herramientas import Api, _cliente_id

# El canario de `scripts/lanzar_job.py` (lo comprueba un test): pocas URLs,
# poca profundidad, una hora y suave con el WAF. Lo demas, la configuracion
# del cliente, para que valide la MISMA que el rastreo completo.
CANARIO = {"max_depth": 3, "max_urls": 300,
           "crawl_behavior": {"max_runtime_hours": 1, "autothrottle_target_concurrency": 2.0}}
EN_MARCHA = ("pending", "queued", "running", "analyzing")
_RESUMEN = ("max_urls", "max_depth", "render_js", "robots_mode", "use_sitemap")


def _poner(cfg: dict, clave: str, valor: Any) -> None:
    partes = clave.split(".")
    d = cfg
    for p in partes[:-1]:
        d = d.setdefault(p, {})
    d[partes[-1]] = valor


def _fundir(base: dict, encima: dict) -> dict:
    salida = dict(base)
    for k, v in encima.items():
        salida[k] = _fundir(salida[k], v) if isinstance(v, dict) and isinstance(salida.get(k), dict) else v
    return salida


def _cambios(canario: bool, ajustes: dict | None) -> dict:
    """Lo que el rastreo cambia sobre lo heredado. `ajustes` admite claves con
    puntos (`crawl_behavior.request_delay`) como `lanzar_job.py --set`."""
    cfg: dict = _fundir({}, CANARIO) if canario else {}
    for k, v in (ajustes or {}).items():
        _poner(cfg, k, v)
    return cfg


def _claves(d: dict, prefijo: str = "") -> set[str]:
    salida = set()
    for k, v in d.items():
        salida |= _claves(v, f"{prefijo}{k}.") if isinstance(v, dict) and v else {f"{prefijo}{k}"}
    return salida


def _perfil(api: Api, cid: str, perfil: str | None) -> dict | None:
    """El perfil por id o nombre. Sin nombrarlo: el unico propio activo; si hay
    varios, se pide cual (lanzar el que no era cuesta horas)."""
    perfiles = [p for p in api.get(f"/clients/{cid}/profiles") if p.get("activo", True)]
    if perfil:
        p = perfil.strip().lower()
        for x in perfiles:
            if p in (str(x["id"]).lower(), x["nombre"].lower()):
                return x
        raise ValueError(f"No hay ningún perfil «{perfil}». Hay: " + ", ".join(x["nombre"] for x in perfiles))
    propios = [x for x in perfiles if x.get("rol", "propio") == "propio"]
    if len(propios) == 1:
        return propios[0]
    if not propios:
        return None
    raise ValueError("Este cliente tiene varios perfiles; di cuál: " + ", ".join(x["nombre"] for x in propios))


def _en_marcha(api: Api) -> list[dict]:
    salida = []
    for estado in EN_MARCHA:
        for j in api.get("/jobs", status=estado, page_size=20)["items"]:
            salida.append({"id": j["id"], "nombre": j["name"], "estado": j["status"],
                           "urls": j.get("total_urls_crawled")})
    return salida


def _plan(api: Api, cliente: str, perfil: str | None, canario: bool, ajustes: dict | None,
          semillas: list[str] | None) -> dict:
    cid = _cliente_id(api, cliente)
    p = _perfil(api, cid, perfil)
    res = api.get(f"/clients/{cid}/config-resuelta", profile_id=p["id"] if p else None)
    cambios = _cambios(canario, ajustes)
    efectiva = _fundir(res["config"], cambios)
    seeds = list(semillas or res.get("seeds") or [])
    if not seeds:
        raise ValueError("Faltan semillas: el perfil no tiene y no se han dado")
    return {"cliente_id": cid, "perfil": p, "semillas": seeds, "cambios": cambios, "efectiva": efectiva,
            "semillas_propias": bool(semillas)}


def lanzar_rastreo(api: Api, cliente: str, perfil: str | None = None, canario: bool = False,
                   ajustes: dict | None = None, semillas: list[str] | None = None,
                   nombre: str | None = None, confirmar: bool = False) -> dict:
    plan = _plan(api, cliente, perfil, canario, ajustes, semillas)
    p = plan["perfil"]
    if not nombre:
        base = cliente + (f" · {p['nombre']}" if p else "")
        nombre = (f"CANARY - {base}" if canario
                  else f"{base} · {datetime.now(timezone.utc).date().isoformat()}")
    vista = {"nombre": nombre, "cliente": cliente, "perfil": p["nombre"] if p else None,
             "tipo": "canario" if canario else "censo", "semillas": plan["semillas"],
             "configuracion": {k: plan["efectiva"].get(k) for k in _RESUMEN},
             "limite_horas": (plan["efectiva"].get("crawl_behavior") or {}).get("max_runtime_hours"),
             "cambios_sobre_lo_heredado": plan["cambios"], "en_marcha_ahora": _en_marcha(api)}
    if not confirmar:
        return {"simulacion": True, **vista,
                "siguiente_paso": "Nada lanzado. Si es esto, llama otra vez con confirmar=true."}
    cuerpo: dict[str, Any] = {"name": nombre, "cliente_id": plan["cliente_id"], "config": plan["cambios"]}
    if p:
        cuerpo["profile_id"] = p["id"]
    if plan["semillas_propias"] or not p:
        cuerpo["seeds"] = plan["semillas"]
    job = api.enviar("POST", "/jobs", cuerpo)
    # Pydantic tira en silencio la clave que JobConfig no declara (decision 33):
    # se dice aqui, que es cuando alguien esta mirando.
    descartadas = sorted(_claves(plan["cambios"]) - _claves(job.get("config") or {}))
    return {"lanzado": True, "id": job["id"], "nombre": job["name"], "estado": job["status"],
            "enlace": f"{api.publica}/#/rastreos/{job['id']}", "claves_descartadas": descartadas,
            "en_marcha_antes": vista["en_marcha_ahora"]}


def estado_rastreo(api: Api, rastreo: str) -> dict:
    """Como va un rastreo: estado, URLs, cola pendiente y motivo de fin."""
    j = api.get(f"/jobs/{rastreo}")
    prog = api.get(f"/jobs/{rastreo}/progress")
    return {"id": j["id"], "nombre": j["name"], "estado": j["status"], "motivo": j.get("finish_reason"),
            "creado": j.get("created_at"), "empezado": j.get("started_at"), "terminado": j.get("completed_at"),
            "progreso": prog, "enlace": f"{api.publica}/#/rastreos/{j['id']}"}


def cancelar_rastreo(api: Api, rastreo: str, confirmar: bool = False) -> dict:
    j = api.get(f"/jobs/{rastreo}")
    if j["status"] not in ("pending", "running"):
        raise ValueError(f"«{j['name']}» está {j['status']}: solo se cancela lo pendiente o en marcha")
    vista = {"id": j["id"], "nombre": j["name"], "estado": j["status"], "urls": j.get("total_urls_crawled")}
    if not confirmar:
        return {"simulacion": True, **vista, "siguiente_paso":
                "Nada cancelado. Lo rastreado se queda, pero el censo no sera completo. "
                "Si es este, llama otra vez con confirmar=true."}
    j = api.enviar("PATCH", f"/jobs/{rastreo}/cancel")
    return {"cancelado": True, "id": j["id"], "nombre": j["name"], "estado": j["status"]}


def _programacion(p: dict) -> dict:
    return {"id": p["id"], "nombre": p["nombre"], "cliente": p.get("client_id"), "cron": p["cron"],
            "zona_horaria": p.get("zona_horaria"), "activa": p["activa"],
            "proxima": p.get("proxima_ejecucion"), "ultima": p.get("ultima_ejecucion"),
            "ultimo_rastreo": p.get("ultimo_job_id")}


def programaciones(api: Api, cliente: str | None = None) -> list[dict]:
    slug = None
    if cliente:
        cid = _cliente_id(api, cliente)
        slug = next(c["slug"] for c in api.get("/clients") if str(c["id"]) == str(cid))
    return [_programacion(p) for p in api.get("/programaciones", client_id=slug)]


def programar_rastreo(api: Api, cliente: str, cron: str, perfil: str | None = None, canario: bool = False,
                      zona_horaria: str = "Europe/Madrid", ajustes: dict | None = None,
                      semillas: list[str] | None = None, nombre: str | None = None,
                      confirmar: bool = False) -> dict:
    plan = _plan(api, cliente, perfil, canario, ajustes, semillas)
    p = plan["perfil"]
    nombre = nombre or (("CANARY - " if canario else "") + cliente + (f" · {p['nombre']}" if p else ""))
    vista = {"nombre": nombre, "cliente": cliente, "perfil": p["nombre"] if p else None,
             "cron": cron, "zona_horaria": zona_horaria, "semillas": plan["semillas"],
             "configuracion": {k: plan["efectiva"].get(k) for k in _RESUMEN},
             "cambios_sobre_lo_heredado": plan["cambios"]}
    if not confirmar:
        return {"simulacion": True, **vista, "siguiente_paso":
                "Nada programado. Cron de cinco campos (minuto hora dia mes dia-semana). "
                "Si es esto, llama otra vez con confirmar=true."}
    # Una programacion guarda su configuracion entera (la del cliente de hoy
    # mas los cambios): es la que usara cada disparo.
    cuerpo = {"nombre": nombre, "cliente_id": plan["cliente_id"], "seeds": plan["semillas"],
              "config": plan["efectiva"], "cron": cron, "zona_horaria": zona_horaria, "activa": True}
    if p:
        cuerpo["profile_id"] = p["id"]
    return {"programado": True, **_programacion(api.enviar("POST", "/programaciones", cuerpo))}


def editar_programacion(api: Api, programacion: str, activa: bool | None = None, cron: str | None = None,
                        zona_horaria: str | None = None, confirmar: bool = False) -> dict:
    """Pausar (`activa=false`), reanudar o cambiar el cuando."""
    actual = _programacion(api.get(f"/programaciones/{programacion}"))
    cambios = {k: v for k, v in (("activa", activa), ("cron", cron), ("zona_horaria", zona_horaria))
               if v is not None}
    if not cambios:
        raise ValueError("No hay nada que cambiar: activa, cron o zona_horaria")
    if not confirmar:
        return {"simulacion": True, "ahora": actual, "cambios": cambios,
                "siguiente_paso": "Nada cambiado. Si es esto, llama otra vez con confirmar=true."}
    return {"editada": True, **_programacion(api.enviar("PATCH", f"/programaciones/{programacion}", cambios))}
