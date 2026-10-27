"""Logs del servidor (#35, decision 213): lectura en streaming, bots,
verificacion de IPs y el procesado de una importacion de punta a punta
(SQLite y un fichero temporal; sin red: los rangos y el DNS se simulan)."""

from __future__ import annotations

import gzip
import io
import ipaddress
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared import logs_servidor as L

GBOT = "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/W.X.Y.Z Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
GBOT_PC = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
GIMG = "Googlebot-Image/1.0"
PERSONA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15"
LOG = "\n".join([
    f'66.249.66.1 - - [20/Oct/2026:10:00:00 +0200] "GET /es/hoteles/ HTTP/1.1" 200 5123 "-" "{GBOT}"',
    f'66.249.66.1 - - [21/Oct/2026:11:00:00 +0200] "GET /es/hoteles/ HTTP/1.1" 200 5123 "-" "{GBOT}"',
    f'66.249.66.2 - - [22/Oct/2026:12:00:00 +0200] "GET /es/viejo?utm=x HTTP/1.1" 404 120 "-" "{GBOT_PC}"',
    f'1.2.3.4 - - [22/Oct/2026:12:30:00 +0200] "GET /es/hoteles/ HTTP/1.1" 200 5123 "-" "{GBOT}"',
    f'66.249.66.3 - - [23/Oct/2026:09:00:00 +0200] "GET /img/a.jpg HTTP/1.1" 200 99 "-" "{GIMG}"',
    f'88.1.1.1 - - [23/Oct/2026:09:01:00 +0200] "GET /es/hoteles/ HTTP/2.0" 200 5123 "https://google.com/" "{PERSONA}"',
    f'66.249.66.1 - - [24/Oct/2026:10:00:00 +0200] "POST /api/x HTTP/1.1" 200 1 "-" "{GBOT}"',
    'esto no es una linea de log',
    '',
])
RANGOS = {"google": [ipaddress.ip_network("66.249.64.0/19")]}


def test_bot_de_mira_lo_especifico_antes():
    assert L.bot_de(GIMG) == ("googlebot-imagen", "google")
    assert L.bot_de(GBOT) == ("googlebot", "google") and L.es_movil(GBOT) and not L.es_movil(GBOT_PC)
    assert L.bot_de("Mozilla/5.0 (compatible; GPTBot/1.2; +https://openai.com/gptbot)") == ("gptbot", "openai")
    assert L.bot_de(PERSONA) is None and L.bot_de(None) is None


def test_agregar_lineas_bots_y_errores():
    a = L.agregar(L.lineas(io.BytesIO(LOG.encode())), L.regex_de("combined"))
    assert (a.lineas_ok, a.lineas_error, a.personas, a.otros_bots) == (7, 1, 1, 1)
    assert a.errores == ["esto no es una linea de log"]
    d = a.por_ruta[("/es/hoteles/", "googlebot")]
    assert d["ips"] == {"66.249.66.1": 2, "1.2.3.4": 1} and d["codigos"] == {"200": 3} and d["movil"] == 3
    assert ("/es/viejo?utm=x", "googlebot") in a.por_ruta
    assert a.desde == datetime(2026, 10, 20, 8, tzinfo=timezone.utc)
    assert a.ips_por_proveedor["google"] == {"66.249.66.1", "66.249.66.2", "1.2.3.4", "66.249.66.3"}


def test_lee_un_gz_por_los_bytes():
    a = L.agregar(L.lineas(io.BytesIO(gzip.compress(LOG.encode()))), L.regex_de("combined"))
    assert a.lineas_ok == 7


def test_una_regex_propia_sin_sus_grupos_se_rechaza():
    with pytest.raises(L.FormatoNoValido):
        L.regex_de("propio", r"(?P<ip>\S+) (?P<url>\S+)")
    with pytest.raises(L.FormatoNoValido):
        L.regex_de("propio", r"(?P<ip>")
    r = L.regex_de("propio", r"(?P<ip>\S+)\t(?P<url>\S+)\t(?P<codigo>\d{3})\t(?P<ua>.*)")
    a = L.agregar(["66.249.66.1\t/x\t200\t" + GBOT], r)
    assert a.lineas_ok == 1 and ("/x", "googlebot") in a.por_ruta


def test_la_ruta_de_una_url_entera_y_la_clave_sin_minusculas():
    assert L.ruta_de("https://www.x.com/A/b?c=1#f") == "/A/b?c=1"
    assert L.clave_log("www.x.com", "/A/b/") == "x.com/A/b"


def test_verificar_por_rangos_por_dns_y_lo_que_no_se_puede():
    v = L.verificar({"google": {"66.249.66.1", "1.2.3.4"}, "anthropic": {"5.5.5.5"}},
                    rangos=lambda p: RANGOS.get(p), dns=lambda ip, d: pytest.fail("con rangos no hay DNS"))
    assert v == {"66.249.66.1": True, "1.2.3.4": False, "5.5.5.5": None}
    v = L.verificar({"bing": {"40.77.1.1", "9.9.9.9"}}, rangos=lambda p: None,
                    dns=lambda ip, d: ip.startswith("40.77") and "search.msn.com" in d)
    assert v == {"40.77.1.1": True, "9.9.9.9": False}


def test_las_filas_separan_lo_verificado_de_lo_falso():
    a = L.agregar(L.lineas(io.BytesIO(LOG.encode())), L.regex_de("combined"))
    v = L.verificar(a.ips_por_proveedor, rangos=lambda p: RANGOS.get(p))
    filas = {(f["ruta"], f["bot"], f["verificado"]): f for f in L.filas_por_url(a, v, "www.x.com")}
    assert filas[("/es/hoteles/", "googlebot", True)]["visitas"] == 2
    assert filas[("/es/hoteles/", "googlebot", False)]["visitas"] == 1
    assert filas[("/es/hoteles/", "googlebot", True)]["clave"] == "x.com/es/hoteles"


@compiles(BigInteger, "sqlite")
def _bigint(tipo, compilador, **kw):
    return "INTEGER"


def test_procesar_de_punta_a_punta_y_el_fichero_se_borra(tmp_path):
    from shared.models import Base, Client, LogImport, LogUrl

    engine = create_engine(f"sqlite:///{tmp_path}/l.db")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    f = tmp_path / "access.log.gz"
    f.write_bytes(gzip.compress(LOG.encode()))
    with S() as s:
        c = Client(id=uuid.uuid4(), nombre="X", slug="x", dominios=["www.x.com"], config_defecto={}, integraciones=[])
        s.add(c)
        s.flush()
        imp = LogImport(cliente_id=c.id, fichero="access.log.gz", ruta_disco=str(f), formato="combined",
                        host="www.x.com", estado="pendiente")
        s.add(imp)
        s.commit()
        iid = imp.id
    rangos = lambda p: RANGOS.get(p)  # noqa: E731
    assert L.procesar(iid, sesion_factory=S, verificar_ips=lambda ips: L.verificar(ips, rangos=rangos)) == "hecho"
    assert L.procesar(iid, sesion_factory=S) is None, "ya no esta pendiente: no se lee dos veces"
    assert not f.exists(), "el fichero tiene IPs de personas: se borra"
    with S() as s:
        imp = s.get(LogImport, iid)
        assert (imp.lineas_ok, imp.lineas_error, imp.personas) == (7, 1, 1)
        assert imp.bots["googlebot"] == {"visitas": 4, "verificadas": 3, "falsas": 1, "sin_comprobar": 0}
        assert s.query(LogUrl).filter(LogUrl.import_id == iid).count() == 4


def test_un_log_que_no_casa_queda_en_error_con_sus_lineas(tmp_path):
    from shared.models import Base, Client, LogImport

    engine = create_engine(f"sqlite:///{tmp_path}/l.db")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    f = tmp_path / "raro.log"
    f.write_text("formato raro 1\nformato raro 2\n")
    with S() as s:
        c = Client(id=uuid.uuid4(), nombre="X", slug="x", dominios=[], config_defecto={}, integraciones=[])
        s.add(c)
        s.flush()
        imp = LogImport(cliente_id=c.id, ruta_disco=str(f), formato="combined", host="x.com", estado="pendiente")
        s.add(imp)
        s.commit()
        iid = imp.id
    assert L.procesar(iid, sesion_factory=S) == "error"
    with S() as s:
        imp = s.get(LogImport, iid)
        assert "Ninguna línea casa" in imp.motivo and imp.errores == ["formato raro 1", "formato raro 2"]
