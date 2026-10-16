"""Extraccion y busqueda a medida por job (#34, decision 104).

Lo que Screaming Frog llama *custom extraction* y *custom search*: reglas que
escribe el consultor (un selector CSS, una XPath o una regex) y que se aplican
a cada pagina HTML 2xx del rastreo. El resultado es una fila por (URL, regla)
en la tabla `custom_extraction`.

Vive en `shared/` y no en el extractor porque lo usan DOS sitios que tienen
que dar exactamente lo mismo: el spider, al rastrear, y la API, que valida las
reglas al guardarlas y las prueba contra un HTML pegado
(`POST /api/extraccion/probar`). Dos copias acabarian discrepando (decisiones
23 y 39). `lxml`, `parsel` y `regex` se importan dentro de las funciones: la
imagen de la API los lleva desde esta PR, pero importar el modulo no los exige.

Cuatro reglas que no son obvias:

- **Una XPath se compila con `lxml.etree.XPath` sin namespaces y con
  `regexp=False`.** `Selector.xpath` de parsel registra el `re:` de EXSLT, que
  usa el `re` de Python —vulnerable a ReDoS y sin forma de cortarlo—. Sin el
  prefijo, `re:test()` falla al validar con «prefijo no definido».
- **Las regex van con el modulo `regex` y `timeout`.** Mantiene lookaround y
  referencias hacia atras, que los consultores usan, y se puede cortar. Una
  regla que agota el tiempo `AGOTADOS_PARA_DESACTIVAR` veces queda
  `desactivada` el resto del rastreo: el cortacircuitos es lo que evita pagar
  el tope en cada pagina.
- **Sin fila no es «vacio».** Una URL sin fila de una regla no se evaluo (no
  es HTML 2xx, cae fuera de `solo_urls` o el censo es anterior a la regla).
  «No contiene» es exactamente `estado='sin_coincidencia'`; una busqueda sobre
  una fuente recortada que no encuentra nada es `incompleta`, que NO es «no
  contiene».
- **El texto de un elemento es el que se pinta**: sale de
  `shared.texto_visible`, el mismo recorrido que `word_count` y
  `page_content`, asi que `Zapa<span>tillas` es una palabra y lo oculto no
  entra.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

NOMBRE_REGLA = r"^[a-z][a-z0-9_]{0,39}$"
_RE_NOMBRE = re.compile(NOMBRE_REGLA)

MAX_EXTRACTORES = 25
MAX_BUSQUEDAS = 25

# Lo maximo que las reglas pueden guardar por pagina (suma de
# max_valores x max_caracteres). Con 60.000 URLs, 64 kB son 3,8 GB en el peor
# caso: el tope no es para el caso normal sino para que un `todos` x 100 x
# 10.000 no se escriba sin darse cuenta.
MAX_BYTES_PAGINA = int(os.getenv("CUSTOM_EXTRACTION_MAX_BYTES_PAGINA", "65536"))
# Tiempo por regex y por pagina. Una regex corre con `timeout`; una XPath no se
# puede interrumpir, se mide.
TIMEOUT_REGEX_S = float(os.getenv("CUSTOM_EXTRACTION_TIMEOUT_MS", "50")) / 1000.0
PRESUPUESTO_PAGINA_MS = float(os.getenv("CUSTOM_EXTRACTION_PRESUPUESTO_MS", "250"))
AGOTADOS_PARA_DESACTIVAR = int(os.getenv("CUSTOM_EXTRACTION_AGOTADOS", "3"))
# Las regex y las busquedas leen como mucho esto de la fuente (caracteres).
FUENTE_MAX = 2 * 1024 * 1024
# Coincidencias que se cuentan como mucho en una busqueda o un recuento.
MAX_COINCIDENCIAS = 10_000
CONTEXTO = 120

ESTADOS = (
    "ok", "sin_coincidencia", "incompleta", "conversion_fallida", "tiempo_agotado",
    "presupuesto_pagina", "desactivada", "pagina_bloqueada", "error",
)
# Estados en los que la regla SE EVALUO de verdad: los unicos sobre los que se
# puede afirmar «contiene» o «no contiene».
ESTADOS_EVALUADOS = ("ok", "sin_coincidencia", "conversion_fallida")

# Valores por defecto de cada regla. Los repite `api/schemas.py` (Pydantic) y
# un test comprueba que coinciden: la firma los quita para que anadir un campo
# con su valor por defecto no cambie la firma de las reglas ya guardadas.
DEFECTO_EXTRACTOR: dict[str, Any] = {
    "etiqueta": None, "devuelve": "texto", "atributo": None, "grupo": None,
    "ignorar_mayusculas": False, "fuente": "html", "cuantos": "primero",
    "max_valores": 20, "max_caracteres": 1000, "valor": "texto",
    "separador_decimal": None, "solo_urls": None,
}
DEFECTO_BUSQUEDA: dict[str, Any] = {
    "etiqueta": None, "modo": "texto", "ignorar_mayusculas": True, "fuente": "html",
    "dentro_de": None, "solo_urls": None,
}

_HTML_PRUEBA = ("<html><head><title>t</title><meta name='d' content='c'></head><body>"
                "<div class='a' id='b'><p>x<a href='/y'>y</a><span>z</span></p>"
                "<ul><li>1</li></ul><img src='i' alt='a'></div></body></html>")


# ---------------------------------------------------------------------------
# Validacion
# ---------------------------------------------------------------------------
def _xpath_absoluto_en_predicado(expr: str) -> bool:
    """`//a[//b]`: dentro del predicado, `//` recorre el documento ENTERO por
    cada nodo candidato. Es cuadratico y es casi siempre un error (lo que se
    quiere es `.//b`)."""
    profundidad = 0
    comilla = None
    i = 0
    while i < len(expr):
        c = expr[i]
        if comilla:
            if c == comilla:
                comilla = None
        elif c in "'\"":
            comilla = c
        elif c == "[":
            profundidad += 1
        elif c == "]":
            profundidad = max(0, profundidad - 1)
        elif c == "/" and expr[i:i + 2] == "//" and profundidad > 0:
            antes = expr[:i].rstrip()
            if not antes or antes[-1] in "[(,=<>|!+*" or re.search(r"\b(and|or|div|mod)$", antes):
                return True
            i += 1
        i += 1
    return False


def _fuera_de_comillas(expr: str) -> str:
    """La expresion con los literales entre comillas vaciados."""
    return re.sub(r"'[^']*'|\"[^\"]*\"", "''", expr)


def _prefijos(expr: str) -> set[str]:
    """Prefijos de namespace (`re:test`, `str:tokenize`) fuera de literales.

    Se miran en el TEXTO y no solo evaluando: una XPath con `re:test()` dentro
    de un predicado compila, y solo falla si llega a evaluarse sobre un nodo
    que exista. `xml:` es el unico predefinido (`@xml:lang`)."""
    limpio = _fuera_de_comillas(expr)
    return {m.group(1) for m in re.finditer(r"(?<![\w:.-])([A-Za-z_][\w.-]*):(?![:])", limpio)
            if m.group(1) != "xml"}


def _compilar_xpath(expr: str):
    from lxml import etree

    # Sin namespaces y sin EXSLT: `re:test()` no tiene prefijo (ver docstring).
    return etree.XPath(expr, regexp=False, smart_strings=False)


def _css_a_xpath(expr: str) -> str:
    from parsel.csstranslator import HTMLTranslator

    return HTMLTranslator().css_to_xpath(expr)


def _compilar_regex(patron: str, ignorar: bool):
    import regex

    flags = regex.IGNORECASE if ignorar else 0
    return regex.compile(patron, flags)


def _probar_xpath(xp) -> None:
    """Las XPath mal escritas que compilan (`foo()`, `re:test()`) solo fallan
    al evaluar: se evaluan una vez contra un documento minimo."""
    from lxml import html as lhtml

    xp(lhtml.fromstring(_HTML_PRUEBA))


def _bytes_regla(r: dict, clase: str) -> int:
    if clase == "busqueda":
        return CONTEXTO
    if r.get("cuantos") == "recuento":
        return 16
    por_valor = int(r.get("max_caracteres") or DEFECTO_EXTRACTOR["max_caracteres"])
    if r.get("cuantos") == "todos":
        return por_valor * int(r.get("max_valores") or DEFECTO_EXTRACTOR["max_valores"])
    return por_valor


def validar(ce: dict | None, cs: dict | None, *, extract_page_content: bool = True,
            max_urls: int | None = None) -> list[str]:
    """Errores de las reglas, cada uno con su ruta (`custom_extraction.precio:
    selector CSS no valido: ...`). Lista vacia = validas.

    Recibe dicts (las reglas ya pasadas por Pydantic y volcadas): asi lo puede
    llamar quien no tenga los modelos de la API.
    """
    ce = ce or {}
    cs = cs or {}
    errores: list[str] = []
    if len(ce) > MAX_EXTRACTORES:
        errores.append(f"custom_extraction: como mucho {MAX_EXTRACTORES} extractores ({len(ce)})")
    if len(cs) > MAX_BUSQUEDAS:
        errores.append(f"custom_search: como mucho {MAX_BUSQUEDAS} busquedas ({len(cs)})")
    for nombre in set(ce) & set(cs):
        errores.append(
            f"custom_search.{nombre}: el nombre ya lo usa un extractor (comparten "
            f"las columnas x.<nombre>)")

    total_bytes = 0
    for clase, reglas, raiz in (("extractor", ce, "custom_extraction"),
                                ("busqueda", cs, "custom_search")):
        for nombre, r in reglas.items():
            ruta = f"{raiz}.{nombre}"
            if r is None:
                continue
            if not isinstance(nombre, str) or not _RE_NOMBRE.match(nombre):
                errores.append(f"{ruta}: el nombre tiene que ser minusculas, cifras y _ "
                               f"empezando por letra, 40 caracteres como mucho")
                continue
            total_bytes += _bytes_regla(r, clase)
            try:
                errores += [f"{ruta}: {e}" for e in _validar_regla(r, clase, extract_page_content)]
            except ImportError as exc:  # pragma: no cover - imagen sin dependencias
                errores.append(f"{ruta}: falta una dependencia para validar ({exc})")
    if total_bytes > MAX_BYTES_PAGINA:
        extra = ""
        if max_urls:
            extra = (f"; con max_urls={max_urls} serian hasta "
                     f"{total_bytes * max_urls / 1e9:.1f} GB")
        errores.append(
            f"custom_extraction: las reglas pueden guardar hasta {total_bytes // 1024} kB "
            f"por pagina y el limite es {MAX_BYTES_PAGINA // 1024} kB (suma de "
            f"max_valores x max_caracteres){extra}")
    return errores


def _validar_regla(r: dict, clase: str, extract_page_content: bool) -> list[str]:
    errores: list[str] = []
    fuente = r.get("fuente") or "html"
    if fuente == "contenido" and not extract_page_content:
        errores.append("fuente=contenido necesita extraction.extract_page_content")
    if r.get("solo_urls"):
        try:
            _compilar_regex(r["solo_urls"], False)
        except Exception as exc:
            errores.append(f"solo_urls no es una regex valida: {exc}")

    if clase == "busqueda":
        if r.get("dentro_de"):
            if fuente == "contenido":
                errores.append("dentro_de solo vale con fuente html o texto")
            try:
                xp = _compilar_xpath(_css_a_xpath(r["dentro_de"]))
                _probar_xpath(xp)
            except Exception as exc:
                errores.append(f"dentro_de: selector CSS no valido: {exc}")
        if (r.get("modo") or "texto") == "regex":
            try:
                rx = _compilar_regex(r["patron"], bool(r.get("ignorar_mayusculas", True)))
            except Exception as exc:
                return errores + [f"regex no valida: {exc}"]
            if rx.search("") is not None:
                errores.append("la regex casa con la cadena vacia: contaria una "
                               "coincidencia en cada posicion de la pagina")
        return errores

    tipo = r.get("tipo")
    devuelve = r.get("devuelve") or "texto"
    if tipo in ("css", "xpath"):
        if fuente != "html":
            errores.append(f"un selector {tipo.upper()} se aplica al HTML: fuente tiene "
                           f"que ser html")
        if r.get("grupo") is not None:
            errores.append("grupo solo vale con tipo regex")
        if (devuelve == "atributo") != bool(r.get("atributo")):
            errores.append("atributo va si y solo si devuelve=atributo")
        expr = r.get("expresion") or ""
        try:
            xpath = _css_a_xpath(expr) if tipo == "css" else expr
        except Exception as exc:
            return errores + [f"selector CSS no valido: {exc}"]
        if tipo == "xpath" and _prefijos(expr):
            return errores + [
                "XPath no valida: prefijo no definido " + ", ".join(sorted(_prefijos(expr)))
                + ": (las extensiones EXSLT como re:test() no se admiten)"]
        if tipo == "xpath" and _xpath_absoluto_en_predicado(expr):
            errores.append("XPath con // absoluto dentro de un predicado (recorre la "
                           "pagina entera por cada nodo): usa .// para buscar dentro")
        try:
            _probar_xpath(_compilar_xpath(xpath))
        except Exception as exc:
            nombre = "selector CSS" if tipo == "css" else "XPath"
            mensaje = str(exc)
            if "namespace prefix" in mensaje.lower():
                mensaje += " (las extensiones EXSLT como re:test() no se admiten)"
            errores.append(f"{nombre} no valido: {mensaje}")
    elif tipo == "regex":
        if devuelve != "texto" or r.get("atributo"):
            errores.append("devuelve y atributo solo valen con css o xpath; en una "
                           "regex se elige el grupo")
        try:
            rx = _compilar_regex(r.get("expresion") or "", bool(r.get("ignorar_mayusculas")))
        except Exception as exc:
            return errores + [f"regex no valida: {exc}"]
        grupo = r.get("grupo")
        if grupo is not None and grupo > rx.groups:
            errores.append(f"grupo {grupo} y la regex solo tiene {rx.groups}")
        if r.get("cuantos") == "recuento" and rx.search("") is not None:
            errores.append("la regex casa con la cadena vacia: el recuento no significaria nada")
    else:
        errores.append(f"tipo desconocido: {tipo!r}")
    return errores


# ---------------------------------------------------------------------------
# Firma: identidad de una regla para comparar censos (3.4)
# ---------------------------------------------------------------------------
def firma(regla: dict, clase: str = "extractor") -> str:
    """SHA-256 de la regla normalizada, SIN la etiqueta y sin los valores por
    defecto: renombrar la columna no cambia lo que se extrae, y anadir un
    campo nuevo con su valor por defecto tampoco."""
    defecto = DEFECTO_BUSQUEDA if clase == "busqueda" else DEFECTO_EXTRACTOR
    limpia = {k: v for k, v in (regla or {}).items()
              if k != "etiqueta" and v is not None and defecto.get(k, object()) != v}
    texto = json.dumps({"clase": clase, **limpia}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Conversion a numero
# ---------------------------------------------------------------------------
_RE_FRAGMENTO = re.compile(r"[-−]?\d(?:[\d.,'’   ]*\d)?")


def a_numero(texto: str | None, separador: str | None = None) -> float | None:
    """El primer numero del texto, o None si no lo hay o es ambiguo.

    Con `separador` (`,` o `.`) se sabe cual es el decimal. En automatico: si
    aparecen punto y coma, el ULTIMO es el decimal (`1.299,00` y `1,234.56`);
    si solo aparece uno, repetido es de millares (`1.234.567`); una sola vez
    con tres cifras detras es AMBIGUO (`1.234` son mil doscientos o uno coma
    dos) y devuelve None —la fila sale `conversion_fallida`—: inventar uno de
    los dos daria precios mil veces mayores o menores sin que nada lo marque.
    """
    if not texto:
        return None
    m = _RE_FRAGMENTO.search(str(texto))
    if not m:
        return None
    s = m.group(0).replace("−", "-")
    for c in ("'", "’", " ", " ", " "):
        s = s.replace(c, "")
    negativo = s.startswith("-")
    s = s.lstrip("-")
    if separador == ",":
        s = s.replace(".", "").replace(",", ".")
    elif separador == ".":
        s = s.replace(",", "")
    else:
        tiene_p, tiene_c = "." in s, "," in s
        if tiene_p and tiene_c:
            dec = "." if s.rfind(".") > s.rfind(",") else ","
            mil = "," if dec == "." else "."
            s = s.replace(mil, "").replace(dec, ".")
        elif tiene_p or tiene_c:
            sep = "." if tiene_p else ","
            if s.count(sep) > 1:
                s = s.replace(sep, "")
            else:
                detras = len(s) - s.index(sep) - 1
                if detras == 3:
                    return None
                s = s.replace(sep, ".")
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if negativo else n


# ---------------------------------------------------------------------------
# Compilacion
# ---------------------------------------------------------------------------
@dataclass
class ReglaCompilada:
    nombre: str
    clase: str                    # extractor | busqueda
    regla: dict
    xpath: Any = None             # css/xpath: lxml.etree.XPath
    rx: Any = None                # regex o busqueda
    grupo: int = 0
    solo_urls: Any = None
    dentro_de: Any = None


@dataclass
class Compiladas:
    reglas: list[ReglaCompilada] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.reglas)

    def __len__(self) -> int:
        return len(self.reglas)


def compilar(ce: dict | None, cs: dict | None) -> Compiladas:
    """Reglas listas para aplicar. Lanza ValueError con todos los errores si
    alguna no es valida: el spider no descarta reglas en silencio."""
    ce = {k: v for k, v in (ce or {}).items() if v is not None}
    cs = {k: v for k, v in (cs or {}).items() if v is not None}
    errores = validar(ce, cs)
    if errores:
        raise ValueError("; ".join(errores))
    salida = Compiladas()
    for nombre, r in ce.items():
        rc = ReglaCompilada(nombre=nombre, clase="extractor", regla=r)
        if r["tipo"] == "regex":
            rc.rx = _compilar_regex(r["expresion"], bool(r.get("ignorar_mayusculas")))
            g = r.get("grupo")
            rc.grupo = g if g is not None else (1 if rc.rx.groups else 0)
        else:
            expr = _css_a_xpath(r["expresion"]) if r["tipo"] == "css" else r["expresion"]
            rc.xpath = _compilar_xpath(expr)
        if r.get("solo_urls"):
            rc.solo_urls = _compilar_regex(r["solo_urls"], False)
        salida.reglas.append(rc)
    for nombre, r in cs.items():
        rc = ReglaCompilada(nombre=nombre, clase="busqueda", regla=r)
        import regex

        patron = r["patron"] if (r.get("modo") or "texto") == "regex" else regex.escape(r["patron"])
        rc.rx = _compilar_regex(patron, bool(r.get("ignorar_mayusculas", True)))
        if r.get("solo_urls"):
            rc.solo_urls = _compilar_regex(r["solo_urls"], False)
        if r.get("dentro_de"):
            rc.dentro_de = _compilar_xpath(_css_a_xpath(r["dentro_de"]))
        salida.reglas.append(rc)
    return salida


# ---------------------------------------------------------------------------
# Presupuesto de tiempo y cortacircuitos
# ---------------------------------------------------------------------------
class Presupuesto:
    """Tiempo acumulado por regla y cortacircuitos, para TODO el rastreo.

    Lo crea el spider una vez (o el endpoint de prueba, uno por llamada). Una
    regla que agota su tiempo `AGOTADOS_PARA_DESACTIVAR` veces se desactiva:
    sin esto, una regex catastrofica pagaria su `timeout` en cada una de las
    60.000 paginas (50 minutos de reactor parado).
    """

    def __init__(self, ms_pagina: float | None = None, timeout_s: float | None = None,
                 agotados_max: int | None = None):
        self.ms_pagina = PRESUPUESTO_PAGINA_MS if ms_pagina is None else ms_pagina
        self.timeout_s = TIMEOUT_REGEX_S if timeout_s is None else timeout_s
        self.agotados_max = AGOTADOS_PARA_DESACTIVAR if agotados_max is None else agotados_max
        self.agotados: dict[str, int] = {}
        self.desactivadas: set[str] = set()
        self.ms_por_regla: dict[str, float] = {}
        self.recien_desactivadas: list[str] = []

    def anotar_agotado(self, nombre: str) -> None:
        self.agotados[nombre] = self.agotados.get(nombre, 0) + 1
        if self.agotados[nombre] >= self.agotados_max and nombre not in self.desactivadas:
            self.desactivadas.add(nombre)
            self.recien_desactivadas.append(nombre)


# ---------------------------------------------------------------------------
# Aplicacion
# ---------------------------------------------------------------------------
_ESPACIOS = re.compile(r"\s+")


def _clave(valor: str | None) -> str | None:
    if valor is None:
        return None
    return _ESPACIOS.sub(" ", valor).strip()[:256] or None


def _texto_de(el) -> str:
    from shared.texto_visible import _lineas_visibles

    return " ".join(_lineas_visibles(el, forzar_raiz=True))


def _valor_de_nodo(nodo, devuelve: str, atributo: str | None) -> str | None:
    """Un resultado de XPath como texto. Los escalares (`count()`, `string()`,
    `@href`, `text()`) se toman tal cual."""
    if isinstance(nodo, bool):
        return "true" if nodo else "false"
    if isinstance(nodo, float):
        return str(int(nodo)) if nodo.is_integer() else str(nodo)
    if isinstance(nodo, str):
        return _ESPACIOS.sub(" ", nodo).strip()
    if not isinstance(getattr(nodo, "tag", None), str):
        return None  # comentario / PI
    if devuelve == "atributo":
        v = nodo.get(atributo or "")
        return None if v is None else v.strip()
    if devuelve in ("html", "html_interno"):
        from lxml import html as lhtml

        if devuelve == "html":
            return lhtml.tostring(nodo, encoding="unicode", with_tail=False).strip()
        partes = [nodo.text or ""]
        partes += [lhtml.tostring(h, encoding="unicode", with_tail=True) for h in nodo]
        return "".join(partes).strip()
    return _texto_de(nodo)


def _fila(rc: ReglaCompilada, estado: str, render: bool | None, **kw) -> dict:
    fila = {"nombre": rc.nombre, "clase": rc.clase, "estado": estado, "n": None,
            "valor": None, "valor_clave": None, "valor_num": None, "valores": None,
            "truncado": False, "render": render, "detalle": None}
    fila.update(kw)
    return fila


def _fuente(rc: ReglaCompilada, html: str, texto: str | None, contenido: str | None,
            raiz) -> tuple[str, bool]:
    """El texto sobre el que corre una regex o una busqueda, y si se recorto."""
    fuente = rc.regla.get("fuente") or "html"
    if rc.dentro_de is not None and raiz is not None:
        nodos = [n for n in rc.dentro_de(raiz) if isinstance(getattr(n, "tag", None), str)]
        if fuente == "texto":
            s = "\n".join(_texto_de(n) for n in nodos)
        else:
            from lxml import html as lhtml

            s = "\n".join(lhtml.tostring(n, encoding="unicode", with_tail=False) for n in nodos)
    elif fuente == "texto":
        s = texto or ""
    elif fuente == "contenido":
        s = contenido or ""
    else:
        s = html or ""
    if len(s) > FUENTE_MAX:
        return s[:FUENTE_MAX], True
    return s, False


def _aplicar_extractor(rc: ReglaCompilada, raiz, html, texto, contenido, presupuesto,
                       render) -> dict:
    r = rc.regla
    cuantos = r.get("cuantos") or "primero"
    max_car = int(r.get("max_caracteres") or 1000)
    max_val = int(r.get("max_valores") or 20) if cuantos == "todos" else 1
    recortada = False
    if rc.xpath is not None:
        if raiz is None:
            return _fila(rc, "sin_coincidencia", render, n=0)
        resultado = rc.xpath(raiz)
        if not isinstance(resultado, list):
            resultado = [resultado]
        if cuantos == "recuento":
            n = len(resultado)
            if len(resultado) == 1 and isinstance(resultado[0], float):
                n = int(resultado[0])  # `count(...)` ya es el recuento
            return _fila(rc, "ok" if n else "sin_coincidencia", render, n=n, valor=str(n),
                         valor_clave=str(n), valor_num=float(n))
        valores = []
        n = 0
        for nodo in resultado:
            v = _valor_de_nodo(nodo, r.get("devuelve") or "texto", r.get("atributo"))
            if v:
                n += 1
                if len(valores) < max_val:
                    valores.append(v)
    else:
        fuente, recortada = _fuente(rc, html, texto, contenido, raiz)
        valores = []
        n = 0
        for m in rc.rx.finditer(fuente, timeout=presupuesto.timeout_s):
            if m.end() == m.start():
                continue
            v = m.group(rc.grupo) if cuantos != "recuento" else m.group(0)
            if v is None or not v.strip():
                continue
            n += 1
            if cuantos != "recuento" and len(valores) < max_val:
                valores.append(v.strip())
            if n >= MAX_COINCIDENCIAS:
                break
        if cuantos == "recuento":
            if not n and recortada:
                return _fila(rc, "incompleta", render, truncado=True)
            return _fila(rc, "ok" if n else "sin_coincidencia", render, n=n,
                         valor=str(n), valor_clave=str(n), valor_num=float(n),
                         truncado=n >= MAX_COINCIDENCIAS)

    if not n:
        if recortada:
            return _fila(rc, "incompleta", render, truncado=True)
        return _fila(rc, "sin_coincidencia", render, n=0)
    truncado = cuantos == "todos" and n > max_val
    cortados = []
    for v in valores:
        if len(v) > max_car:
            v = v[:max_car]
            truncado = True
        cortados.append(v)
    valor = cortados[0] if cuantos == "primero" else " | ".join(cortados)
    fila = _fila(rc, "ok", render, n=n, valor=valor, valor_clave=_clave(valor),
                 valores=cortados if cuantos == "todos" else None, truncado=truncado)
    if r.get("valor") == "numero":
        num = a_numero(cortados[0], r.get("separador_decimal"))
        if num is None:
            fila["estado"] = "conversion_fallida"
        else:
            fila["valor_num"] = num
    return fila


def _aplicar_busqueda(rc, raiz, html, texto, contenido, presupuesto, render) -> dict:
    fuente, recortada = _fuente(rc, html, texto, contenido, raiz)
    n = 0
    primera = None
    for m in rc.rx.finditer(fuente, timeout=presupuesto.timeout_s):
        if m.end() == m.start():
            continue
        if primera is None:
            primera = m
        n += 1
        if n >= MAX_COINCIDENCIAS:
            break
    if not n:
        if recortada:
            return _fila(rc, "incompleta", render, truncado=True)
        return _fila(rc, "sin_coincidencia", render, n=0)
    lado = max(0, (CONTEXTO - (primera.end() - primera.start())) // 2)
    contexto = fuente[max(0, primera.start() - lado):primera.end() + lado]
    contexto = _ESPACIOS.sub(" ", contexto).strip()[:CONTEXTO]
    return _fila(rc, "ok", render, n=n, valor=contexto, valor_clave=_clave(contexto),
                 valor_num=float(n), truncado=n >= MAX_COINCIDENCIAS)


def aplicar(raiz, html: str, comp: Compiladas, *, texto: str | None = None,
            contenido: str | None = None, ruta: str | None = None,
            titulo: str | None = None, render: bool | None = None,
            presupuesto: Presupuesto | None = None, medir: dict | None = None) -> list[dict]:
    """Aplica las reglas a una pagina. Una fila por regla EVALUADA (las que
    `solo_urls` deja fuera no dan fila). Nunca lanza: un fallo de una regla es
    una fila `error`, no un rastreo caido.

    `raiz`: elemento lxml del documento (`selector.root`). `texto`: el texto
    visible del body. `contenido`: el contenido principal (`page_content`).
    `medir`, si se pasa, recibe los ms de cada regla (lo usa la prueba).
    """
    from shared.descarga_cruda import TITULOS_DE_BLOQUEO

    presupuesto = presupuesto or Presupuesto()
    filas: list[dict] = []
    bloqueada = bool(titulo) and any(t in titulo.strip().lower() for t in TITULOS_DE_BLOQUEO)
    inicio_pagina = time.perf_counter()
    for rc in comp.reglas:
        if rc.solo_urls is not None:
            try:
                if not rc.solo_urls.search(ruta or "/", timeout=presupuesto.timeout_s):
                    continue
            except TimeoutError:
                continue
        if bloqueada:
            filas.append(_fila(rc, "pagina_bloqueada", render))
            continue
        if rc.nombre in presupuesto.desactivadas:
            filas.append(_fila(rc, "desactivada", render))
            continue
        if (time.perf_counter() - inicio_pagina) * 1000 > presupuesto.ms_pagina:
            filas.append(_fila(rc, "presupuesto_pagina", render))
            continue
        t0 = time.perf_counter()
        try:
            if rc.clase == "busqueda":
                fila = _aplicar_busqueda(rc, raiz, html, texto, contenido, presupuesto, render)
            else:
                fila = _aplicar_extractor(rc, raiz, html, texto, contenido, presupuesto, render)
        except TimeoutError:
            presupuesto.anotar_agotado(rc.nombre)
            fila = _fila(rc, "tiempo_agotado", render,
                         detalle=f"la regex paso de {int(presupuesto.timeout_s * 1000)} ms")
        except Exception as exc:  # noqa: BLE001 — una regla rota no tumba la pagina
            fila = _fila(rc, "error", render, detalle=f"{type(exc).__name__}: {exc}"[:200])
        ms = (time.perf_counter() - t0) * 1000
        presupuesto.ms_por_regla[rc.nombre] = presupuesto.ms_por_regla.get(rc.nombre, 0.0) + ms
        if medir is not None:
            medir[rc.nombre] = ms
        filas.append(fila)
    return filas
