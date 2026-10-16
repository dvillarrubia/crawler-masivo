"""Segmentos de negocio definidos por el usuario (Ola 3.3, decision 108).

Un segmento es el nombre de negocio de un grupo de URLs: «fichas de producto»,
«blog», «categorias». Lo decide el consultor con reglas que viajan en
`config.segmentos` (herencia cliente -> perfil -> job de la decision 83: una
LISTA, asi que se sustituye entera, que es lo que tiene que pasar con una
precedencia), y se MATERIALIZA en `urls.segmento` al analizar: filtrar en
Python en cada consulta no escala y no se puede usar desde SQL.

Una regla:

    {"nombre": "fichas", "patron": "^/p/"}                  # como config.templates
    {"nombre": "fichas", "si": {"x": "og_type", "igual": "product"}}
    {"nombre": "blog", "patron": "^/blog/",
     "si": {"no": {"campo": "status_group", "en": ["4xx", "5xx"]}}}

`patron` es una regex sobre el path, sin distinguir mayusculas — el mismo
formato que `config.templates` (`TemplateRule`), asi que una lista de
plantillas se pega tal cual como lista de segmentos. Con `patron` y `si` a la
vez tienen que casar los dos. La PRIMERA regla que casa gana.

Condiciones (`si`), un dict con una sola clase:

- `{"ruta": regex}` / `{"url": regex}`: sobre el path o la URL entera.
- `{"campo": <columna de urls>, <op>: valor}`: `CAMPOS` dice cuales.
- `{"x": <regla a medida>, <op>: valor}`: el valor de la extraccion o la
  busqueda a medida del censo (decision 104, tabla `custom_extraction`).
- `{"todas": [...]}` (Y), `{"alguna": [...]}` (O), `{"no": {...}}` (NO).

Operadores: `igual`, `en` (lista), `gte`, `lte`, `contiene`, `regex`,
`vacio` (bool); en `x` ademas `es` (bool, para una busqueda) y `estado`
(lista de estados de `custom_extraction`, `no_evaluada` incluido).

Lo que no se sabe no se afirma (decisiones 67, 89 y 104): una condicion sobre
un dato que no existe —una regla a medida sin fila para esa URL, una pagina
bloqueada por el WAF, un `indexable` a NULL— no es falsa, es DESCONOCIDA, y se
evalua con logica de tres valores (Kleene). `NO desconocido` sigue siendo
desconocido. Una regla solo asigna su segmento si es VERDADERA; lo desconocido
se cuenta por regla en el resumen para que «0 fichas» no se lea como «no hay
fichas» cuando es «este censo no tiene la extraccion».

Si ninguna regla casa, el segmento es la forma de la ruta
(`shared.plantillas.firma_de_ruta`), que siempre empieza por `/`; por eso un
nombre de regla no puede empezar por `/`: se distingue a la vista que segmento
viene de una regla y cual es el repuesto.

Las reglas solo juzgan PAGINAS DEL SITIO. Antes de mirar ninguna, las URLs
externas van a `SEGMENTO_EXTERNA` y los recursos internos que no son un
documento (imagenes, CSS, JS, fuentes: `TIPOS_RECURSO`) a `SEGMENTO_RECURSO`:
un enlace a `otro.com/blog/` no es del blog del cliente, y una imagen de
`/blog/wp-content/` no es una pagina del blog; contarlas en el segmento
inflaria el denominador de cada porcentaje (decision 108).

Vive en `shared/` porque lo usan el analizador (al terminar `run_all`) y la API
(re-segmentar sin el analisis entero, y validar al guardar la configuracion).
No lee `links`: re-segmentar funciona tambien en un censo con los enlaces
archivados en Parquet (decision 95), que el analisis entero rechaza.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from shared.respuestas import NO_VALIDAS

SEGMENTO_EXTERNA = "(externa)"
SEGMENTO_RECURSO = "(recurso)"
# Lo que no es un documento (decision 42: HTML y PDF, que Google indexa). Los
# saltos (`redirect`), lo bloqueado por robots (`blocked`) y lo que fallo sin
# respuesta (`other`) SI son paginas: tienen su segmento como cualquier otra.
TIPOS_RECURSO = frozenset({"image", "svg", "css", "js", "font"})
MAX_REGLAS = 200
MAX_PROFUNDIDAD = 6
MAX_HOJAS_POR_REGLA = 50
MAX_NOMBRE = 80
TIMEOUT_REGEX_S = 0.05
LOTE = 5000

# Columnas de `urls` sobre las que se puede escribir una condicion, con su tipo.
CAMPOS: dict[str, str] = {
    "url": "texto", "path": "texto", "host": "texto",
    "status_code": "numero", "status_group": "texto", "status_text": "texto",
    "content_type": "texto", "resource_type": "texto", "response_class": "texto",
    "indexable": "bool", "noindex": "bool", "is_internal": "bool", "in_sitemap": "bool",
    "is_html": "bool", "blocked_by_robots": "bool",
    "indexability_status": "texto",
    "crawl_depth": "numero", "click_depth": "numero", "folder_depth": "numero",
    "url_length": "numero", "word_count": "numero", "content_word_count": "numero",
    "inlinks_count": "numero", "unique_inlinks_count": "numero", "outlinks_count": "numero",
    "pagerank_score": "numero", "redirect_type": "numero", "response_time_ms": "numero",
}

# Lo que se sabe de una URL aunque la respuesta fuera un muro (decision 103):
# su direccion y la propia clase de respuesta. Todo lo demas (codigo, palabras,
# indexabilidad...) es del muro, no de la pagina, y una condicion sobre ello es
# DESCONOCIDA: la pagina no se llego a ver.
CAMPOS_FIABLES_CON_MURO = frozenset({"url", "path", "host", "is_internal", "response_class"})

# Columnas en las que NULL es un dato y no un «no se sabe»: el spider deja
# `redirect_type` a None en toda respuesta que no es un salto. Con una
# respuesta que no es 3xx, NULL = «no redirige», conocido; sin respuesta
# (bloqueada, error de red) o en un salto sin codigo (JS), sigue sin saberse.
def _null_conocido(campo: str, fila: dict) -> bool:
    if campo == "redirect_type":
        codigo = fila.get("status_code")
        return codigo is not None and not 300 <= codigo < 400
    return False

_OPS_POR_TIPO = {
    "texto": {"igual", "en", "contiene", "regex", "vacio"},
    "numero": {"igual", "en", "gte", "lte", "vacio"},
    "bool": {"igual", "vacio"},
}
_OPS_X = {"igual", "en", "gte", "lte", "contiene", "regex", "vacio", "es", "estado"}
_CLASES = ("ruta", "url", "campo", "x", "todas", "alguna", "no")
_EVALUADOS = ("ok", "sin_coincidencia", "conversion_fallida")
ESTADOS_X = {"ok", "sin_coincidencia", "incompleta", "conversion_fallida", "tiempo_agotado",
             "presupuesto_pagina", "desactivada", "pagina_bloqueada", "error", "no_evaluada"}


def _compilar_regex(patron: str):
    import regex

    return regex.compile(patron, regex.IGNORECASE)


# ---------------------------------------------------------------------------
# Validacion: la llama JobConfig (al crear el job o guardar un cliente/perfil)
# y el endpoint de re-segmentar. Devuelve errores con su ruta, no lanza.
# ---------------------------------------------------------------------------
def validar(reglas: list[dict] | None, nombres_x: dict[str, str] | None = None) -> list[str]:
    """`nombres_x`: nombre -> clase (`extractor`/`busqueda`) de las reglas a
    medida del config ENTERO; una condicion `x` sobre otra es un error, porque
    nunca casaria y se leeria como «no hay ninguna».

    `nombres_x=None` = no se conocen todavia: una capa de la herencia (cliente,
    perfil, el override de un job) puede usar una extraccion que fija otra
    capa, asi que ahi se valida la forma y el nombre se comprueba sobre la
    configuracion FUSIONADA (`errores_de_config`)."""
    errores: list[str] = []
    reglas = reglas or []
    if len(reglas) > MAX_REGLAS:
        return [f"segmentos: como mucho {MAX_REGLAS} reglas (hay {len(reglas)})"]
    for i, r in enumerate(reglas):
        donde = f"segmentos[{i}]"
        if not isinstance(r, dict):
            errores.append(f"{donde}: tiene que ser un objeto")
            continue
        nombre = r.get("nombre")
        if not isinstance(nombre, str) or not nombre.strip():
            errores.append(f"{donde}: falta el nombre")
        else:
            if len(nombre) > MAX_NOMBRE:
                errores.append(f"{donde}: el nombre pasa de {MAX_NOMBRE} caracteres")
            if nombre.strip().startswith(("/", "(")):
                errores.append(f"{donde}: un nombre no puede empezar por '/' ni '(' "
                               f"(los reserva el segmento por defecto, la forma de la ruta)")
        sobran = set(r) - {"nombre", "patron", "si", "etiqueta"}
        if sobran:
            errores.append(f"{donde}: claves desconocidas {sorted(sobran)}")
        if r.get("patron") in (None, "") and r.get("si") is None:
            errores.append(f"{donde}: hace falta `patron` o `si`")
        if r.get("patron") not in (None, ""):
            errores += _validar_regex(r["patron"], f"{donde}.patron")
        if r.get("si") is not None:
            hojas = [0]
            errores += _validar_cond(r["si"], f"{donde}.si", 1, nombres_x, hojas)
            if hojas[0] > MAX_HOJAS_POR_REGLA:
                errores.append(f"{donde}: como mucho {MAX_HOJAS_POR_REGLA} condiciones por regla")
    return errores


def _validar_regex(patron, donde: str) -> list[str]:
    if not isinstance(patron, str) or not patron:
        return [f"{donde}: tiene que ser una regex no vacia"]
    if len(patron) > 500:
        return [f"{donde}: la regex pasa de 500 caracteres"]
    try:
        _compilar_regex(patron)
    except Exception as exc:  # noqa: BLE001 — regex.error y compania
        return [f"{donde}: regex no valida: {exc}"]
    return []


def _validar_cond(c, donde: str, nivel: int, nombres_x: dict, hojas: list[int]) -> list[str]:
    if nivel > MAX_PROFUNDIDAD:
        return [f"{donde}: anidado mas de {MAX_PROFUNDIDAD} niveles"]
    if not isinstance(c, dict):
        return [f"{donde}: tiene que ser un objeto"]
    clases = [k for k in _CLASES if k in c]
    if len(clases) != 1:
        return [f"{donde}: lleva exactamente una de {list(_CLASES)}"]
    clase = clases[0]
    if clase in ("todas", "alguna"):
        if set(c) != {clase}:
            return [f"{donde}: `{clase}` va solo"]
        if not isinstance(c[clase], list) or not c[clase]:
            return [f"{donde}.{clase}: una lista no vacia de condiciones"]
        errores: list[str] = []
        for j, sub in enumerate(c[clase]):
            errores += _validar_cond(sub, f"{donde}.{clase}[{j}]", nivel + 1, nombres_x, hojas)
        return errores
    if clase == "no":
        if set(c) != {"no"}:
            return [f"{donde}: `no` va solo"]
        return _validar_cond(c["no"], f"{donde}.no", nivel + 1, nombres_x, hojas)
    hojas[0] += 1
    if clase in ("ruta", "url"):
        if set(c) != {clase}:
            return [f"{donde}: `{clase}` va solo (es la regex)"]
        return _validar_regex(c[clase], f"{donde}.{clase}")
    ops = set(c) - {clase}
    if len(ops) != 1:
        return [f"{donde}: lleva exactamente un operador"]
    op = ops.pop()
    v = c[op]
    if clase == "campo":
        tipo = CAMPOS.get(c["campo"])
        if tipo is None:
            return [f"{donde}: campo desconocido {c['campo']!r}; validos: {sorted(CAMPOS)}"]
        if op not in _OPS_POR_TIPO[tipo]:
            return [f"{donde}: `{op}` no vale para {c['campo']} ({tipo}); "
                    f"validos: {sorted(_OPS_POR_TIPO[tipo])}"]
    else:
        nombre = c["x"]
        if not isinstance(nombre, str) or not nombre:
            return [f"{donde}: `x` es el nombre de una regla a medida"]
        if op not in _OPS_X:
            return [f"{donde}: operador desconocido {op!r}; validos: {sorted(_OPS_X)}"]
        tipo = "numero" if op in ("gte", "lte") else "texto"
        if nombres_x is None:
            return _validar_valor(op, v, tipo, donde)
        if nombre not in nombres_x:
            return [f"{donde}: este config no tiene la regla a medida {nombre!r} "
                    f"(custom_extraction / custom_search)"]
        if op == "es" and nombres_x[nombre] != "busqueda":
            return [f"{donde}: `es` solo vale para una busqueda; {nombre!r} es un extractor"]
    return _validar_valor(op, v, tipo, donde)


def _validar_valor(op: str, v, tipo: str, donde: str) -> list[str]:
    def es_num(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool)

    if op in ("vacio", "es"):
        return [] if isinstance(v, bool) else [f"{donde}: `{op}` es true o false"]
    if op in ("gte", "lte"):
        return [] if es_num(v) else [f"{donde}: `{op}` necesita un numero"]
    if op == "regex":
        return _validar_regex(v, f"{donde}.regex")
    if op == "contiene":
        return [] if isinstance(v, str) and v else [f"{donde}: `contiene` necesita un texto"]
    if op == "estado":
        if not isinstance(v, list) or not v or any(e not in ESTADOS_X for e in v):
            return [f"{donde}: `estado` es una lista de {sorted(ESTADOS_X)}"]
        return []
    valores = v if op == "en" else [v]
    if op == "en" and (not isinstance(v, list) or not v):
        return [f"{donde}: `en` necesita una lista no vacia"]
    for x in valores:
        ok = (isinstance(x, bool) if tipo == "bool" else es_num(x) if tipo == "numero"
              else isinstance(x, (str, int, float)) and not isinstance(x, bool))
        if not ok:
            return [f"{donde}: {x!r} no es un valor {tipo}"]
    return []


# ---------------------------------------------------------------------------
# Compilacion y evaluacion con logica de tres valores
# ---------------------------------------------------------------------------
Cond = Callable[[dict], "bool | None"]


@dataclass
class _Contadores:
    regex_agotadas: int = 0


def _regex_busca(rx, texto: str, cont: _Contadores) -> bool | None:
    try:
        return rx.search(texto, timeout=TIMEOUT_REGEX_S) is not None
    except TimeoutError:
        cont.regex_agotadas += 1
        return None


def _norm(v):
    return v.strip().casefold() if isinstance(v, str) else v


def _valor_campo(fila: dict, campo: str):
    v = fila.get(campo)
    if campo == "content_type" and isinstance(v, str):
        # `text/html; charset=utf-8` -> `text/html`: la condicion la escribe
        # alguien pensando en el tipo, no en el juego de caracteres.
        v = v.split(";")[0].strip() or None
    return v


def _op_sobre_valor(op: str, esperado, v, cont: _Contadores, rx=None) -> bool | None:
    """`v` ya conocido (puede ser None = conocido y vacio)."""
    if op == "vacio":
        return (v is None or (isinstance(v, str) and not v.strip())) == esperado
    if v is None:
        # Conocido y sin valor: no es igual a nada, no contiene nada.
        return False
    if op == "igual":
        return _norm(v) == _norm(esperado) if not isinstance(v, bool) else v is esperado
    if op == "en":
        return _norm(v) in {_norm(e) for e in esperado}
    if op in ("gte", "lte"):
        try:
            n = float(v)
        except (TypeError, ValueError):
            return None
        return n >= esperado if op == "gte" else n <= esperado
    if op == "contiene":
        return _norm(esperado) in _norm(str(v))
    if op == "regex":
        return _regex_busca(rx, str(v), cont)
    return None


def _compilar_cond(c: dict, cont: _Contadores) -> Cond:
    if "todas" in c:
        return _y([_compilar_cond(s, cont) for s in c["todas"]])
    if "alguna" in c:
        subs = [_compilar_cond(s, cont) for s in c["alguna"]]

        def alguna(f):
            hay_none = False
            for s in subs:
                r = s(f)
                if r is True:
                    return True
                if r is None:
                    hay_none = True
            return None if hay_none else False
        return alguna
    if "no" in c:
        sub = _compilar_cond(c["no"], cont)

        def no(f):
            r = sub(f)
            return None if r is None else not r
        return no
    if "ruta" in c or "url" in c:
        clave = "ruta" if "ruta" in c else "url"
        rx = _compilar_regex(c[clave])
        col = "path" if clave == "ruta" else "url"
        return lambda f: _regex_busca(rx, f.get(col) or ("/" if col == "path" else ""), cont)
    if "campo" in c:
        campo = c["campo"]
        op = next(k for k in c if k != "campo")
        esperado = c[op]
        rx = _compilar_regex(esperado) if op == "regex" else None

        def por_campo(f):
            if campo not in CAMPOS_FIABLES_CON_MURO and f.get("response_class") in NO_VALIDAS:
                return None  # un muro no es la pagina (decision 103)
            v = _valor_campo(f, campo)
            if v is None and op != "vacio" and not _null_conocido(campo, f):
                return None  # NULL: no se sabe (indexable sin decidir, sin sitemap...)
            return _op_sobre_valor(op, esperado, v, cont, rx)
        return por_campo
    nombre = c["x"]
    op = next(k for k in c if k != "x")
    esperado = c[op]
    rx = _compilar_regex(esperado) if op == "regex" else None

    def por_x(f):
        fila = (f.get("_x") or {}).get(nombre)
        estado = fila["estado"] if fila else "no_evaluada"
        if op == "estado":
            return estado in esperado
        if estado not in _EVALUADOS:
            return None  # sin fila, bloqueada, error: no se sabe
        if op == "es":
            if estado == "conversion_fallida":
                return None
            return (estado == "ok") == esperado
        if estado == "sin_coincidencia":
            return _op_sobre_valor(op, esperado, None, cont)
        numerico = op in ("gte", "lte") or (
            op in ("igual", "en") and all(isinstance(e, (int, float)) and not isinstance(e, bool)
                                          for e in (esperado if op == "en" else [esperado])))
        if numerico:
            num = fila.get("n") if fila.get("clase") == "busqueda" else fila.get("valor_num")
            if num is None:
                return None  # conversion fallida: hay texto pero no numero
            return _op_sobre_valor(op, esperado, num, cont)
        return _op_sobre_valor(op, esperado, fila.get("valor"), cont, rx)
    return por_x


def _y(subs: list[Cond]) -> Cond:
    def todas(f):
        hay_none = False
        for s in subs:
            r = s(f)
            if r is False:
                return False
            if r is None:
                hay_none = True
        return None if hay_none else True
    return todas


@dataclass
class Segmentador:
    reglas: list[dict]
    cont: _Contadores = field(default_factory=_Contadores)

    def __post_init__(self):
        self._compiladas: list[tuple[str, Cond]] = []
        for r in self.reglas:
            partes = []
            if r.get("patron") not in (None, ""):
                partes.append(_compilar_cond({"ruta": r["patron"]}, self.cont))
            if r.get("si") is not None:
                partes.append(_compilar_cond(r["si"], self.cont))
            self._compiladas.append((r["nombre"].strip(), partes[0] if len(partes) == 1
                                     else _y(partes)))
        self.casadas = [0] * len(self._compiladas)
        self.desconocidas = [0] * len(self._compiladas)
        self.por_defecto = 0
        self.externas = 0
        self.recursos = 0

    @property
    def nombres_x(self) -> set[str]:
        salida: set[str] = set()

        def recorrer(c):
            if not isinstance(c, dict):
                return
            if "x" in c:
                salida.add(c["x"])
            for k in ("todas", "alguna"):
                for s in c.get(k) or []:
                    recorrer(s)
            if "no" in c:
                recorrer(c["no"])
        for r in self.reglas:
            recorrer(r.get("si"))
        return salida

    def segmento(self, fila: dict) -> str:
        """Externas y recursos aparte, ANTES de las reglas: no son paginas del
        sitio. Luego la primera regla VERDADERA gana; lo desconocido no gana y
        se cuenta."""
        if fila.get("is_internal") is False:
            self.externas += 1
            return SEGMENTO_EXTERNA
        if fila.get("resource_type") in TIPOS_RECURSO:
            self.recursos += 1
            return SEGMENTO_RECURSO
        for i, (nombre, cond) in enumerate(self._compiladas):
            r = cond(fila)
            if r is True:
                self.casadas[i] += 1
                return nombre
            if r is None:
                self.desconocidas[i] += 1
        from shared.plantillas import firma_de_ruta

        self.por_defecto += 1
        return firma_de_ruta(fila.get("path") or "/")


def huella(reglas: list[dict] | None) -> str:
    """Identifica el juego de reglas con el que se materializo `urls.segmento`."""
    return hashlib.sha256(json.dumps(reglas or [], sort_keys=True, ensure_ascii=False)
                          .encode()).hexdigest()[:16]


def reglas_de_config(config: dict | None) -> list[dict]:
    return [r for r in ((config or {}).get("segmentos") or []) if isinstance(r, dict)]


def errores_de_config(config: dict | None) -> list[str]:
    """Los segmentos de una configuracion COMPLETA (fusionada o la de un job
    sin cliente) contra sus reglas a medida: la comprobacion que una capa
    suelta no puede hacer."""
    return validar(reglas_de_config(config), nombres_a_medida(config))


def nombres_a_medida(config: dict | None) -> dict[str, str]:
    config = config or {}
    salida = {}
    for clase, clave in (("extractor", "custom_extraction"), ("busqueda", "custom_search")):
        for nombre, regla in (config.get(clave) or {}).items():
            if isinstance(regla, dict):
                salida[nombre] = clase
    return salida


# ---------------------------------------------------------------------------
# Materializacion
# ---------------------------------------------------------------------------
def segmentar(session, job_id, reglas: list[dict] | None = None, *, lote: int = LOTE) -> dict:
    """Escribe `urls.segmento` en TODAS las URLs del job y devuelve el resumen
    (que tambien queda en `jobs.segmentacion`). No hace commit.

    `reglas=None` lee las del propio job (`jobs.config.segmentos`). Recorre las
    URLs por tramos y escribe agrupando por segmento: un UPDATE por (tramo,
    segmento) y no uno por URL.

    El tramo va por `(job_id, url_hash)`, el indice unico `uq_job_url`, y NO
    por `id`: no hay indice `(job_id, id)`, asi que `id > :ultimo ORDER BY id`
    hacia que Postgres recorriese `urls_pkey` —la clave de TODOS los censos—
    filtrando por job, y el coste crecia con la tabla entera y no con el censo.
    Medido en una base sintetica de 6 M de filas (100 censos de 60.000): ver la
    decision 108.
    """
    from sqlalchemy import select, update

    from shared.models import CustomExtraction, Job, Url
    from shared.version import VERSION

    inicio = time.time()
    job = session.get(Job, job_id)
    if reglas is None:
        reglas = reglas_de_config(job.config if job else None)
    seg = Segmentador(reglas)
    nombres_x = sorted(seg.nombres_x)
    cols = [getattr(Url, c) for c in CAMPOS] + [Url.id, Url.url_hash]
    ultimo = ""
    total = 0
    distintos: dict[str, int] = {}
    while True:
        filas = session.execute(
            select(*cols).where(Url.job_id == job_id, Url.url_hash > ultimo)
            .order_by(Url.url_hash).limit(lote)
        ).mappings().all()
        if not filas:
            break
        ids = [f["id"] for f in filas]
        ultimo = filas[-1]["url_hash"]
        xs: dict[int, dict] = {}
        if nombres_x:
            CE = CustomExtraction
            for r in session.execute(
                select(CE.url_id, CE.nombre, CE.clase, CE.estado, CE.n, CE.valor, CE.valor_num)
                .where(CE.job_id == job_id, CE.nombre.in_(nombres_x), CE.url_id.in_(ids))
            ).mappings():
                xs.setdefault(r["url_id"], {})[r["nombre"]] = dict(r)
        por_segmento: dict[str, list[int]] = {}
        for f in filas:
            d = dict(f)
            d["_x"] = xs.get(f["id"])
            por_segmento.setdefault(seg.segmento(d), []).append(f["id"])
        for s, ids_s in por_segmento.items():
            session.execute(update(Url).where(Url.id.in_(ids_s)).values(segmento=s)
                            .execution_options(synchronize_session=False))
            distintos[s] = distintos.get(s, 0) + len(ids_s)
        total += len(filas)

    por_regla: list[dict] = []
    for i, r in enumerate(reglas):
        por_regla.append({"nombre": r["nombre"].strip(), "urls": seg.casadas[i],
                          "sin_saber": seg.desconocidas[i]})
    resumen = {
        "reglas": len(reglas),
        "huella": huella(reglas),
        "urls": total,
        "por_regla": por_regla,
        "por_defecto": seg.por_defecto,
        "externas": seg.externas,
        "recursos": seg.recursos,
        "segmentos_distintos": len(distintos),
        "regex_agotadas": seg.cont.regex_agotadas,
        "segundos": round(time.time() - inicio, 2),
        "version": VERSION,
        "cuando": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    session.execute(update(Job).where(Job.id == job_id).values(segmentacion=resumen)
                    .execution_options(synchronize_session=False))
    return resumen
