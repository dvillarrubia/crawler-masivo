"""URL/s del handler de descarga con conexiones reutilizadas o no (#277, decision 221).

Levanta un sitio HTTPS local (certificado propio) y descarga N paginas con el
`ImpersonateConMedidas` real, con la concurrencia que se pida, dos veces: con
una sesion de curl por peticion (lo de antes: `MAX_SESIONES_CURL = 0`) y con la
sesion compartida. La latencia de red se simula con netem en `lo`, por eso
necesita NET_ADMIN e iproute2:

    docker run --rm --cap-add NET_ADMIN -v "$PWD":/repo -w /repo \\
      -e PYTHONPATH=/repo:/repo/crawler crawler-masivo-crawler sh -c \\
      'apt-get install -qy iproute2 >/dev/null 2>&1 || (apt-get update -q && apt-get install -qy iproute2) >/dev/null; \\
       python scripts/bench/conexiones.py --rtt-ms 50 --paginas 400 --concurrencia 8'
"""

from __future__ import annotations

import argparse
import os
import ssl
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CUERPO = b"<html><head><title>x</title></head><body>" + b"<p>texto</p>" * 2000 + b"</body></html>"


class _Sitio(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive; curl negocia h2 solo si hay ALPN

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(CUERPO)))
        self.end_headers()
        self.wfile.write(CUERPO)

    def log_message(self, *a):
        pass


def _servidor() -> int:
    tmp = tempfile.mkdtemp()
    cert, key = os.path.join(tmp, "c.pem"), os.path.join(tmp, "k.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=localhost", "-keyout", key, "-out", cert],
                   check=True, capture_output=True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Sitio)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paginas", type=int, default=400)
    ap.add_argument("--concurrencia", type=int, default=8)
    ap.add_argument("--rtt-ms", type=int, default=50)
    args = ap.parse_args()

    if args.rtt_ms:
        subprocess.run(["tc", "qdisc", "replace", "dev", "lo", "root", "netem", "delay",
                        f"{args.rtt_ms / 2}ms"], check=True)

    from scrapy.utils.reactor import install_reactor
    install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")
    import asyncio

    from scrapy import Request
    from scrapy.utils.defer import deferred_from_coro
    from scrapy.utils.test import get_crawler
    from twisted.internet import reactor

    from seo_crawler import handlers

    puerto = _servidor()
    resultados: dict[str, float] = {}

    async def una_pasada(nombre: str, max_sesiones: int) -> None:
        handlers.MAX_SESIONES_CURL = max_sesiones
        crawler = get_crawler(settings_dict={"CONCURRENT_REQUESTS": args.concurrencia})
        h = handlers._clase_con_medidas().from_crawler(crawler)
        semaforo = asyncio.Semaphore(args.concurrencia)

        async def una(i: int) -> None:
            async with semaforo:
                r = Request(f"https://localhost:{puerto}/p{i}",
                            meta={"impersonate": "chrome124", "impersonate_args": {"verify": False}})
                resp = await h._download_request(r)
                assert resp.status == 200 and len(resp.body) == len(CUERPO)

        t0 = time.perf_counter()
        await asyncio.gather(*(una(i) for i in range(args.paginas)))
        resultados[nombre] = args.paginas / (time.perf_counter() - t0)
        await h.close()

    async def todo() -> None:
        try:
            await una_pasada("una sesion por peticion", 0)
            await una_pasada("sesion compartida", 8)
        finally:
            reactor.stop()

    reactor.callWhenRunning(lambda: deferred_from_coro(todo()))
    reactor.run()
    print(f"{args.paginas} paginas, concurrencia {args.concurrencia}, RTT {args.rtt_ms} ms")
    for nombre, ups in resultados.items():
        print(f"  {nombre:26s} {ups:7.1f} URL/s")


if __name__ == "__main__":
    main()
