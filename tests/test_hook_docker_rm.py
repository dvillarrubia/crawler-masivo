"""Que el hook de sesiones no deje pasar un `docker rm -f` sin `-v`.

El `--rm` de `docker run` borra el volumen anonimo al pararse el contenedor,
pero un `docker rm -f` explicito no: el 2026-10-21 habia 77 datadirs de
Postgres huerfanos (53 GB) en el Mac, restos de pgvector efimeros de pruebas.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "sesiones.py"

_spec = importlib.util.spec_from_file_location("sesiones", HOOK)
sesiones = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sesiones)


@pytest.mark.parametrize("cmd", [
    "docker rm -f pg-$N",
    "docker rm -f pg-$N redis-$N >/dev/null; docker network rm $N",
    "docker container rm --force ux-api",
    "docker rm pg-1 -f",
    "ssh vps 'docker rm -f prueba'",
    "docker rm -fv a && docker rm -f b",
])
def test_bloquea_rm_forzado_sin_volumenes(cmd):
    assert sesiones.rm_sin_volumenes(cmd)


@pytest.mark.parametrize("cmd", [
    "docker rm -fv pg-$N",
    "docker rm -vf pg-$N",
    "docker rm -f -v pg-$N",
    "docker container rm --force --volumes ux-api",
    "docker rm ux-api",
    "docker network rm $N",
    "docker run -d --rm --name pg-1 pgvector/pgvector:pg16",
    "docker volume rm crawler-masivo_pgdata",
    "docker rmi -f crawler-prueba-version",
    "git rm -f fichero.py",
])
def test_deja_pasar_lo_demas(cmd):
    assert not sesiones.rm_sin_volumenes(cmd)


def _hook(cmd: str, cwd: Path) -> str:
    entrada = {"session_id": "test-docker-rm", "cwd": str(cwd),
               "tool_input": {"command": cmd}}
    r = subprocess.run([sys.executable, str(HOOK), "hook-bash"], input=json.dumps(entrada),
                       capture_output=True, text=True, cwd=cwd, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_el_hook_deniega_de_verdad(tmp_path):
    # Un repo de usar y tirar: el hook guarda su estado en el git-common-dir del cwd.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    salida = _hook("docker rm -f pg-1", tmp_path)
    decision = json.loads(salida)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "docker rm -fv" in decision["permissionDecisionReason"]
    assert _hook("docker rm -fv pg-1", tmp_path) == ""
    assert _hook("SESIONES_FORZAR=1 docker rm -f pg-1", tmp_path) == ""
