"""La linea de robots.txt que decide si un bot puede pedir una URL (decision 129).

Protego da el veredicto (`can_fetch`) pero no la regla: para la ficha de una URL
bloqueada (decision 48) hace falta decir QUE linea la bloquea, que es lo que el
cliente tiene que tocar. Aqui se repite el emparejamiento con el numero de linea
de cada regla, con el mismo criterio que Protego 0.7 y que Google (RFC 9309):

- el grupo es el de mayor puntuacion: el nombre del grupo encontrado dentro del
  agente, en frontera de token, vale su longitud; `*` vale 1. Los grupos con el
  mismo nombre se fusionan (Google tambien lo hace);
- dentro del grupo gana la regla **mas larga** (longitud del patron ya
  normalizado), y con la misma longitud **gana `Allow`**;
- `*` es cualquier secuencia y un `$` final ancla al final de la URL;
- el escapado por ciento se normaliza en los dos lados igual (`%7E` y `~` son
  la misma ruta; `%2F` no es una barra);
- `Disallow:` vacio no es una regla; `/robots.txt` siempre se puede pedir.

`como_google=True` reescribe antes los grupos con `grupos_como_google`, igual
que `EvaluadorRobots`: el grupo se elige por token ENTERO (decision 113,
revision). Asi esta funcion y `EvaluadorRobots.permitido` dicen lo mismo, y
`tests/test_robots_regla.py` lo contrasta con Protego en una tabla de casos.
Si alguna vez discrepan, quien llama no afirma la linea (`veredicto_coincide`).

Pura Python, sin Protego: la usa la API, cuya imagen no lo lleva.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

from shared.robots_txt import LIMITE_BYTES, grupos_como_google

# --- Lectura de lineas: la misma tolerancia que Protego ----------------------

_DISALLOW = {"disallow", "dissallow", "dissalow", "disalow", "diasllow", "disallaw"}
_ALLOW = {"allow"}
_USER_AGENT = {"user-agent", "useragent", "user agent"}
_SITEMAP = {"sitemap", "sitemaps", "site-map"}
_HOST = {"host"}
_DEL_SITIO = _SITEMAP | _HOST
_TODAS = (_DISALLOW | _ALLOW | _USER_AGENT | _SITEMAP | _HOST
          | {"crawl-delay", "crawl delay", "request-rate", "request rate",
             "visit-time", "visit time"})
_MAX_PALABRAS = max(len(a.split()) for a in _TODAS)


def _directiva(linea: str) -> tuple[str, str] | None:
    campo = None
    valor = ""
    if ":" in linea:
        campo, crudo = linea.split(":", 1)
        campo = campo.strip().lower()
        valor = crudo.strip()
        if campo in _TODAS:
            return campo, valor
        if not crudo or crudo[0].isspace():
            return campo, valor
    palabras = linea.split()
    for n in range(1, _MAX_PALABRAS + 1):
        candidato = " ".join(palabras[:n]).lower()
        if candidato in _TODAS:
            return candidato, " ".join(palabras[n:])
    if campo is None:
        return None
    return campo, valor


# --- Normalizacion del escapado (la de Protego) ------------------------------

_HEX = set("0123456789ABCDEFabcdef")
_KEPT = "/%"
_PATTERN_KEPT = _KEPT + "*"
_SAFE = _KEPT + "="
_PATTERN_SAFE = _PATTERN_KEPT + "="
_URL_YA_ESCAPADA = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*)?(/(?!/)[A-Za-z0-9_.~/=-]*)").fullmatch
_PATRON_YA_ESCAPADO = re.compile(r"[A-Za-z0-9_.~/=*-]+\$?").fullmatch


def _unquote(texto: str, ignorar: str) -> str:
    if "%" not in texto:
        return texto
    no_tocar = {f"{ord(c):02X}" for c in ignorar}
    partes = texto.split("%")
    salida = [partes[0].encode("utf-8")]
    for parte in partes[1:]:
        if len(parte) >= 2 and set(parte[:2]) <= _HEX:
            hexa = parte[:2].upper()
            resto = parte[2:]
            if hexa not in no_tocar:
                salida.append(bytes.fromhex(hexa) + resto.encode("utf-8"))
                continue
            parte = hexa + resto
        salida.append(b"%" + parte.encode("utf-8"))
    return b"".join(salida).decode("utf-8", "replace")


def _quote(texto: str, kept: str, safe: str) -> str:
    return quote(_unquote(texto, kept), safe=safe)


def ruta_normalizada(url: str) -> str:
    """Ruta y query de la URL, escapadas como las compara una regla."""
    m = _URL_YA_ESCAPADA(url)
    if m:
        return m[1]
    url = url.partition("#")[0]
    p = urlsplit(url)
    ruta = p.path
    if "?" in url:
        ruta += f"?{p.query}"
    ruta = _quote(ruta, _KEPT, _SAFE)
    return ruta if ruta.startswith("/") else f"/{ruta}"


def _patron_normalizado(patron: str) -> str:
    if _PATRON_YA_ESCAPADO(patron):
        return patron
    if patron.startswith(("https://", "http://")):
        patron = f"/{patron}"
    ancla = ""
    if patron.endswith("$"):
        ancla = "$"
        patron = patron[:-1]
    return _quote(patron, _PATTERN_KEPT, _PATTERN_SAFE) + ancla


def _hexescape(c: str) -> str:
    return f"%{ord(c):02X}"


# --- Patron ------------------------------------------------------------------

class _Patron:
    def __init__(self, patron: str) -> None:
        self.patron = patron
        self.prioridad = len(patron)
        self._asterisco = "*" in patron
        self._dolar = patron.endswith("$")
        if self._asterisco:
            self._antes = patron[:patron.find("*")]
            p = patron[:-1] if self._dolar else patron
            self._trozos = re.sub(r"\*+", "*", p).split("*")
        elif self._dolar:
            self._sin_dolar = patron[:-1]

    def casa(self, url: str) -> bool:
        if not self._asterisco:
            if not self._dolar:
                return url.startswith(self.patron)
            return url == self._sin_dolar
        if not url.startswith(self._antes):
            return False
        trozos = self._trozos
        pos = len(trozos[0])
        for t in trozos[1:-1]:
            i = url.find(t, pos)
            if i == -1:
                return False
            pos = i + len(t)
        ultimo = trozos[-1]
        if self._dolar:
            fin = len(url) - len(ultimo)
            return fin >= pos and url[fin:] == ultimo
        if not ultimo:
            return True
        return url.find(ultimo, pos) != -1


# --- Reglas y grupos ---------------------------------------------------------

@dataclass
class _Regla:
    campo: str          # "allow" | "disallow"
    patron: _Patron
    linea: int          # 1-based, del texto ORIGINAL
    valor: str          # el valor tal y como se escribio


@dataclass
class _Grupo:
    nombre: str
    reglas: list[_Regla] = field(default_factory=list)
    lineas_ua: list[int] = field(default_factory=list)

    def puntuacion(self, agente: str) -> int:
        agente = agente.strip().lower()
        if self.nombre == "*":
            return 1
        i = agente.find(self.nombre)
        while i != -1:
            if i == 0 or not (agente[i - 1].isalnum() or agente[i - 1] in "-_"):
                return len(self.nombre)
            i = agente.find(self.nombre, i + 1)
        return 0

    def _anadir(self, campo: str, valor: str, linea: int, cache: dict) -> None:
        if "$" in valor:
            self._anadir(campo, valor.replace("$", _hexescape("$")), linea, cache)
        if valor not in cache:
            q = _patron_normalizado(valor)
            cache[valor] = _Patron(q) if q else None
        patron = cache[valor]
        if patron is None:
            return
        self.reglas.append(_Regla(campo, patron, linea, valor))
        if campo == "allow" and patron.patron.endswith("/index.html"):
            self.reglas.append(_Regla(
                "allow", _Patron(patron.patron.removesuffix("index.html") + "$"), linea, valor))

    def cerrar(self) -> None:
        self.reglas.sort(key=lambda r: (r.patron.prioridad, r.campo == "allow"), reverse=True)


def _grupos(texto: str) -> dict[str, _Grupo]:
    grupos: dict[str, _Grupo] = {}
    actuales: list[_Grupo] = []
    anterior = None
    cache: dict = {}
    for n, linea in enumerate(texto.splitlines(), start=1):
        h = linea.find("#")
        if h != -1:
            linea = linea[:h].strip()
        linea = linea.strip()
        if not linea:
            continue
        d = _directiva(linea)
        if d is None:
            continue
        campo, valor = d
        if campo in _USER_AGENT and anterior is not None and anterior not in _USER_AGENT:
            actuales = []
        if campo not in _DEL_SITIO:
            anterior = campo
        if not valor:
            continue
        if not actuales and campo not in _USER_AGENT and campo not in _DEL_SITIO:
            continue
        if campo in _USER_AGENT:
            ua = valor.strip().lower()
            sin_ast = ua.replace("*", "") if ua != "*" and "*" in ua else None
            for nombre in (ua, sin_ast):
                if not nombre:
                    continue
                g = grupos.get(nombre)
                if g is None:
                    g = grupos[nombre] = _Grupo(nombre)
                if g not in actuales:
                    actuales.append(g)
                g.lineas_ua.append(n)
        elif campo in _ALLOW or campo in _DISALLOW:
            for g in actuales:
                g._anadir("allow" if campo in _ALLOW else "disallow", valor, n, cache)
    for g in grupos.values():
        g.cerrar()
    return grupos


def _grupo_de(grupos: dict[str, _Grupo], agente: str) -> _Grupo | None:
    if not grupos:
        return None
    puntos, g = max(((g.puntuacion(agente), g) for g in grupos.values()), key=lambda p: p[0])
    return g if puntos else None


def _leido(texto: str | None) -> str:
    return (texto or "").encode("utf-8")[:LIMITE_BYTES].decode("utf-8", "ignore")


def _preparar(leido: str, original: list[str], agente: str, como_google: bool) -> dict:
    """El grupo que aplica a `agente` y la base del resultado (sin la URL)."""
    fuente = grupos_como_google(leido, agente) if como_google else leido
    vacio = {"permitida": True, "linea": None, "texto": None, "directiva": None,
             "valor": None, "grupo": None, "linea_grupo": None}
    grupo = _grupo_de(_grupos(fuente), agente)
    if grupo is None:
        return {"grupo": None, "base": vacio}
    linea_grupo = grupo.lineas_ua[0] if grupo.lineas_ua else None
    # La linea `User-agent` tal y como esta escrita: es lo que se busca en el fichero.
    nombre = (original[linea_grupo - 1].split("#")[0].strip()
              if linea_grupo and linea_grupo <= len(original) else grupo.nombre)
    return {"grupo": grupo, "base": dict(vacio, grupo=nombre, linea_grupo=linea_grupo),
            "vacio": vacio}


def _decidir(preparado: dict, original: list[str], url: str) -> dict:
    if "/robots.txt" in url and urlsplit(url).path == "/robots.txt":
        return dict(preparado.get("vacio") or preparado["base"])
    resultado = dict(preparado["base"])
    grupo = preparado["grupo"]
    if grupo is None:
        return resultado
    ruta = ruta_normalizada(url)
    for r in grupo.reglas:
        if r.patron.casa(ruta):
            resultado.update(
                permitida=r.campo == "allow", linea=r.linea,
                texto=original[r.linea - 1].strip() if r.linea <= len(original) else None,
                directiva="Allow" if r.campo == "allow" else "Disallow", valor=r.valor)
            break
    return resultado


def regla_que_decide(texto: str, url: str, agente: str, *,
                     como_google: bool = True) -> dict:
    """Que decide el robots.txt `texto` para `agente` sobre `url`.

    `agente` es un token (`Googlebot`, `GPTBot`) con `como_google=True`, o la
    cadena de User-Agent entera con la que se rastreo con `como_google=False`
    (que es como la evalua Scrapy con Protego, decision 98).

    Devuelve ``{"permitida", "linea", "texto", "directiva", "valor", "grupo",
    "linea_grupo"}``. Sin regla que case, ``linea`` es None (permitida porque
    nada la prohibe); sin grupo que aplique, ``grupo`` tambien.
    """
    leido = _leido(texto)
    original = leido.splitlines()
    return _decidir(_preparar(leido, original, agente, como_google), original, url)


def evaluar_lote(texto: str, urls: list[str], agentes: list[str], *,
                 como_google: bool = True) -> list[dict]:
    """`regla_que_decide` para cada (agente, url), leyendo el fichero una vez
    por agente: el robots.txt de prueba (decision 171). Mismo resultado que
    llamarla una a una, con ``agente`` y ``url`` en cada fila."""
    leido = _leido(texto)
    original = leido.splitlines()
    salida = []
    for agente in agentes:
        preparado = _preparar(leido, original, agente, como_google)
        for url in urls:
            salida.append(dict(_decidir(preparado, original, url), agente=agente, url=url))
    return salida


def diagnostico(texto: str | None) -> dict:
    """Lo que un robots.txt pegado tiene de raro antes de subirlo (decision 171).

    Las lineas que el parser se salta, con el mismo `_directiva` que la
    evaluacion: una directiva que no conoce (`Noindex:`, que Google dejo de
    leer en 2019, o una errata que la tolerancia de Protego no cubre) y una
    `Allow`/`Disallow` antes de cualquier `User-agent`, que no es de ningun
    grupo. Mas los `Sitemap` y si el fichero pasa del tope de 500 KiB que lee
    Google (lo de detras no existe para el). No juzga las reglas: eso es la
    evaluacion.
    """
    bruto = (texto or "").encode("utf-8")
    leido = _leido(texto)
    ignoradas: list[dict] = []
    sitemaps: list[dict] = []
    grupos = 0
    reglas = 0
    en_grupo = False
    anterior = None
    for n, linea in enumerate(leido.splitlines(), start=1):
        escrita = linea.strip()
        h = linea.find("#")
        if h != -1:
            linea = linea[:h]
        linea = linea.strip()
        if not linea:
            continue
        d = _directiva(linea)
        if d is None:
            ignoradas.append({"linea": n, "texto": escrita[:300], "motivo": "no_es_directiva"})
            continue
        campo, valor = d
        if campo not in _TODAS:
            ignoradas.append({"linea": n, "texto": escrita[:300],
                              "motivo": "directiva_desconocida"})
            continue
        if campo in _SITEMAP:
            if valor:
                sitemaps.append({"linea": n, "url": valor[:2000]})
            continue
        if campo in _USER_AGENT:
            if anterior not in _USER_AGENT:
                grupos += 1
            en_grupo = en_grupo or bool(valor)
        elif campo in (_ALLOW | _DISALLOW) and valor:
            if en_grupo:
                reglas += 1
            else:
                ignoradas.append({"linea": n, "texto": escrita[:300],
                                  "motivo": "regla_sin_user_agent"})
        if campo not in _DEL_SITIO:
            anterior = campo
    return {"bytes": len(bruto), "recortado": len(bruto) > LIMITE_BYTES,
            "grupos": grupos, "reglas": reglas, "sitemaps": sitemaps, "ignoradas": ignoradas}


def estado_guardado(fila) -> str:
    """El estado con el que se evaluo una fila de `robots_txt`.

    El que escribio el analisis (`evaluacion.googlebot.estado`, que tiene en
    cuenta la comprobacion al cerrar); sin analisis, el de la primera descarga.
    """
    from shared.robots_txt import estado

    ev = getattr(fila, "evaluacion", None) or {}
    g = ev.get("googlebot") if isinstance(ev, dict) else None
    if isinstance(g, dict) and g.get("estado"):
        return g["estado"]
    return estado(fila.status_code, fila.error, fila.response_class)


def reglas_de_url(fila, url: str, *, bloqueada_googlebot: bool | None,
                  blocked_by_robots: bool | None, user_agent: str | None,
                  robots_mode: str | None) -> dict | None:
    """La linea que bloquea una URL bloqueada, para su ficha (decisiones 48 y 129).

    Dos preguntas, porque son dos bloqueos: el de Googlebot
    (`urls.bloqueada_googlebot`) y el de nuestro rastreo (`blocked_by_robots`,
    con el UA entero, como decidio Scrapy). Solo se dice la linea si el
    veredicto de aqui es el guardado: si no coinciden, no se afirma (None en
    ese lado). Sin fila de `robots_txt`, o con un robots.txt que no se proceso,
    None: no se sabe.
    """
    from shared.robots_txt import PROCESADO

    if fila is None or not (bloqueada_googlebot or blocked_by_robots):
        return None
    if estado_guardado(fila) != PROCESADO or fila.cuerpo is None:
        return None
    out: dict = {"robots_url": fila.url, "googlebot": None, "rastreador": None}
    if bloqueada_googlebot:
        r = regla_que_decide(fila.cuerpo, url, "Googlebot")
        if r["permitida"] is False:
            out["googlebot"] = r
    if blocked_by_robots and robots_mode in (None, "respect", "audit") and user_agent:
        r = regla_que_decide(fila.cuerpo, url, user_agent, como_google=False)
        if r["permitida"] is False:
            out["rastreador"] = dict(r, user_agent=user_agent)
    if out["googlebot"] is None and out["rastreador"] is None:
        return None
    return out


__all__ = ["diagnostico", "estado_guardado", "evaluar_lote", "regla_que_decide", "reglas_de_url", "ruta_normalizada"]
