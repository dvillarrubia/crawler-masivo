"""El tema oscuro (`frontend/theme-terminal.css`) no deja huecos blancos.

`style.css` es el tema claro y el oscuro lo sobrescribe regla a regla. Un
fondo escrito a mano en `style.css` (`background:#fff`) que el tema oscuro no
sobrescribe sale como un recuadro blanco con el texto claro del tema encima,
ilegible: le paso a los segmentos de cada incidencia en Problemas
(`.chip-tramo`), a los ambitos (`.chip-ambito`) y al filtro de problema del
Explorador (`.exp-problema`). Lo correcto es usar las variables
(`var(--bg-card)`), que cada tema define.

Se ejecuta en Chromium con los dos CSS cargados y `data-theme="terminal"`:
por cada regla de `style.css` con un fondo claro literal se construye un
elemento que la cumple y se mide el contraste entre el fondo que pinta de
verdad y su letra (minimo 3:1): un fondo claro con letra oscura, como la
pestana activa en verde, es a proposito. Las que solo
aplican con el raton (`:hover`, `:active`...) no se pueden provocar asi; para
esas basta con que el tema oscuro tenga una regla con el mismo selector.

Necesita Chromium: se salta si no esta; corre en el contenedor del crawler y
en CI (el trabajo del crawler lo instala).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("playwright")

RAIZ = Path(__file__).resolve().parent.parent
CLARO = (RAIZ / "frontend" / "style.css").read_text(encoding="utf-8")
OSCURO = (RAIZ / "frontend" / "theme-terminal.css").read_text(encoding="utf-8")

_JS = r"""
() => {
  const PREFIJO = 'html[data-theme="terminal"]';
  const [claro, oscuro] = [document.getElementById('claro').sheet, document.getElementById('oscuro').sheet];

  // Luminancia relativa de WCAG (con la curva de sRGB) y alfa.
  function rgba(v) {
    const m = /rgba?\(([^)]*)\)/.exec(v || '');
    if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    const lin = x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
    return {lum: 0.2126 * lin(p[0]) + 0.7152 * lin(p[1]) + 0.0722 * lin(p[2]), alfa: p.length > 3 ? p[3] : 1};
  }
  const claroOpaco = c => c && c.alfa > 0.35 && c.lum > 0.5;
  const contraste = (a, b) => (Math.max(a.lum, b.lum) + 0.05) / (Math.min(a.lum, b.lum) + 0.05);

  // Reglas de estilo, entrando en @media salvo print (impreso, el informe va en blanco a proposito).
  function reglas(hoja) {
    const out = [];
    (function rec(lista) {
      for (const r of lista) {
        if (r.selectorText !== undefined) out.push(r);
        else if (r.cssRules && !(r.media && /print/.test(r.media.mediaText))) rec(r.cssRules);
      }
    })(hoja.cssRules);
    return out;
  }

  const delOscuro = new Set();
  for (const r of reglas(oscuro)) {
    if (!r.style.background && !r.style.backgroundColor) continue;
    for (const s of r.selectorText.split(',')) delOscuro.add(s.trim().replace(PREFIJO, '').trim());
  }

  // Construye ancestros y elemento para un selector de compuestos simples
  // (etiqueta, clases, id, [atributo="valor"]); null si no sabe.
  const COMPUESTO = /^([a-z][a-z0-9]*)?((?:\.[\w-]+|#[\w-]+|\[[\w-]+(?:="[^"]*")?\])*)$/i;
  function construir(selector) {
    const partes = selector.replace(/\s*>\s*/g, ' ').trim().split(/\s+/);
    let padre = document.getElementById('lienzo'), el = null;
    for (const parte of partes) {
      const m = COMPUESTO.exec(parte);
      if (!m || !parte) return null;
      el = document.createElement(m[1] || 'div');
      for (const t of m[2].match(/\.[\w-]+|#[\w-]+|\[[^\]]+\]/g) || []) {
        if (t[0] === '.') el.classList.add(t.slice(1));
        else if (t[0] === '#') el.id = t.slice(1);
        else { const a = /\[([\w-]+)(?:="([^"]*)")?\]/.exec(t); el.setAttribute(a[1], a[2] || ''); }
      }
      padre.appendChild(el); padre = el;
    }
    return el;
  }

  const fallos = [];
  for (const r of reglas(claro)) {
    const fondo = r.style.backgroundColor || '';
    if (!claroOpaco(rgba(fondo))) continue;
    for (let s of r.selectorText.split(',')) {
      s = s.trim();
      if (s === 'body' || s === 'html' || s.includes('::')) continue;
      const el = construir(s);
      if (el) {
        // Un fondo claro vale si la letra es oscura (la pestana activa, verde
        // con tinta oscura); lo que no vale es el blanco con la letra del tema.
        const cs = getComputedStyle(el), bg = rgba(cs.backgroundColor), tinta = rgba(cs.color);
        if (claroOpaco(bg) && contraste(bg, tinta) < 3)
          fallos.push(s + ' pinta ' + cs.backgroundColor + ' con letra ' + cs.color);
      } else if (!delOscuro.has(s)) {
        fallos.push(s + ' (' + fondo + ') sin regla en el tema oscuro');
      }
      document.getElementById('lienzo').replaceChildren();
    }
  }
  return fallos;
}
"""


async def _fallos() -> list[str]:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        try:
            nav = await pw.chromium.launch()
        except Exception as e:  # pragma: no cover - sin Chromium instalado
            pytest.skip(f"Chromium no disponible: {e}")
        pagina = await nav.new_page()
        await pagina.set_content(
            '<!doctype html><html data-theme="terminal"><head>'
            f"<style id=claro>{CLARO}</style><style id=oscuro>{OSCURO}</style>"
            "</head><body><div id=lienzo></div></body></html>"
        )
        fallos = await pagina.evaluate(_JS)
        await nav.close()
        return fallos


def test_ningun_fondo_claro_en_el_tema_oscuro():
    fallos = asyncio.run(_fallos())
    assert fallos == [], "fondos claros en el tema oscuro:\n" + json.dumps(fallos, indent=1, ensure_ascii=False)


def test_el_test_detecta_un_fondo_blanco_sin_sobrescribir():
    """Control: con el fallo de `.chip-tramo` devuelto, el test lo ve."""
    global CLARO
    original = CLARO
    CLARO = original + "\n.chip-prueba{background:#fff}\n"
    try:
        fallos = asyncio.run(_fallos())
    finally:
        CLARO = original
    assert any(f.startswith(".chip-prueba pinta") for f in fallos), fallos
