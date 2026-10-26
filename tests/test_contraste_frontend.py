"""La escala de color de la interfaz pasa 4,5:1 en los dos temas (revision de
UX del 25-oct, 3.1), pintando `frontend/style.css` y `theme-terminal.css` en
Chromium y midiendo el color que de verdad resulta.

Antes el verde de marca #5BAE23 era a la vez enlace, boton principal y «bien»
con 2,79:1 sobre blanco, `--text-dim` daba 4,28:1 sobre el fondo y el naranja
de los avisos 2,85:1. Aqui cada pareja de texto y fondo se compone como la ve
el navegador (los fondos con transparencia, sobre su tarjeta) y se exige
4,5:1, el minimo de WCAG para texto normal.

Ademas, el significado: «3 alertas» no sale del verde de la pestana activa,
un rastreo cancelado es neutro y no del naranja de un aviso.

Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("playwright")

RAIZ = Path(__file__).resolve().parent.parent
CSS = "".join((RAIZ / "frontend" / n).read_text(encoding="utf-8") for n in ("style.css", "theme-terminal.css"))

# Cada muestra: (nombre, marcado). El texto a medir es el elemento con id="m".
MUESTRAS = [
    ("enlace sobre el fondo", '<a id="m" href="#">enlace</a>'),
    ("enlace en una tarjeta", '<div class="card"><a id="m" href="#">enlace</a></div>'),
    ("boton principal", '<button id="m" class="btn btn-primary">Nuevo rastreo</button>'),
    ("texto gris sobre el fondo", '<span id="m" class="text-dim">gris</span>'),
    ("texto gris en una tarjeta", '<div class="card"><span id="m" class="text-dim">gris</span></div>'),
    ("cabecera de tabla", '<div class="card"><table><thead><tr><th id="m">fecha</th></tr></thead></table></div>'),
    ("pestana", '<div class="tabs"><button id="m" class="tab">Historial</button></div>'),
    ("pestana activa", '<div class="tabs"><button id="m" class="tab active">Historial</button></div>'),
    ("alertas en la pestana activa", '<div class="tabs"><button class="tab active">Alertas <span id="m" class="count count-aviso">3</span></button></div>'),
    ("alertas criticas en la pestana", '<div class="tabs"><button class="tab">Alertas <span id="m" class="count count-critica">3</span></button></div>'),
    ("alertas criticas en la pestana activa", '<div class="tabs"><button class="tab active">Alertas <span id="m" class="count count-critica">3</span></button></div>'),
    ("alertas de aviso en la pestana", '<div class="tabs"><button class="tab">Alertas <span id="m" class="count count-aviso">3</span></button></div>'),
    ("alertas solo informativas en la pestana", '<div class="tabs"><button class="tab">Alertas <span id="m" class="count count-info">3</span></button></div>'),
    ("info", '<div class="card"><span id="m" class="severity-info">info</span></div>'),
    ("error", '<div class="card"><span id="m" class="text-red">85</span></div>'),
    ("aviso", '<div class="card"><span id="m" class="text-orange">3 alertas</span></div>'),
    ("bien", '<div class="card"><span id="m" class="text-green">sin alertas</span></div>'),
    ("cifra de aviso", '<div class="stat-card"><div id="m" class="stat-value orange">3</div></div>'),
    ("delta que empeora", '<div class="card"><span id="m" class="delta peor">▲ 6</span></div>'),
    ("delta que mejora", '<div class="card"><span id="m" class="delta mejor">▼ 4</span></div>'),
    ("4xx del Explorador", '<div class="card"><span id="m" class="exp-status exp-status-4xx">404</span></div>'),
] + [
    (f"insignia {c}", f'<div class="card"><span id="m" class="badge {c}">estado</span></div>')
    for c in ("badge-pending", "badge-running", "badge-completed", "badge-failed",
              "badge-cancelled", "badge-info", "badge-neutral")
]

MEDIR = """(tema) => {
  document.documentElement.dataset.theme = tema;
  const rgba = s => { const n = s.match(/[\\d.]+/g).map(Number); return [n[0], n[1], n[2], n.length > 3 ? n[3] : 1]; };
  const sobre = (a, b) => [0, 1, 2].map(i => a[i] * a[3] + b[i] * (1 - a[3])).concat(1);
  // El fondo que se ve detras de un elemento: sus fondos y los de sus padres compuestos.
  const fondo = el => {
    const capas = [];
    for (let e = el; e; e = e.parentElement) capas.push(rgba(getComputedStyle(e).backgroundColor));
    return capas.reverse().reduce((acc, c) => sobre(c, acc), [255, 255, 255, 1]);
  };
  const lum = c => { const f = v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; };
                     return .2126 * f(c[0]) + .7152 * f(c[1]) + .0722 * f(c[2]); };
  const out = {};
  for (const [nombre, html] of MUESTRAS) {
    document.body.innerHTML = '<div class="main">' + html + '</div>';
    const el = document.getElementById('m');
    const bg = fondo(el), fg = sobre(rgba(getComputedStyle(el).color), bg);
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
    out[nombre] = { ratio: Math.round((a + .05) / (b + .05) * 100) / 100, color: getComputedStyle(el).color };
  }
  return out;
}"""


def _medir(tema: str) -> dict:
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"sin Chromium: {exc}")
            pagina = await nav.new_page()
            await pagina.set_content("<html><head><style>" + CSS + "</style></head><body></body></html>")
            await pagina.evaluate("m => { window.MUESTRAS = m; }", MUESTRAS)
            salida = await pagina.evaluate(MEDIR, tema)
            await nav.close()
            return salida

    return asyncio.run(run())


@pytest.mark.parametrize("tema", ["light", "terminal"])
def test_todo_el_texto_clave_pasa_4_5(tema):
    r = _medir(tema)
    flojos = {k: v["ratio"] for k, v in r.items() if v["ratio"] < 4.5}
    assert not flojos, f"tema {tema}: por debajo de 4,5:1 {flojos}"


@pytest.mark.parametrize("tema", ["light", "terminal"])
def test_las_alertas_no_salen_del_verde_y_cancelado_es_neutro(tema):
    # En los dos temas: en el terminal unas reglas mas especificas de
    # `.tab .count` dejaban el numero en verde (hallazgo de la revision).
    r = _medir(tema)
    verdes = (_medir_color_pestana_activa(tema), _medir_color_pestana(tema), r["bien"]["color"])
    for k in ("alertas en la pestana activa", "alertas de aviso en la pestana",
              "alertas criticas en la pestana", "alertas criticas en la pestana activa",
              "alertas solo informativas en la pestana"):
        assert r[k]["color"] not in verdes, f"tema {tema}: {k} sale verde ({r[k]['color']})"
    assert r["alertas en la pestana activa"]["color"] == r["aviso"]["color"]
    assert r["alertas de aviso en la pestana"]["color"] == r["aviso"]["color"]
    assert r["alertas criticas en la pestana"]["color"] == r["error"]["color"]
    assert r["alertas criticas en la pestana activa"]["color"] == r["error"]["color"]
    # Si todas son informativas, azul de info y no naranja de aviso.
    assert r["alertas solo informativas en la pestana"]["color"] == r["info"]["color"]
    # Cancelado en gris: ni el naranja de los avisos ni el verde de «bien».
    # (En el terminal el neutro es su gris verdoso propio, --neutral, no el --text-dim.)
    if tema == "light":
        assert r["insignia badge-neutral"]["color"] == r["texto gris en una tarjeta"]["color"]
    assert r["insignia badge-neutral"]["color"] not in (r["aviso"]["color"], r["bien"]["color"], r["error"]["color"])
    # El enlace ya no es el verde de marca.
    assert r["enlace sobre el fondo"]["color"] != "rgb(91, 174, 35)"


def _medir_color_pestana_activa(tema: str = "light") -> str:
    return _medir_color_pestana(tema, activa=True)


def _medir_color_pestana(tema: str = "light", activa: bool = False) -> str:
    """Color del contador normal (no de alertas) de una pestana."""
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"sin Chromium: {exc}")
            pagina = await nav.new_page()
            await pagina.set_content(f'<html data-theme="{tema}"><head><style>' + CSS + "</style></head><body>"
                                     f'<div class="tabs"><button class="tab{" active" if activa else ""}">Problemas '
                                     '<span id="m" class="count">12</span></button></div></body></html>')
            c = await pagina.evaluate("getComputedStyle(document.getElementById('m')).color")
            await nav.close()
            return c

    return asyncio.run(run())


def test_el_estado_cancelado_usa_la_insignia_neutra():
    from playwright.async_api import async_playwright

    fuentes = [(RAIZ / "frontend" / n).read_text(encoding="utf-8") for n in ("incidencias.js", "app.js")]

    async def run():
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"sin Chromium: {exc}")
            pagina = await nav.new_page()
            await pagina.set_content("<html><body></body></html>")
            await pagina.add_script_tag(content="window.lucide = { createIcons() {} };")
            for f in fuentes:
                await pagina.add_script_tag(content=f)
            salida = await pagina.evaluate(
                "() => { const a = app(); return ['running', 'completed', 'failed', 'cancelled', 'pending']"
                ".map(s => a.statusColor(s)); }")
            await nav.close()
            return salida

    assert asyncio.run(run()) == ["badge-running", "badge-completed", "badge-failed", "badge-neutral", "badge-pending"]
