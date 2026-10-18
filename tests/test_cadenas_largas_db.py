"""Cadenas de redireccion: una fila por cadena, y error pasados 10 saltos
(decision 117). EJECUTA `analyze_redirect_chains` contra SQLite (decision 74).

Una cadena de 12 saltos daba 11 `redirect_chain` (una por cada origen
intermedio), todas `warning`, cuando Google deja de seguirla a los 10.
"""

from __future__ import annotations

from sqlalchemy import select

from analysis.analyzer import LIMITE_SALTOS_GOOGLE, SEOAnalyzer
from shared.models import Issue
from test_canonicals_y_cadenas_db import _montar, _salto, _url


def _cadena(s, j, n: int, *, final_status: int = 200, prefijo: str = "/p"):
    """n redirecciones seguidas: /p0 → /p1 → ... → /pn, y /pn responde."""
    saltos = [_salto(s, j, f"{prefijo}{i}", f"{prefijo}{i + 1}") for i in range(n)]
    _url(s, j, f"{prefijo}{n}", status=final_status, sin_meta=final_status != 200)
    return saltos


def _avisos(s, j):
    SEOAnalyzer(s, j.id).analyze_redirect_chains()
    s.flush()
    return s.execute(
        select(Issue.url_id, Issue.issue_type, Issue.severity, Issue.details)
        .where(Issue.job_id == j.id)).all()


def test_el_limite_es_diez():
    assert LIMITE_SALTOS_GOOGLE == 10


def test_doce_saltos_son_un_aviso_de_error():
    s, j = _montar()
    saltos = _cadena(s, j, 12)
    avisos = _avisos(s, j)
    assert [(a.url_id, a.issue_type, a.severity) for a in avisos] == [
        (saltos[0].id, "redirect_chain", "error")]
    d = avisos[0].details
    assert d["hops"] == 12 and d["supera_limite_google"] is True
    assert d["final_status"] == 200
    # Las 11 intermedias tambien son cadenas (de 11 a 2 saltos): van aqui.
    assert d["paginas_afectadas"] == 11
    assert d["intermedias"][0] == "https://x.com/p1"


def test_diez_saltos_es_warning_y_once_error():
    s, j = _montar()
    _cadena(s, j, 10, prefijo="/d")
    _cadena(s, j, 11, prefijo="/o")
    avisos = {a.details["chain"][0]: a for a in _avisos(s, j)}
    assert set(avisos) == {"https://x.com/d0", "https://x.com/o0"}
    diez, once = avisos["https://x.com/d0"], avisos["https://x.com/o0"]
    assert (diez.severity, "supera_limite_google" in diez.details) == ("warning", False)
    assert (once.severity, once.details["supera_limite_google"]) == ("error", True)


def test_doce_saltos_a_un_404_manda_el_limite():
    """Google ni llega al 404: es el limite, con `final_status`, y no tambien
    `redireccion_a_error` en el mismo origen."""
    s, j = _montar()
    saltos = _cadena(s, j, 12, final_status=404)
    avisos = _avisos(s, j)
    del_origen = [a for a in avisos if a.url_id == saltos[0].id]
    assert [(a.issue_type, a.severity) for a in del_origen] == [("redirect_chain", "error")]
    assert del_origen[0].details["final_status"] == 404
    # La de 11 va dentro del aviso; las de 10 o menos siguen siendo, cada una,
    # una redireccion que lleva a un error (decision 101).
    assert {a.url_id for a in avisos if a.issue_type == "redirect_chain"} == {saltos[0].id}
    assert sorted(a.details["hops"] for a in avisos
                  if a.issue_type == "redireccion_a_error") == list(range(1, 11))


def test_dos_cadenas_que_se_juntan_son_dos_avisos():
    s, j = _montar()
    a = _salto(s, j, "/a", "/c")
    b = _salto(s, j, "/b", "/c")
    _salto(s, j, "/c", "/d")
    _salto(s, j, "/d", "/e")
    _url(s, j, "/e")
    avisos = [x for x in _avisos(s, j) if x.issue_type == "redirect_chain"]
    assert sorted(x.url_id for x in avisos) == sorted([a.id, b.id])
    assert all(x.details["paginas_afectadas"] == 2 for x in avisos)  # y /c → /d → /e


def test_una_cadena_de_dos_sigue_igual():
    s, j = _montar()
    a = _salto(s, j, "/a", "/b")
    _salto(s, j, "/b", "/c")
    _url(s, j, "/c")
    [aviso] = _avisos(s, j)
    assert (aviso.url_id, aviso.issue_type, aviso.severity) == (a.id, "redirect_chain", "warning")
    assert aviso.details["paginas_afectadas"] == 1


def test_si_el_anterior_es_externo_la_cadena_empieza_en_la_interna():
    s, j = _montar()
    _salto(s, j, "https://otro.com/x", "https://x.com/a", interna=False)
    a = _cadena(s, j, 3, prefijo="/a")[0]
    assert [x.url_id for x in _avisos(s, j)] == [a.id]


def test_cortada_por_el_rastreo_no_afirma_el_final():
    """El spider deja de seguir a los 20 saltos: la ultima no se rastreo."""
    s, j = _montar()
    saltos = [_salto(s, j, f"/c{i}", f"/c{i + 1}") for i in range(21)]
    [aviso] = _avisos(s, j)
    assert aviso.url_id == saltos[0].id
    assert aviso.details["final_status"] is None and aviso.severity == "error"
