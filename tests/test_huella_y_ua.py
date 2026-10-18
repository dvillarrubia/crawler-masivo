"""El UA y la huella TLS son el mismo navegador, y la huella se valida (decision 132).

Corre en las dos imagenes: `JobConfig` vive en la API, que no lleva curl_cffi,
y por eso la lista de perfiles es una copia que el test de abajo contrasta con
el curl_cffi instalado (en la imagen del crawler).
"""

from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace

import pytest

from shared import perfiles
from shared.config import DEFAULT_USER_AGENT


def _jobconfig():
    """`JobConfig` solo existe donde hay pydantic (la imagen de la API)."""
    pytest.importorskip("pydantic")
    from api.schemas import JobConfig

    return JobConfig

RAIZ = pathlib.Path(__file__).resolve().parent.parent
UA_124 = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
UA_FIREFOX = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0"


def test_la_lista_de_perfiles_es_la_de_curl_cffi_instalado():
    curl_cffi = pytest.importorskip("curl_cffi")
    from curl_cffi.requests import BrowserType

    instalados = {b.value for b in BrowserType}
    assert perfiles.PERFILES_IMPERSONATE <= instalados, (
        curl_cffi.__version__, perfiles.PERFILES_IMPERSONATE - instalados)


def test_el_de_serie_es_coherente_y_sale_de_una_sola_constante():
    assert perfiles.IMPERSONATE_POR_DEFECTO == "chrome150"
    assert perfiles.IMPERSONATE_POR_DEFECTO in perfiles.PERFILES_IMPERSONATE
    assert DEFAULT_USER_AGENT == perfiles.UA_POR_DEFECTO
    assert "Chrome/150.0.0.0" in DEFAULT_USER_AGENT and "Windows" in DEFAULT_USER_AGENT
    assert perfiles.incoherencia_ua_huella(DEFAULT_USER_AGENT,
                                           perfiles.IMPERSONATE_POR_DEFECTO) is None

def test_el_jobconfig_de_serie_es_coherente():
    cfg = _jobconfig()()
    assert cfg.impersonate == "chrome150"
    assert perfiles.incoherencia_ua_huella(cfg.user_agent, cfg.impersonate) is None


def test_los_ua_de_paridad_casan_con_su_huella():
    """El de Android llevaba la version evergreen (155) con la huella de 131."""
    assert perfiles.incoherencia_ua_huella(perfiles.UA_CHROME_ANDROID,
                                           perfiles.IMPERSONATE_MOVIL) is None
    assert perfiles.incoherencia_ua_huella(perfiles.UA_CHROME_ESCRITORIO,
                                           perfiles.IMPERSONATE_POR_DEFECTO) is None
    assert perfiles.es_ua_movil(perfiles.UA_CHROME_ANDROID)


@pytest.mark.parametrize("perfil", ["chrome", "chrome999", "Chrome150 ", "", "safari"])
def test_una_huella_que_curl_cffi_no_tiene_es_un_422(perfil):
    JobConfig = _jobconfig()
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="huella TLS desconocido"):
        JobConfig(impersonate=perfil)


def test_un_config_viejo_con_el_ua_de_124_sigue_saliendo_con_la_huella_de_124():
    JobConfig = _jobconfig()
    """Los `projects/*/config.json` y los clientes que fijaron el UA a mano."""
    cfg = JobConfig(user_agent=UA_124)
    assert cfg.impersonate == "chrome124"
    # deducida, no fijada: al fusionar capas no pisa nada
    assert "impersonate" not in cfg.model_fields_set


def test_si_solo_se_fija_la_huella_el_ua_es_el_de_esa_huella():
    JobConfig = _jobconfig()
    cfg = JobConfig(impersonate="firefox147")
    assert "Firefox/147.0" in cfg.user_agent
    assert JobConfig(impersonate="chrome124").user_agent == UA_124


def test_un_ua_de_firefox_sin_huella_no_sale_con_la_de_chrome():
    JobConfig = _jobconfig()
    assert JobConfig(user_agent=UA_FIREFOX).impersonate == "firefox147"


def test_un_bot_no_cambia_la_huella():
    JobConfig = _jobconfig()
    cfg = JobConfig(user_agent=perfiles.UA_GOOGLEBOT_SMARTPHONE)
    assert cfg.impersonate == perfiles.IMPERSONATE_POR_DEFECTO


def test_si_se_fijan_los_dos_mandan_los_dos():
    JobConfig = _jobconfig()
    cfg = JobConfig(user_agent=UA_124, impersonate="chrome150")
    assert (cfg.user_agent, cfg.impersonate) == (UA_124, "chrome150")
    assert "124" in perfiles.incoherencia_ua_huella(cfg.user_agent, cfg.impersonate)


def test_la_herencia_deduce_sobre_la_config_fusionada():
    _jobconfig()
    pytest.importorskip("fastapi")
    from api.routers.clientes import resolver_config

    cliente = SimpleNamespace(config_defecto={"user_agent": UA_124})
    perfil = SimpleNamespace(config={"max_depth": 2})
    cfg = resolver_config(cliente, perfil, {})
    assert (cfg["user_agent"], cfg["impersonate"]) == (UA_124, "chrome124")
    # el job que pide otra huella explicita la tiene
    cfg = resolver_config(cliente, perfil, {"impersonate": "chrome150"})
    assert cfg["impersonate"] == "chrome150"


@pytest.mark.parametrize("ua,plataforma", [
    (perfiles.UA_POR_DEFECTO, '"Windows"'),
    (perfiles.UA_POR_DEFECTO.replace("Windows NT 10.0; Win64; x64", "X11; Linux x86_64"), '"Linux"'),
    (perfiles.UA_CHROME_ANDROID, '"Android"'),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/150.0.0.0 Safari/537.36", '"macOS"'),
])
def test_la_plataforma_de_las_client_hints_es_la_del_ua(ua, plataforma):
    assert perfiles.cabeceras_de_huella("chrome150", ua)["Sec-Ch-Ua-Platform"] == plataforma


def test_firefox_no_manda_client_hints():
    assert "Sec-Ch-Ua-Platform" not in perfiles.cabeceras_de_huella("firefox147", UA_FIREFOX)


def test_el_preset_chrome_del_formulario_es_el_ua_de_serie():
    app = (RAIZ / "frontend" / "app.js").read_text(encoding="utf-8")
    preset = re.search(r"^\s*chrome_win:\s*'([^']+)'", app, re.M).group(1)
    assert preset == perfiles.UA_POR_DEFECTO
    for valor in re.findall(r'<option value="([^"]*)"', (RAIZ / "frontend" / "index.html")
                            .read_text(encoding="utf-8").split('x-model="createForm.impersonate"')[1]
                            .split("</select>")[0]):
        assert valor == "" or valor in perfiles.PERFILES_IMPERSONATE, valor
