"""Sitio HOSTIL para pruebas de robustez del crawler.

Sirve deliberadamente todo lo que rompe crawlers: bucles y cadenas de
redirección, encoding mentiroso, bytes inválidos, páginas gigantes,
trampas infinitas (calendario/facetas/paginación), soft-404 (NUNCA
devuelve 404 salvo donde se indica), HTML malformado, binario como HTML,
URLs basura, canonicals en bucle, hreflang inválido, JSON-LD roto,
duplicados y casi-duplicados, endpoints lentos y códigos raros.

Uso:
    python scripts/hostile_site.py [puerto]      # default 8099

Crawlearlo desde el contenedor: http://host.docker.internal:<puerto>/
"""
from __future__ import annotations

import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8099

ERROR_TEMPLATE = """<!doctype html><html><head><title>Error 404 - Página no encontrada</title>
</head><body><header><nav><a href="/">Inicio</a></nav></header>
<h1>Ups, no encontramos esa página</h1>
<p>La página que buscas no existe o fue movida. Vuelve al inicio.</p>
<footer><a href="/">hostil.local</a></footer></body></html>"""


def page(title: str, body: str, extra_head: str = "") -> bytes:
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>{title}</title>{extra_head}</head><body>
<header><nav><a href="/">Inicio</a> <a href="/seccion/normal">Normal</a> <a href="/mil-enlaces">Enlaces</a></nav></header>
<main><h1>{title}</h1>{body}</main>
<footer><a href="/">hostil.local</a> <a href="/legal">Legal</a></footer>
</body></html>""".encode("utf-8")


HOME_LINKS = [
    "/seccion/normal", "/loop-a", "/cadena/1", "/meta-refresh", "/js-redirect",
    "/redirect-self", "/redirect-a-404", "/latin1-mentiroso", "/utf8-roto",
    "/win1252", "/sin-content-type", "/gigante", "/mil-enlaces",
    "/calendario?dia=1", "/faceta?color=rojo", "/paginacion/1", "/soft-404",
    "/404-real", "/500", "/503", "/429", "/418", "/999", "/204", "/lenta-media",
    "/html-roto", "/binario-como-html", "/nul-bytes",
    "/CON%20MAYUSCULAS%20Y%20ESPACIOS/p%C3%A1gina", "//doble//slash",
    "/largo-" + "x" * 300, "/con-utm?utm_source=hostil&utm_medium=test",
    "/canonical-loop-a", "/canonical-404", "/canonical-externo",
    "/hreflang-malo", "/jsonld-roto", "/jsonld-gigante",
    "/dup-1", "/dup-2", "/casi-dup-1", "/casi-dup-2", "/titulo-hostil",
    "/bloqueada-robots",
]


class Hostile(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # silencioso
        pass

    def _send(self, status: int, body: bytes, ctype: str | None = "text/html; charset=utf-8",
              headers: dict | None = None):
        self.send_response(status)
        if ctype is not None:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):  # noqa: C901 — es un zoo a propósito
        raw = self.path
        parts = urlsplit(raw)
        path = unquote(parts.path)
        qs = parse_qs(parts.query)

        # -- infraestructura ------------------------------------------------
        if path == "/robots.txt":
            body = (f"User-agent: *\nDisallow: /bloqueada-robots\n"
                    f"Sitemap: http://{self.headers.get('Host')}/sitemap.xml\n").encode()
            return self._send(200, body, "text/plain")
        if path == "/sitemap.xml":
            host = self.headers.get("Host")
            body = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>http://{host}/seccion/normal</loc><lastmod>2026-01-15</lastmod></url>
<url><loc>http://{host}/fantasma-1</loc><lastmod>fecha-basura</lastmod></url>
<url><loc>http://{host}/fantasma-2</loc><lastmod>2026-13-45</lastmod></url>
<url><loc>http://{host}/fantasma-3</loc></url>
</urlset>""".encode()
            return self._send(200, body, "application/xml")

        # -- home -----------------------------------------------------------
        if path == "/":
            links = "".join(f'<p><a href="{u}">enlace {i}</a></p>'
                            for i, u in enumerate(HOME_LINKS))
            extra = ('<p><a href="javascript:void(0)">js falso</a>'
                     '<a href="mailto:x@hostil.local">mail</a>'
                     '<a href="tel:+34600000000">tel</a>'
                     '<a href="#solo-fragmento">fragmento</a>'
                     '<a href="data:text/html,hola">data uri</a></p>')
            return self._send(200, page("Portada hostil", links + extra))

        # -- redirecciones ----------------------------------------------------
        if path == "/loop-a":
            return self._send(301, b"", headers={"Location": "/loop-b"})
        if path == "/loop-b":
            return self._send(301, b"", headers={"Location": "/loop-a"})
        if path.startswith("/cadena/"):
            n = int(path.rsplit("/", 1)[1] or 1)
            if n >= 6:
                return self._send(200, page("Final de la cadena", "<p>Llegaste tras 6 saltos.</p>"))
            return self._send(302, b"", headers={"Location": f"/cadena/{n + 1}"})
        if path == "/meta-refresh":
            return self._send(200, page("Meta refresh", "<p>Te vas.</p>",
                              '<meta http-equiv="refresh" content="0;url=/seccion/normal">'))
        if path == "/js-redirect":
            return self._send(200, page("JS redirect",
                              '<script>window.location.href="/seccion/normal";</script><p>redirigiendo</p>'))
        if path == "/redirect-self":
            return self._send(301, b"", headers={"Location": "/redirect-self"})
        if path == "/redirect-a-404":
            return self._send(302, b"", headers={"Location": "/404-real"})

        # -- encoding hostil ---------------------------------------------------
        if path == "/latin1-mentiroso":
            # cabecera dice UTF-8, bytes son Latin-1
            body = ("<!doctype html><html><head><meta charset='utf-8'><title>Ñandú café</title></head>"
                    "<body><h1>Ñoño añejo</h1><p>Camión, jamón, montaña.</p></body></html>").encode("latin-1")
            return self._send(200, body, "text/html; charset=utf-8")
        if path == "/utf8-roto":
            body = (b"<!doctype html><html><head><title>Bytes rotos</title></head><body>"
                    b"<h1>Texto</h1><p>v\xc3\xa1lido y luego \xff\xfe\x80 roto</p></body></html>")
            return self._send(200, body)
        if path == "/win1252":
            body = ("<!doctype html><html><head><meta charset='windows-1252'>"
                    "<title>Windows-1252</title></head><body><h1>Precio: 9,99€</h1>"
                    "<p>“Comillas curvas” y —guiones—.</p></body></html>").encode("cp1252")
            return self._send(200, body, "text/html; charset=windows-1252")
        if path == "/sin-content-type":
            return self._send(200, page("Sin content type", "<p>Adivina qué soy.</p>"), ctype=None)

        # -- tamaño ------------------------------------------------------------
        if path == "/gigante":
            parrafo = "<p>" + ("palabra " * 500) + "</p>"
            return self._send(200, page("Página gigante", parrafo * 1200))  # ~4 MB
        if path == "/mil-enlaces":
            links = "".join(f'<a href="/generada/{i}">g{i}</a> ' for i in range(3000))
            return self._send(200, page("Tres mil enlaces", links))
        if path.startswith("/generada/"):
            return self._send(200, page(f"Generada {path}", "<p>Página fina generada.</p>"))

        # -- trampas -----------------------------------------------------------
        if path == "/calendario":
            dia = int(qs.get("dia", ["1"])[0])
            return self._send(200, page(f"Calendario día {dia}",
                              f'<p><a href="/calendario?dia={dia + 1}">día siguiente</a></p>'))
        if path == "/faceta":
            colores = ["rojo", "azul", "verde", "negro"]
            tallas = ["s", "m", "l", "xl"]
            links = "".join(
                f'<a href="/faceta?color={c}&talla={t}&orden={o}">f</a> '
                for c in colores for t in tallas for o in ("asc", "desc"))
            return self._send(200, page("Facetas infinitas", links))
        if path.startswith("/paginacion/"):
            n = int(path.rsplit("/", 1)[1] or 1)
            return self._send(200, page(f"Listado página {n}",
                              f'<p>items</p><a href="/paginacion/{n + 1}" rel="next">siguiente</a>'))

        # -- estados -----------------------------------------------------------
        if path == "/soft-404":
            return self._send(200, ERROR_TEMPLATE.encode())
        if path == "/404-real":
            return self._send(404, page("No existe", "<p>404 de verdad.</p>"))
        if path == "/500":
            return self._send(500, b"error interno")
        if path == "/503":
            return self._send(503, b"mantenimiento", headers={"Retry-After": "1"})
        if path == "/429":
            return self._send(429, b"calma", headers={"Retry-After": "1"})
        if path == "/418":
            return self._send(418, page("Tetera", "<p>Soy una tetera.</p>"))
        if path == "/999":
            return self._send(999, page("Estado inventado", "<p>999.</p>"))
        if path == "/204":
            self.send_response(204)
            self.end_headers()
            return
        if path == "/lenta-media":
            time.sleep(2)
            return self._send(200, page("Lenta", "<p>Tardé 2 segundos.</p>"))

        # -- malformados --------------------------------------------------------
        if path == "/html-roto":
            body = (b"<html><head><title>Roto<title></head><body><h1>Sin cerrar"
                    b"<div><p>parrafo<div><span>caos</b></body>")
            return self._send(200, body)
        if path == "/binario-como-html":
            fake_png = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40
            return self._send(200, fake_png, "text/html; charset=utf-8")
        if path == "/nul-bytes":
            body = ("<!doctype html><html><head><title>Nulos</title></head><body>"
                    "<h1>Con\x00nulos\x00dentro</h1></body></html>").encode()
            return self._send(200, body)

        # -- canonicals / hreflang / datos estructurados --------------------------
        if path == "/canonical-loop-a":
            return self._send(200, page("Canonical loop A", "<p>a</p>",
                              '<link rel="canonical" href="/canonical-loop-b">'))
        if path == "/canonical-loop-b":
            return self._send(200, page("Canonical loop B", "<p>b</p>",
                              '<link rel="canonical" href="/canonical-loop-a">'))
        if path == "/canonical-404":
            return self._send(200, page("Canonical a 404", "<p>x</p>",
                              '<link rel="canonical" href="/404-real">'))
        if path == "/canonical-externo":
            return self._send(200, page("Canonical externo", "<p>x</p>",
                              '<link rel="canonical" href="https://otro-dominio.example/">'))
        if path == "/hreflang-malo":
            return self._send(200, page("Hreflang malo", "<p>x</p>",
                              '<link rel="alternate" hreflang="en-UK" href="/404-real">'
                              '<link rel="alternate" hreflang="zz-XX" href="/hreflang-malo">'))
        if path == "/jsonld-roto":
            return self._send(200, page("JSON-LD roto", "<p>x</p>",
                              '<script type="application/ld+json">{"@context": "https://schema.org", '
                              '"@type": "Product", "name": "sin cerrar</script>'))
        if path == "/jsonld-gigante":
            items = ",".join(f'{{"@type":"Thing","name":"item {i}"}}' for i in range(20000))
            return self._send(200, page("JSON-LD gigante", "<p>x</p>",
                              f'<script type="application/ld+json">{{"@graph":[{items}]}}</script>'))

        # -- duplicados -----------------------------------------------------------
        if path in ("/dup-1", "/dup-2"):
            return self._send(200, page("Contenido duplicado exacto",
                              "<p>" + "texto idéntico repetido " * 60 + "</p>"))
        if path in ("/casi-dup-1", "/casi-dup-2"):
            extra = "final uno" if path.endswith("1") else "final dos"
            return self._send(200, page("Contenido casi duplicado",
                              "<p>" + "texto compartido en ambas paginas " * 60 + extra + "</p>"))

        # -- on-page hostil ---------------------------------------------------------
        if path == "/titulo-hostil":
            head = ("<title>🔥🚀 " + "Título larguísimo " * 40 + "</title>"
                    "<title>Segundo title</title><title>Tercero</title>")
            body = "".join(f"<h1>H1 número {i} 🎯</h1>" for i in range(5))
            anchor = "a" * 10000
            return self._send(200, page("ignorado", body +
                              f'<a href="/seccion/normal">{anchor}</a>', head))

        # -- normales -----------------------------------------------------------------
        if path == "/seccion/normal":
            return self._send(200, page("Sección normal",
                              "<p>" + "contenido normal y sano de la seccion con palabras variadas " * 30 + "</p>"
                              '<p><a href="/seccion/hija">hija</a></p>'))
        if path == "/seccion/hija":
            return self._send(200, page("Hija normal", "<p>" + "texto hijo decente " * 50 + "</p>"))
        if path == "/legal":
            return self._send(200, page("Legal", "<p>aviso legal breve</p>"))
        if path == "/bloqueada-robots":
            return self._send(200, page("Bloqueada", "<p>no deberías estar aquí con robots respect</p>"))

        # -- default: soft-404 global (NUNCA 404) --------------------------------------
        return self._send(200, ERROR_TEMPLATE.encode())


if __name__ == "__main__":
    print(f"Sitio hostil en http://0.0.0.0:{PORT}/ — Ctrl+C para parar")
    ThreadingHTTPServer(("0.0.0.0", PORT), Hostile).serve_forever()
