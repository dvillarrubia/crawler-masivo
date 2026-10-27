"""Escala de letra y estilos en linea a clases (revision de UX del 25-oct,
3.8; V43 y UX-21), EJECUTANDO `frontend/index.html` con Alpine en Chromium y
la API de mentira de `test_movil_frontend.py`.

Lo que se sujeta:

- ningun texto visible calcula menos de 12 px, en las vistas principales, a
  1440 y a 390 px y en los dos temas (claro y terminal). Con `html` a 14px,
  `.8rem` eran 11,2 px; `code` y `pre` salian a 11,4 px por la rareza de
  Chromium con la familia `monospace` sola; y el texto de un SVG cuenta con
  la escala a la que se pinta;
- en esas mismas vistas (21, con alertas, migracion, IA, limpieza,
  puntuacion, semantico y la ficha de URL), cada utilidad con !important gana
  de verdad en el elemento que la lleva, oculto o no: `.mono` sin el perdia
  contra input[type="text"] y la cron o el canal de Slack salian en letra
  normal sin que ninguna medida de tamano lo viera;
- los titulares de markdown de h1 a h6 a 12 px o mas, y las etiquetas
  largas de «Que arreglar primero» sin cortar;
- todo `font-size` del frontend usa una variable de la escala (`--fs-*`),
  salvo la raiz de 14px, y la escala son cuatro tamanos de texto mas las dos
  excepciones declaradas (cifra e icono), ninguno por debajo de 12 px;
- los estilos en linea fijos de `index.html` bajan de la mitad de los que
  habia (947) y las clases que los sustituyen existen en `style.css`;
- un solo ayudante de porcentaje (`fmt.pct`).

Los tres ultimos tests leen el fuente: son guardas de convencion (la escala
en variables, el recuento de estilos en linea, un solo ayudante), no
sustituyen a lo que se ejecuta arriba.

Necesita Chromium y poder bajar Alpine de su CDN: si no, se salta.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

pytest.importorskip("playwright")

from test_movil_frontend import BASE, CLI, ESTUBS, FRONT, JOB, _respuesta_api  # noqa: E402

RUTAS = [
    "#/clientes", "#/rastreos", "#/programados", "#/produccion", "#/ajustes", "#/clientes/nuevo",
    f"#/clientes/{CLI}", f"#/clientes/{CLI}/historial", f"#/clientes/{CLI}/configuracion",
    f"#/rastreos/{JOB}", f"#/rastreos/{JOB}/problemas", f"#/rastreos/{JOB}/urls",
    f"#/rastreos/{JOB}/enlaces", f"#/rastreos/{JOB}/comparar",
    f"#/clientes/{CLI}/alertas", f"#/rastreos/{JOB}/migracion", f"#/rastreos/{JOB}/ia",
    f"#/rastreos/{JOB}/limpieza", f"#/rastreos/{JOB}/puntuacion", f"#/rastreos/{JOB}/semantico",
    f"#/rastreos/{JOB}/url/1",
]

# El tamano calculado de cada texto visible; en un SVG, por la escala a la
# que se pinta (un 9 en un viewBox que se encoge se lee mas pequeno).
MEDIR = """() => {
  const malos = []; let n = 0;
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    const t = w.currentNode; if (!t.textContent.trim()) continue;
    const e = t.parentElement; if (!e) continue;
    const r = e.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const cs = getComputedStyle(e);
    if (cs.visibility === 'hidden' || cs.opacity === '0') continue;
    let fs = parseFloat(cs.fontSize);
    if (e instanceof SVGElement && e.getScreenCTM && e.getScreenCTM()) {
      const m = e.getScreenCTM(); fs = fs * Math.hypot(m.a, m.b);
    }
    n++;
    if (fs < 11.95) malos.push(Math.round(fs * 10) / 10 + 'px ' + e.tagName.toLowerCase() + '.'
      + (typeof e.className === 'string' ? e.className : '') + ' «' + t.textContent.trim().slice(0, 30) + '»');
  }
  return { n, malos: malos.slice(0, 10) };
}"""


# Que cada utilidad con !important gane de verdad en el elemento que la
# lleva, tambien en los que estan ocultos (modales con x-show): sustituyen a
# un estilo en linea, y una que pierde contra una regla de componente cambia
# el aspecto sin que se vea en ninguna medida de tamano. Se saltan las
# celdas de las tablas que en movil pasan a tarjetas, que mandan a proposito. Paso con `.mono`,
# que perdia contra input[type="text"] {font-family:inherit}. Se compara el
# valor calculado con el que daria la misma declaracion puesta en linea.
GANAN = """() => {
  const quieto = document.createElement('style');
  quieto.textContent = '*{transition:none!important;animation:none!important}';
  document.head.appendChild(quieto);
  const reglas = [];
  const visitar = (lista) => { for (const r of lista) {
    if (r.cssRules && !r.selectorText) {
      if (!r.media || matchMedia(r.media.mediaText).matches) visitar(r.cssRules);
      continue;
    }
    if (!r.selectorText || !/^\\.[\\w-]+$/.test(r.selectorText)) continue;
    const props = [];
    for (let i = 0; i < r.style.length; i++) {
      const p = r.style[i];
      // .mono entra aunque alguien le quite el !important: es la que fallo.
      if (r.style.getPropertyPriority(p) === 'important' || r.selectorText === '.mono') props.push([p, r.style.getPropertyValue(p)]);
    }
    if (props.length) reglas.push([r.selectorText.slice(1), props]);
  } };
  for (const h of document.styleSheets) { try { visitar(h.cssRules) } catch (e) {} }
  const pierden = new Set(), vistas = new Set(); let n = 0;
  // En movil, la tabla que pasa a tarjetas o se apila manda a proposito:
  // alinea a la izquierda, deja partir las lineas y abre los truncados.
  const movil = innerWidth <= 720;
  for (const [clase, props] of reglas) for (const e of document.getElementsByClassName(clase)) for (const [p, v] of props) {
    if (movil && e.closest('.con-tarjetas .tabla-tarjetas, .tabla-apilable')) continue;
    n++; vistas.add(clase);
    const antes = getComputedStyle(e).getPropertyValue(p);
    const previo = e.style.getPropertyValue(p), prio = e.style.getPropertyPriority(p);
    e.style.setProperty(p, v, 'important');
    const debido = getComputedStyle(e).getPropertyValue(p);
    if (previo) e.style.setProperty(p, previo, prio); else e.style.removeProperty(p);
    if (antes !== debido) pierden.add('.' + clase + ' en ' + e.tagName.toLowerCase()
      + (e.id ? '#' + e.id : '') + ': ' + p + ' da «' + antes + '» y no «' + debido + '»');
  }
  quieto.remove();
  return { n, vistas: [...vistas], pierden: [...pierden].slice(0, 10) };
}"""

# Los titulares de un informe en markdown, de h1 a h6: h5 y h6 heredaban
# .83em y .67em del navegador (10 y 8 px sobre la base de 12).
MARKDOWN = """() => {
  const d = document.createElement('div'); d.className = 'markdown-body';
  d.innerHTML = '<h1>a</h1><h2>b</h2><h3>c</h3><h4>d</h4><h5>e</h5><h6>f</h6><p>g</p>';
  document.body.appendChild(d);
  const r = [...d.children].map(e => [e.tagName.toLowerCase(), parseFloat(getComputedStyle(e).fontSize)]);
  d.remove();
  return r;
}"""


# Las etiquetas de «Que arreglar primero» (Resumen) con los nombres largos de
# siempre: a 12 px, «Error datos estructurados» ya no cabia en una linea de
# 130 px y salia con puntos suspensivos. Ahora caben en dos.
ETIQUETAS = """() => {
  const c = document.createElement('div'); c.className = 'bar-chart'; c.style.width = Math.min(innerWidth - 32, 700) + 'px';
  const nombres = ['Error datos estructurados', 'Aviso datos estructurados', 'Canonical apunta a otra URL', 'Imagen sin texto alternativo'];
  c.innerHTML = nombres.map(t => '<div class="bar-row"><div class="bar-label">' + t
    + '</div><div class="bar-track"></div><div class="bar-count">9</div></div>').join('');
  document.body.appendChild(c);
  const cortadas = [...c.querySelectorAll('.bar-label')].filter(e => e.scrollHeight > e.clientHeight + 1 || e.scrollWidth > e.clientWidth + 1).map(e => e.textContent);
  c.remove();
  return cortadas;
}"""


def _recorrer(tema: str, ancho: int):
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"sin Chromium: {exc}")
            ctx = await nav.new_context(viewport={"width": ancho, "height": 900})
            await ctx.add_init_script(f"try {{ localStorage.setItem('seo-crawler-theme', '{tema}') }} catch (e) {{}}")
            pagina = await ctx.new_page()
            errores: list[str] = []
            pagina.on("pageerror", lambda e: errores.append(str(e)))
            alpine_ok = {"v": False}

            async def servir(route):
                url = route.request.url
                for clave, js in ESTUBS.items():
                    if clave in url and "/api/" not in url:
                        return await route.fulfill(body=js, content_type="application/javascript")
                if "fonts.g" in url:
                    return await route.fulfill(body="", content_type="text/css")
                if "alpinejs" in url:
                    try:
                        r = await route.fetch()
                        alpine_ok["v"] = r.ok
                        return await route.fulfill(response=r)
                    except Exception:  # pragma: no cover
                        return await route.abort()
                if not url.startswith(BASE):
                    return await route.abort()
                camino, _, query = url[len(BASE):].partition("?")
                camino = camino.split("#")[0]
                if camino.startswith("/api/"):
                    datos = _respuesta_api(camino[5:], query)
                    if camino == "/api/jobs" and "status=" in query:
                        # Produccion pide los rastreos en curso, analizando y en
                        # cola: la API de mentira no filtra y repetiria los mismos.
                        datos = {"items": [], "total": 0, "page": 1, "page_size": 100, "pages": 1}
                    if datos is None:
                        return await route.fulfill(status=404, body='{"detail":"no existe"}', content_type="application/json")
                    return await route.fulfill(body=json.dumps(datos), content_type="application/json")
                f = "index.html" if camino in ("/", "") else camino.lstrip("/").removeprefix("static/")
                local = FRONT / f
                if local.is_file():
                    tipo = {"html": "text/html", "js": "application/javascript", "css": "text/css"}.get(f.rsplit(".", 1)[-1], "text/plain")
                    return await route.fulfill(body=local.read_bytes(), content_type=tipo)
                return await route.fulfill(status=404, body="")

            await pagina.route("**/*", servir)
            await pagina.goto(BASE + "/")
            try:
                await pagina.wait_for_function("window.Alpine && document.querySelector('[x-data]')._x_dataStack", timeout=20000)
            except Exception:
                alpine_ok["v"] = False
            if not alpine_ok["v"]:
                await nav.close()
                pytest.skip("sin acceso a Alpine en su CDN")
            await pagina.wait_for_timeout(1000)
            salida = {"tema": await pagina.evaluate("document.documentElement.dataset.theme")}
            for h in RUTAS:
                await pagina.evaluate("h => { location.hash = h }", h)
                await pagina.wait_for_timeout(1200)
                salida[h] = await pagina.evaluate(MEDIR)
                salida[h]["ganan"] = await pagina.evaluate(GANAN)
                salida[h]["errores"] = errores[:]
                errores.clear()
            salida["markdown"] = await pagina.evaluate(MARKDOWN)
            salida["etiquetas"] = await pagina.evaluate(ETIQUETAS)
            await nav.close()
            return salida, errores

    return asyncio.run(run())


@pytest.mark.parametrize("tema,ancho", [("light", 1440), ("light", 390), ("terminal", 1440), ("terminal", 390)])
def test_ningun_texto_visible_por_debajo_de_12_px(tema, ancho):
    salida, errores = _recorrer(tema, ancho)
    assert errores == []
    assert salida["tema"] == tema
    for ruta in RUTAS:
        r = salida[ruta]
        assert r["errores"] == [], f"{ruta}: {r['errores']}"
        assert r["n"] > 5, f"{ruta}: casi no hay texto ({r['n']}), la vista no se pinto"
        assert r["malos"] == [], f"{tema} a {ancho} px, {ruta}: {r['malos']}"
        assert r["ganan"]["n"] > 20, f"{ruta}: casi no hay utilidades ({r['ganan']['n']})"
        assert r["ganan"]["pierden"] == [], f"{tema} a {ancho} px, {ruta}: {r['ganan']['pierden']}"
    # Hay campos monoespaciados en el recorrido (Ajustes, configuracion), y se miran.
    assert any("mono" in salida[r]["ganan"]["vistas"] for r in RUTAS)
    assert salida["etiquetas"] == [], salida["etiquetas"]
    md = dict(salida["markdown"])
    assert min(md.values()) >= 11.95, md
    assert md["h1"] > md["h3"] > md["p"] - 0.01, md


def _css(nombre: str) -> str:
    return (FRONT / nombre).read_text(encoding="utf-8")


def test_todo_font_size_usa_la_escala():
    fuentes = {n: _css(n) for n in ("style.css", "theme-terminal.css", "index.html", "entrar.html")}
    sueltos = []
    for nombre, texto in fuentes.items():
        for m in re.finditer(r"font-size:\s*([^;}\"!]+)", texto):
            valor = m.group(1).strip()
            if re.fullmatch(r"var\(--fs-[a-z]+\)", valor) or valor == "inherit":
                continue
            if nombre == "style.css" and texto[max(0, m.start() - 5):m.start()].endswith("html{"):
                continue  # la raiz: 14px, de ahi salen los rem
            sueltos.append(f"{nombre}: {m.group(0)}")
    assert sueltos == []
    # Los font-size de los SVG pintados por atributo, tambien a 12 px o mas.
    for m in re.finditer(r'font-size="(\d+)"', fuentes["index.html"]):
        assert int(m.group(1)) >= 13, m.group(0)
    raiz = re.search(r":root\{(.*?)\n\}", fuentes["style.css"], re.S).group(1)
    escala = dict(re.findall(r"--fs-([a-z]+):\s*(\d+)px", raiz))
    assert set(escala) == {"sm", "base", "md", "lg", "cifra", "icono"}
    assert min(int(v) for v in escala.values()) >= 12
    usados = set()
    for texto in fuentes.values():
        usados |= set(re.findall(r"var\(--fs-([a-z]+)\)", texto))
    assert usados <= set(escala), usados - set(escala)


def test_estilos_en_linea_a_menos_de_la_mitad_y_sus_clases_existen():
    html = _css("index.html")
    css = _css("style.css")
    fijos = re.findall(r'(?<=\s)style="', html)
    assert len(fijos) < 947 // 2, len(fijos)
    definidas = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
    utilidad = re.compile(r"^(fs|mt|mb|ml|mr|mx|my|m|pt|pb|pl|pr|px|py|p|gap|w|minw|maxw|h|maxh|ico|c|bg|bc|filete|ta|fw|lh)-")
    clases = set()
    for m in re.finditer(r'(?<=\s)class="([^"]*)"', html):
        clases |= {c for c in m.group(1).split() if utilidad.match(c)}
    sin_definir = sorted(c for c in clases if c not in definidas)
    assert sin_definir == []
    # Las de display van sin !important (x-show tiene que poder ocultar); el resto, con el.
    for regla in re.findall(r"^\.(?:flex|grid|bloque|inline-flex|contenidos)\{[^}]*\}", css, re.M):
        assert "!important" not in regla, regla
    assert re.search(r"^\.mt-35\{margin-top:\.35rem!important\}", css, re.M)


def test_un_solo_ayudante_de_porcentaje():
    src = _css("app.js") + _css("index.html")
    assert "pctEs" not in src
    assert re.search(r"^\s+pct: \(n, d = 1\) =>", _css("app.js"), re.M)
