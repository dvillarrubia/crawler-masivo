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
- **Lo que no aplica no se revisa.** Una pregunta con `solo_si` (la
  recomendacion de una canibalizacion, decision 175) solo donde la otra
  respondio lo que pide.
- **Una version nueva se etiqueta otra vez** (decision 180): la etiqueta es
  de (estado, pregunta, version, opciones).
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
# Cuantas filas se leen por cada una que se devuelve: varias copias del mismo
# estado salen juntas y se quedan en una.
_LOTE_POR_FILA = 5


class EtiquetaInvalida(ValueError):
    pass


def modulo_de(pregunta: str):
    """El modulo de `shared.enriquecido` que hace la pregunta, o None."""
    from shared.enriquecido import REGISTRO

    for m in REGISTRO.values():
        if pregunta in m.preguntas:
            return m
    return None


def pregunta_de(db, decision):
    """La `Pregunta` con la que se juzgo la decision, con las opciones de su
    cliente si son suyas (decision 178). None si ya no esta en el catalogo."""
    from shared.models import Job
    from shared.preguntas import CATALOGO

    p = CATALOGO.get(decision.pregunta)
    if p is None or not p.opciones_del_cliente:
        return p
    modulo = modulo_de(decision.pregunta)
    job = db.get(Job, decision.job_id) if decision.job_id else None
    if modulo is None or job is None:
        return None
    for pc in modulo.preguntas_para(db, job):
        if pc.nombre == p.nombre:
            return pc if pc.variante else None
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


def _consulta(db, *, pregunta: str | None, job_id, estado: str | None):
    from shared.models import Decision, DecisionLabel
    from shared.preguntas import CATALOGO

    q = (db.query(Decision)
         .filter(Decision.origen.in_(ORIGENES_DE_JEV), Decision.estado_enviado.isnot(None),
                 Decision.respuesta.isnot(None), ~_etiquetada(Decision, DecisionLabel)))
    if estado:
        q = q.filter(Decision.estado == estado)
    if job_id:
        q = q.filter(Decision.job_id == job_id)
    if pregunta:
        q = q.filter(Decision.pregunta == pregunta)
        p = CATALOGO.get(pregunta)
        if p is not None:
            # Solo la version del catalogo: etiquetar la vieja no mide nada
            # que se vaya a usar.
            q = q.filter(Decision.version_pregunta == p.version)
            if p.solo_si is not None:
                q = q.filter(_aplica(Decision, p))
    return q


def pendientes(db, *, pregunta: str | None = None, job_id=None, estado: str | None = "incierto",
               limite: int = 20) -> list[dict[str, Any]]:
    """Lo que falta por revisar, una fila por estado, SIN la respuesta de Jev."""
    from shared.models import Decision, Url

    vistas: set[tuple[str, str, int]] = set()
    salida: list[dict[str, Any]] = []
    desde = 0
    lote = max(limite * _LOTE_POR_FILA, 50)
    while len(salida) < limite:
        filas = (_consulta(db, pregunta=pregunta, job_id=job_id, estado=estado)
                 .order_by(Decision.id).offset(desde).limit(lote).all())
        if not filas:
            break
        desde += len(filas)
        for d in filas:
            k = (d.clave, d.pregunta, d.version_pregunta)
            if k in vistas:
                continue
            vistas.add(k)
            p = pregunta_de(db, d)
            if p is None:
                continue
            url = db.query(Url.url).filter(Url.id == d.url_id).scalar() if d.url_id else None
            salida.append({
                "decision_id": d.id, "pregunta": p.nombre, "version": p.version,
                "tipo": p.tipo, "instrucciones": p.instrucciones, "descripcion": p.descripcion,
                "opciones": opciones_para_pantalla(p), "url": url, "job_id": str(d.job_id) if d.job_id else None,
                "ambito": d.ambito, "estado": d.estado_enviado,
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
    llama. `EtiquetaInvalida` si no esta en el vocabulario de la pregunta, y
    `LookupError` si la decision no existe o su pregunta ya no esta.
    """
    from shared.evaluacion_decisiones import etiqueta_de_respuesta, normalizar_etiqueta
    from shared.models import Decision, DecisionLabel

    d = db.get(Decision, decision_id)
    if d is None:
        raise LookupError("decision no encontrada")
    p = pregunta_de(db, d)
    if p is None:
        raise LookupError("su pregunta ya no esta en el catalogo")
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
    fila.etiqueta = valor
    fila.nota = (nota or "").strip() or None
    fila.autor = (autor or "").strip()[:128] or None
    niveles = len(p.opciones) if p.tipo == "score" else None
    jev = etiqueta_de_respuesta(p.tipo, d.respuesta, niveles)
    return {"etiqueta": valor, "jev": jev, "confianza": d.confianza, "coincide": jev == valor}


def resumen(db) -> list[dict[str, Any]]:
    """Por pregunta (version del catalogo): cuanto falta por revisar, cuanto
    esta etiquetado y en cuanto coincide Jev con la persona."""
    from shared.evaluacion_decisiones import etiqueta_de_respuesta
    from shared.models import Decision, DecisionLabel
    from shared.preguntas import CATALOGO

    salida = []
    for nombre, p in sorted(CATALOGO.items()):
        q = _consulta(db, pregunta=nombre, job_id=None, estado="incierto")
        por_revisar = q.with_entities(func.count(func.distinct(Decision.clave))).scalar() or 0
        etiquetas = (db.query(DecisionLabel.etiqueta, DecisionLabel.respuesta_jev, DecisionLabel.opciones)
                     .filter(DecisionLabel.pregunta == nombre, DecisionLabel.version_pregunta == p.version)
                     .all())
        if not por_revisar and not etiquetas:
            continue
        niveles = len(p.opciones) if p.tipo == "score" else None
        coinciden = sum(1 for e, r, _ in etiquetas if etiqueta_de_respuesta(p.tipo, r, niveles) == e)
        salida.append({"pregunta": nombre, "version": p.version, "descripcion": p.descripcion,
                       "por_revisar": int(por_revisar), "etiquetadas": len(etiquetas),
                       "coinciden_con_jev": coinciden, "umbral": p.umbral, "ejemplo": p.ejemplo})
    return salida


def exportar(db, pregunta: str) -> tuple[list[dict], list[dict]]:
    """(juego, respuestas) de lo etiquetado, con la forma de `extraer` y de
    `responder` de `scripts/evaluar_decisiones.py`, para pasarlo a `evaluar`.
    Solo la version del catalogo; el `id` de cada fila es el del estado."""
    from shared.models import DecisionLabel
    from shared.preguntas import CATALOGO

    p = CATALOGO.get(pregunta)
    if p is None:
        raise LookupError(f"{pregunta} no esta en el catalogo")
    filas = (db.query(DecisionLabel)
             .filter(DecisionLabel.pregunta == pregunta, DecisionLabel.version_pregunta == p.version)
             .order_by(DecisionLabel.id).all())
    juego, respuestas = [], []
    for f in filas:
        ident = f"{f.ambito}|{f.clave}|{f.variante}" if f.variante else f"{f.ambito}|{f.clave}"
        juego.append({"id": ident, "ambito": f.ambito, "pregunta": pregunta, "version": p.version,
                      "estado": f.estado_enviado, "etiqueta": f.etiqueta,
                      "etiquetado_por": f.autor or "revision", "nota": f.nota or "",
                      "origen_etiqueta": "revision",
                      **({"opciones": f.opciones} if f.opciones else {})})
        respuestas.append({"id": ident, "pregunta": pregunta, "version": p.version,
                           "respuesta": f.respuesta_jev, "confianza": f.confianza_jev})
    return juego, respuestas


def jsonl(filas: list[dict]) -> str:
    return "".join(json.dumps(f, ensure_ascii=False, sort_keys=True) + "\n" for f in filas)
