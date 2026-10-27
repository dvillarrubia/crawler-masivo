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


def _verificador(rangos=None, completas=True):
    rangos = RANGOS if rangos is None else rangos
    return L.Verificador(rangos=lambda p: (rangos.get(p, []), completas))


def test_bot_de_mira_lo_especifico_antes():
    assert L.bot_de(GIMG) == ("googlebot-imagen", "google")
    assert L.bot_de(GBOT) == ("googlebot", "google") and L.es_movil(GBOT) and not L.es_movil(GBOT_PC)
    assert L.bot_de("Mozilla/5.0 (compatible; GPTBot/1.2; +https://openai.com/gptbot)") == ("gptbot", "openai")
    assert L.bot_de(PERSONA) is None and L.bot_de(None) is None


def test_agregar_lineas_bots_y_errores():
    a = L.agregar(L.lineas(io.BytesIO(LOG.encode())), L.regex_de("combined"), verificar=_verificador())
    assert (a.lineas_ok, a.lineas_error, a.personas, a.otros_bots) == (7, 1, 1, 1)
    assert a.errores == ["esto no es una linea de log"]
    d = a.por_ruta[("/es/hoteles/", "googlebot", True)]
    assert d["visitas"] == 2 and d["codigos"] == {"200": 2} and d["movil"] == 2
    assert a.por_ruta[("/es/hoteles/", "googlebot", False)]["visitas"] == 1, "1.2.3.4 no es de Google"
    assert ("/es/viejo?utm=x", "googlebot", True) in a.por_ruta
    assert a.desde == datetime(2026, 10, 20, 8, tzinfo=timezone.utc)


def test_lee_un_gz_por_los_bytes():
    a = L.agregar(L.lineas(io.BytesIO(gzip.compress(LOG.encode()))), L.regex_de("combined"),
                  verificar=_verificador())
    assert a.lineas_ok == 7


def test_una_regex_propia_sin_sus_grupos_se_rechaza():
    with pytest.raises(L.FormatoNoValido):
        L.regex_de("propio", r"(?P<ip>\S+) (?P<url>\S+)")
    with pytest.raises(L.FormatoNoValido):
        L.regex_de("propio", r"(?P<ip>")
    r = L.regex_de("propio", r"(?P<ip>\S+)\t(?P<url>\S+)\t(?P<codigo>\d{3})\t(?P<ua>.*)")
    a = L.agregar(["66.249.66.1\t/x\t200\t" + GBOT], r, verificar=_verificador())
    assert a.lineas_ok == 1 and ("/x", "googlebot", True) in a.por_ruta
    with pytest.raises(L.FormatoNoValido):
        L.regex_de("common"), "common no lleva user-agent: no hay bots que ver"


def test_la_ruta_de_una_url_entera_y_la_clave_sin_minusculas():
    assert L.ruta_de("https://www.x.com/A/b?c=1#f") == "/A/b?c=1"
    assert L.ruta_de("http://otro.com/x", "www.x.com") is None, "una peticion de proxy a otro host no es de este log"
    assert L.ruta_de("https://x.com/y", "www.x.com") == "/y"
    assert L.clave_log("www.x.com", "/A/b/") == "x.com/A/b"


def test_verificar_por_rangos_y_lo_que_no_se_puede_comprobar():
    v = _verificador()
    assert v("66.249.66.1", "google") is True and v("1.2.3.4", "google") is False
    assert v("::ffff:66.249.66.1", "google") is True, "IPv4 mapeada en IPv6"
    assert v("66.249.66.1:443", "google") is None, "ip:puerto no se lee: no se da por falsa"
    assert v("10.0.0.1, 66.249.66.1", "google") is None
    assert v("5.5.5.5", "anthropic") is None, "no publica rangos"
    parcial = _verificador(completas=False)
    assert parcial("1.2.3.4", "google") is None, "con un fichero sin bajar, lo que no casa no es falso"
    assert parcial("66.249.66.1", "google") is True


def test_las_lineas_de_error_se_guardan_con_las_ips_tapadas():
    a = L.agregar(['88.1.2.3 - - roto "GET /x" que no casa 2001:db8::1'], L.regex_de("combined"),
                  verificar=_verificador())
    assert a.errores == ['x.x.x.x - - roto "GET /x" que no casa x:x']


def test_comillas_escapadas_en_el_user_agent():
    linea = (f'66.249.66.1 - - [20/Oct/2026:10:00:00 +0200] "GET /a HTTP/1.1" 200 1 '
             f'"ref \\"raro\\"" "{GBOT_PC}"')
    a = L.agregar([linea], L.regex_de("combined"), verificar=_verificador())
    assert ("/a", "googlebot", True) in a.por_ruta, "un referer con comillas no tira el user-agent"


def test_el_tope_de_rutas_cuenta_lo_que_no_cabe():
    lineas = [f'66.249.66.1 - - [20/Oct/2026:10:00:00 +0200] "GET /p{i} HTTP/1.1" 200 1 "-" "{GBOT}"'
              for i in range(10)]
    a = L.agregar(lineas, L.regex_de("combined"), verificar=_verificador(), max_claves=4)
    assert len(a.por_ruta) == 4 and a.fuera_del_tope == 6


def test_las_filas_separan_lo_verificado_de_lo_falso():
    a = L.agregar(L.lineas(io.BytesIO(LOG.encode())), L.regex_de("combined"), verificar=_verificador())
    filas = {(f["ruta"], f["bot"], f["verificado"]): f for f in L.filas_por_url(a, "www.x.com")}
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
    assert L.procesar(iid, sesion_factory=S, verificador=_verificador()) == "hecho"
    assert L.procesar(iid, sesion_factory=S, verificador=_verificador()) is None, "no se lee dos veces"
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
    assert L.procesar(iid, sesion_factory=S, verificador=_verificador()) == "error"
    assert not f.exists(), "tambien con error se borra el fichero"
    with S() as s:
        imp = s.get(LogImport, iid)
        assert "Ninguna línea casa" in imp.motivo and imp.errores == ["formato raro 1", "formato raro 2"]


def test_la_recuperacion_no_toca_lo_que_esta_latiendo(tmp_path):
    from datetime import timedelta

    from shared.models import Base, Client, LogImport

    engine = create_engine(f"sqlite:///{tmp_path}/l.db")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    f = tmp_path / "a.log"
    f.write_text("x")
    ahora = datetime.now(timezone.utc)
    with S() as s:
        c = Client(id=uuid.uuid4(), nombre="X", slug="x", dominios=[], config_defecto={}, integraciones=[])
        s.add(c)
        s.flush()
        vivo = LogImport(cliente_id=c.id, ruta_disco=str(f), host="x.com", estado="procesando", latido_at=ahora)
        cortado = LogImport(cliente_id=c.id, ruta_disco=str(f), host="x.com", estado="procesando",
                            latido_at=ahora - timedelta(hours=1))
        sin_fichero = LogImport(cliente_id=c.id, ruta_disco=str(tmp_path / "no"), host="x.com",
                                estado="procesando", latido_at=ahora - timedelta(hours=1))
        s.add_all([vivo, cortado, sin_fichero])
        s.commit()
        ids = L.recuperar(s)
        assert ids == [cortado.id]
        assert s.get(LogImport, vivo.id).estado == "procesando"
        assert s.get(LogImport, sin_fichero.id).estado == "error"
