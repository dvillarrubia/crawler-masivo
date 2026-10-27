"""Revision humana de lo que respondio Jev (#8, decision 211).

Lo comparten la API (la pantalla de revision) y `scripts/evaluar_decisiones.py`
(`etiquetas`, que saca un juego y sus respuestas de lo etiquetado para
`evaluar`). No llama a ningun modelo: solo lee `decisions` y escribe
`decision_labels`.

Lo que se sujeta:

- **A ciegas.** Lo que se revisa es lo que VIO el modelo (`estado_enviado`), y
  la respuesta de Jev no viaja hasta que la persona ha etiquetado: verla antes
  empuja la etiqueta hacia ella (decision 179).
- **Un estado, una etiqueta.** El mismo estado en dos URLs o dos censos es la
  misma entrada para Jev (decision 169): se revisa una vez, y la etiqueta vale
  para todas sus copias.
- **Lo que se ofrece es lo que mide la pregunta de hoy**: su version y su
  modelo del catalogo, y con las opciones de su cliente las mismas que vio Jev
  (si el cliente cambio su taxonomia, la respuesta vieja se eligio entre otras
  opciones y no se puede comparar; el mismo filtro que la cache, decision 178).
  Una pregunta con `solo_si` (la recomendacion de una canibalizacion, 175),
  solo donde la otra respondio lo que pide.
- **Sin sesgo de orden.** Se ofrece por la clave del estado (un hash, asi que
  un orden pseudoaleatorio estable), no por antiguedad: por `id` salian
  primero los censos viejos, agrupados por sitio, y `evaluar` trata el juego
  como una muestra.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, exists, func
from sqlalchemy.orm import aliased

# Las decisiones que se ofrecen a revisar: las que respondio el modelo (o la
# cache, que es una respuesta suya anterior). Las de regla, arrastre o humano
# no son de Jev.
ORIGENES_DE_JEV = ("modelo", "cache")
_LOTE = 200


class EtiquetaInvalida(ValueError):
    pass


class NoRevisable(LookupError):
    pass


def modulo_de(pregunta: str):
    """El modulo de `shared.enriquecido` que hace la pregunta, o None."""
    from shared.enriquecido import REGISTRO

    for m in REGISTRO.values():
        if pregunta in m.preguntas:
            return m
    return None


def pregunta_de(db, decision):
    """La `Pregunta` con la que hoy se juzgaria la decision, o None si ya no
    sirve para medirla: fuera del catalogo, otra version u otro modelo, o (con
    las opciones del cliente, decision 178) un cliente sin taxonomia o con una
    distinta de la que vio Jev."""
    from shared.models import Job
    from shared.preguntas import CATALOGO

    p = CATALOGO.get(decision.pregunta)
    if p is None or decision.version_pregunta != p.version or decision.modelo != p.modelo:
        return None
    if not p.opciones_del_cliente:
        return p
    modulo = modulo_de(decision.pregunta)
    job = db.get(Job, decision.job_id) if decision.job_id else None
    if modulo is None or job is None:
        return None
    for pc in modulo.preguntas_para(db, job):
        if pc.nombre == p.nombre and pc.variante:
            if set(decision.probabilidades or {}) != set(pc.opciones):
                return None
            return pc
    return None


def _etiquetada(Decision, DecisionLabel):
    return exists().where(and_(DecisionLabel.clave == Decision.clave,
                               DecisionLabel.pregunta == Decision.pregunta,
                               DecisionLabel.version_pregunta == Decision.version_pregunta))


def _aplica(Decision, pregunta):
    """La condicion `solo_si` sobre la MISMA ejecucion y el mismo estado."""
    otra, valor = pregunta.solo_si
    c = aliased(Decision)
    return exists().where(c.run_id == Decision.run_id, c.clave == Decision.clave,
                          c.url_id == Decision.url_id, c.pregunta == otra, c.respuesta == valor)


def _consulta(db, p, *, job_id=None, estado: str | None = "incierto"):
    """Las decisiones de Jev de la pregunta `p` (del catalogo) que se pueden
    revisar. Sin etiquetar, en SQL, salvo las de opciones del cliente: ahi la
    etiqueta es por variante y `decisions` no la guarda (se mira despues)."""
    from shared.models import Decision, DecisionLabel

    q = (db.query(Decision)
         .filter(Decision.pregunta == p.nombre, Decision.version_pregunta == p.version,
                 Decision.modelo == p.modelo, Decision.origen.in_(ORIGENES_DE_JEV),
                 Decision.estado_enviado.isnot(None), Decision.respuesta.isnot(None)))
    if estado:
        q = q.filter(Decision.estado == estado)
    if job_id:
        q = q.filter(Decision.job_id == job_id)
    if p.opciones_del_cliente:
        # Sin censo no hay cliente, ni taxonomia con la que ensenarla.
        q = q.filter(Decision.job_id.isnot(None))
    else:
        q = q.filter(~_etiquetada(Decision, DecisionLabel))
    if p.solo_si is not None:
        q = q.filter(_aplica(Decision, p))
    return q


def _ya_etiquetada(db, d, p) -> bool:
    from shared.models import DecisionLabel

    return db.query(DecisionLabel.id).filter(
        DecisionLabel.clave == d.clave, DecisionLabel.pregunta == d.pregunta,
        DecisionLabel.version_pregunta == d.version_pregunta,
        DecisionLabel.variante == (p.variante or "")).first() is not None


def pendientes(db, *, pregunta: str, job_id=None, estado: str | None = "incierto",
               limite: int = 20) -> list[dict[str, Any]]:
    """Lo que falta por revisar, una fila por estado, SIN la respuesta de Jev.
    Por la clave del estado (orden pseudoaleatorio estable), con cursor."""
    from shared.models import Decision, Url
    from shared.preguntas import CATALOGO

    p = CATALOGO.get(pregunta)
    if p is None:
        return []
    vistas: set[str] = set()
    salida: list[dict[str, Any]] = []
    tras: tuple[str, int] | None = None
    while len(salida) < limite:
        q = _consulta(db, p, job_id=job_id, estado=estado)
        if tras is not None:
            q = q.filter((Decision.clave > tras[0]) | and_(Decision.clave == tras[0], Decision.id > tras[1]))
        filas = q.order_by(Decision.clave, Decision.id).limit(_LOTE).all()
        if not filas:
            break
        tras = (filas[-1].clave, filas[-1].id)
        for d in filas:
            if d.clave in vistas:
                continue
            pc = pregunta_de(db, d)
            if pc is None or (pc.opciones_del_cliente and _ya_etiquetada(db, d, pc)):
                continue
            vistas.add(d.clave)
            url = db.query(Url.url).filter(Url.id == d.url_id).scalar() if d.url_id else None
            salida.append({
                "decision_id": d.id, "pregunta": pc.nombre, "version": pc.version,
                "tipo": pc.tipo, "instrucciones": pc.instrucciones, "descripcion": pc.descripcion,
                "opciones": opciones_para_pantalla(pc), "url": url,
                "job_id": str(d.job_id) if d.job_id else None, "ambito": d.ambito,
                "estado": d.estado_enviado,
            })
            if len(salida) >= limite:
                break
    return salida


def opciones_para_pantalla(p) -> list[dict[str, str]]:
    """Las respuestas posibles, con el valor que se guarda y su texto."""
    if p.tipo == "noul":
        criterio = p.opciones or {}
        return [{"valor": "true", "texto": "Sí", "detalle": criterio.get("true") or ""},
                {"valor": "false", "texto": "No", "detalle": criterio.get("false") or ""}]
    if p.tipo == "score":
        nombres = list(p.etiquetas) if len(p.etiquetas) == len(p.opciones) else None
        return [{"valor": str(i), "texto": (nombres[i] if nombres else str(i)), "detalle": str(nivel)}
                for i, nivel in enumerate(p.opciones)]
    return [{"valor": str(k), "texto": str(k), "detalle": str(v or "")} for k, v in (p.opciones or {}).items()]


def etiquetar(db, decision_id: int, etiqueta, *, nota: str | None = None,
              autor: str | None = None) -> dict[str, Any]:
    """Guarda (o corrige) la etiqueta humana del estado de la decision.

    Devuelve lo que dijo Jev para ensenarlo DESPUES. Sin commit: lo hace quien
    llama. `EtiquetaInvalida` si no esta en el vocabulario de la pregunta;
    `NoRevisable` si la decision no existe o no es algo que se revise (de
    regla, de otra version o modelo, con una taxonomia que ya no es la suya).
    """
    from shared.evaluacion_decisiones import etiqueta_de_respuesta, normalizar_etiqueta
    from shared.models import Decision, DecisionLabel

    d = db.get(Decision, decision_id)
    if d is None:
        raise LookupError("decision no encontrada")
    if d.origen not in ORIGENES_DE_JEV or d.respuesta is None:
        raise NoRevisable("no es una respuesta de Jev")
    p = pregunta_de(db, d)
    if p is None:
        raise NoRevisable("ya no se puede medir con la pregunta de hoy (otra version, otro modelo "
                          "u otras opciones del cliente)")
    valor = normalizar_etiqueta(p.tipo, etiqueta, p.opciones)
    if valor is None:
        raise EtiquetaInvalida(f"«{etiqueta}» no es una respuesta de {p.nombre}")
    variante = p.variante or ""
    fila = (db.query(DecisionLabel)
            .filter(DecisionLabel.clave == d.clave, DecisionLabel.pregunta == d.pregunta,
                    DecisionLabel.version_pregunta == d.version_pregunta,
                    DecisionLabel.variante == variante)
            .one_or_none())
    if fila is None:
        fila = DecisionLabel(clave=d.clave, pregunta=d.pregunta, version_pregunta=d.version_pregunta,
                             variante=variante)
        db.add(fila)
    fila.decision_id = d.id
    fila.ambito = d.ambito
    fila.estado_enviado = d.estado_enviado
    fila.opciones = dict(p.opciones) if p.opciones_del_cliente else None
    fila.respuesta_jev = d.respuesta
    fila.confianza_jev = d.confianza
    fila.modelo_jev = d.modelo
    fila.etiqueta = valor
    fila.nota = (nota or "").strip() or None
    fila.autor = (autor or "").strip()[:128] or None
    niveles = len(p.opciones) if p.tipo == "score" else None
    jev = etiqueta_de_respuesta(p.tipo, d.respuesta, niveles)
    return {"etiqueta": valor, "jev": jev, "confianza": d.confianza, "coincide": jev == valor}


def resumen(db) -> list[dict[str, Any]]:
    """Por pregunta (version del catalogo): cuanto falta por revisar, cuanto
    esta etiquetado y en cuanto coincide Jev. Con las opciones del cliente,
    `por_revisar` es aproximado (no sabe que estados ya tienen etiqueta con su
    variante) y lo etiquetado va tambien por variante, que es como se exporta."""
    from shared.evaluacion_decisiones import etiqueta_de_respuesta
    from shared.models import Decision, DecisionLabel
    from shared.preguntas import CATALOGO

    salida = []
    for nombre, p in sorted(CATALOGO.items()):
        por_revisar = (_consulta(db, p, estado="incierto")
                       .with_entities(func.count(func.distinct(Decision.clave))).scalar() or 0)
        etiquetas = (db.query(DecisionLabel.etiqueta, DecisionLabel.respuesta_jev, DecisionLabel.variante)
                     .filter(DecisionLabel.pregunta == nombre, DecisionLabel.version_pregunta == p.version)
                     .all())
        if not por_revisar and not etiquetas:
            continue
        niveles = len(p.opciones) if p.tipo == "score" else None
        coinciden = sum(1 for e, r, _ in etiquetas if etiqueta_de_respuesta(p.tipo, r, niveles) == e)
        variantes: dict[str, int] = {}
        for _, _, v in etiquetas:
            variantes[v or ""] = variantes.get(v or "", 0) + 1
        salida.append({"pregunta": nombre, "version": p.version, "descripcion": p.descripcion,
                       "por_revisar": int(por_revisar), "etiquetadas": len(etiquetas),
                       "coinciden_con_jev": coinciden, "umbral": p.umbral, "ejemplo": p.ejemplo,
                       "opciones_del_cliente": p.opciones_del_cliente,
                       "variantes": [{"variante": v, "etiquetadas": n} for v, n in sorted(variantes.items())]})
    return salida


def exportar(db, pregunta: str, variante: str | None = None) -> tuple[list[dict], list[dict]]:
    """(juego, respuestas) de lo etiquetado, con la forma de `extraer` y de
    `responder` de `scripts/evaluar_decisiones.py`, para pasarlo a `evaluar`.

    Solo la version del catalogo. Con las opciones del cliente, un juego por
    variante (taxonomia): `evaluar` normaliza todas las filas con las opciones
    de la primera, y dos clientes juntos contarian unas contra el vocabulario
    del otro. Las respuestas, solo las del modelo del catalogo: la etiqueta
    humana vale para cualquiera, la respuesta no (sin ella la fila cuenta como
    no evaluable, y `responder` la vuelve a preguntar).
    """
    from shared.models import DecisionLabel
    from shared.preguntas import CATALOGO

    p = CATALOGO.get(pregunta)
    if p is None:
        raise LookupError(f"{pregunta} no esta en el catalogo")
    q = db.query(DecisionLabel).filter(DecisionLabel.pregunta == pregunta,
                                       DecisionLabel.version_pregunta == p.version)
    if p.opciones_del_cliente:
        hay = sorted({v for (v,) in q.with_entities(DecisionLabel.variante).distinct()})
        if variante is None:
            if len(hay) > 1:
                raise ValueError(f"{pregunta} tiene etiquetas de {len(hay)} taxonomias: un juego por "
                                 f"variante ({', '.join(hay)})")
            variante = hay[0] if hay else ""
        q = q.filter(DecisionLabel.variante == variante)
    juego, respuestas = [], []
    for f in q.order_by(DecisionLabel.id).all():
        ident = f"{f.ambito}|{f.clave}|{f.variante}" if f.variante else f"{f.ambito}|{f.clave}"
        juego.append({"id": ident, "ambito": f.ambito, "pregunta": pregunta, "version": p.version,
                      "estado": f.estado_enviado, "etiqueta": f.etiqueta,
                      "etiquetado_por": f.autor or "revision", "nota": f.nota or "",
                      "origen_etiqueta": "revision",
                      **({"opciones": f.opciones} if f.opciones else {})})
        if f.respuesta_jev is not None and (f.modelo_jev or p.modelo) == p.modelo:
            respuestas.append({"id": ident, "pregunta": pregunta, "version": p.version,
                               "respuesta": f.respuesta_jev, "confianza": f.confianza_jev,
                               "modelo": f.modelo_jev})
    return juego, respuestas


def jsonl(filas: list[dict]) -> str:
    return "".join(json.dumps(f, ensure_ascii=False, sort_keys=True) + "\n" for f in filas)
