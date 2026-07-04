"""
Grafo de juguete compartido por los tests de PageRank (v1 snapshot y,
en el futuro, T3 v2). No cambiar sin regenerar el snapshot congelado.

Topología (5 páginas internas + 1 externa):

    home ──content──▶ b ──content──▶ c ──content──▶ home
      │                │               │ (+footer duplicado→dedup max)
      │header──▶ c     │nav──▶ d       │ (self-link c→c excluido)
      │footer──▶ d     │ext──▶ X       │
      │nofollow─▶ e    (d y e son dangling)
"""

from __future__ import annotations

from shared.models import Link, Url

HOST = "https://toy.local"


def _hash(name: str) -> str:
    return name.ljust(64, "0")


def build_toy_graph(session, make_job):
    """Inserta el grafo de juguete. Devuelve (job, {nombre: Url})."""
    job = make_job(name="toy-pagerank")

    def _url(path: str, name: str, internal: bool = True) -> Url:
        u = Url(
            job_id=job.id,
            url=f"{HOST}{path}" if internal else path,
            url_hash=_hash(name),
            is_internal=internal,
            is_html=True,
            status_code=200,
        )
        session.add(u)
        return u

    urls = {
        "home": _url("/", "home"),
        "b": _url("/b", "b"),
        "c": _url("/c", "c"),
        "d": _url("/d", "d"),
        "e": _url("/e-nofollow-target", "e"),
        "ext": _url("https://ext.example.org/", "ext", internal=False),
    }
    session.flush()

    def _link(src: Url, dst_name: str, position: str | None,
              follow: bool = True, internal: bool = True) -> None:
        session.add(Link(
            job_id=job.id,
            from_url_id=src.id,
            to_url=f"{HOST}/{dst_name}",
            to_url_hash=_hash(dst_name),
            is_internal=internal,
            follow=follow,
            link_position=position,
        ))

    _link(urls["home"], "b", "content")
    _link(urls["home"], "c", "header")
    _link(urls["home"], "d", "footer")
    _link(urls["home"], "e", "content", follow=False)   # nofollow: fuera del grafo
    _link(urls["b"], "c", "content")
    _link(urls["b"], "d", "nav")                        # bug latente: peso 0.5
    _link(urls["b"], "ext", "content", internal=False)  # externa: fuera del grafo
    _link(urls["c"], "home", "content")
    _link(urls["c"], "home", "footer")                  # duplicada: dedup se queda 1.0
    _link(urls["c"], "c", "content")                    # self-link: excluida
    session.flush()

    return job, urls
