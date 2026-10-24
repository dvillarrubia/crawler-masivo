"""Juego de evaluacion y medida de una pregunta a Jev (#8, decision 159).

Cuatro ordenes, en el orden en que se usan:

    extraer   <pregunta> --job <id> [--job <id> ...] [--n 200] [--semilla 0]
              Saca un juego estratificado por plantilla de censos reales a
              `tests/decisiones/<pregunta>/v<version>.jsonl`, con la etiqueta
              vacia. No llama a nadie.
    responder <juego.jsonl> --salida <respuestas.jsonl> [--confirmar]
              Ensena lo que costaria pasarlo por Jev y, SOLO con --confirmar,
              lo pasa. Sin `OPENROUTER_API_KEY` y `DECISIONS_ENABLED=1` dice que
              la capa esta desactivada y no hace nada (decisiones 84 y 85: nada
              llama a un modelo solo).
    evaluar   <juego.jsonl> <respuestas.jsonl> [--objetivo 0.95]
              Acierto, precision y recall por clase, ECE, curva de cobertura y
              el umbral que se pondria en el catalogo. Cuenta la etiqueta
              HUMANA; las filas sin etiquetar no cuentan y se dice cuantas son.
    repetir   <respuestas_1.jsonl> <respuestas_2.jsonl> [--acuerdo]
              Repetibilidad (la misma pregunta dos veces) o, con --acuerdo,
              acuerdo y kappa entre dos jueces o dos redacciones.

Uso (dentro del contenedor del crawler):

    python scripts/evaluar_decisiones.py extraer es_pagina_legal --job <id> --job <id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _preparar_rutas() -> None:
    """Permite ejecutarlo tanto desde /app como desde la raiz del repo."""
    aqui = os.path.dirname(os.path.abspath(__file__))
    raiz = os.path.dirname(aqui)
    if raiz not in sys.path:
        sys.path.insert(0, raiz)


def _leer(ruta: str) -> list[dict]:
    with open(ruta, encoding="utf-8") as f:
        return [json.loads(linea) for linea in f if linea.strip()]


def _escribir(ruta: str | Path, filas: list[dict]) -> None:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        for fila in filas:
            f.write(json.dumps(fila, ensure_ascii=False, sort_keys=True) + "\n")


def modulo_de(pregunta: str):
    from shared.enriquecido import REGISTRO

    for m in REGISTRO.values():
        if pregunta in m.preguntas:
            return m
    raise SystemExit(f"Ningun modulo de shared/enriquecido.py hace la pregunta {pregunta!r}")


def extraer(session, pregunta: str, jobs: list[str], n: int, semilla: int) -> list[dict]:
    """Las filas del juego: estado, de donde sale y la etiqueta vacia."""
    import uuid

    from shared.evaluacion_decisiones import muestra_estratificada
    from shared.models import Job, Url
    from shared.plantillas import plantilla_de, reglas_de_plantilla
    from shared.preguntas import obtener

    from urllib.parse import urlsplit

    p = obtener(pregunta)
    modulo = modulo_de(pregunta)
    if getattr(modulo, "por_grupo", False):
        return _extraer_grupos(session, p, modulo, jobs, n, semilla)
    por_plantilla: dict[str, list[tuple]] = {}
    vistas: set[str] = set()
    opciones = None
    for jid in jobs:
        job = session.get(Job, uuid.UUID(str(jid)))
        if job is None:
            raise SystemExit(f"No existe el job {jid}")
        if p.opciones_del_cliente:
            # Las opciones son las del cliente (decision 178): el juego va con
            # ellas, y dos taxonomias distintas son dos juegos.
            (pc,) = modulo.preguntas_para(session, job)
            if not pc.variante:
                raise SystemExit(f"El job {jid} no tiene taxonomia: {pregunta} no pregunta nada ahi")
            if opciones is not None and pc.opciones != opciones:
                raise SystemExit(f"{pregunta}: los jobs tienen taxonomias distintas; un juego por cliente")
            opciones = pc.opciones
        reglas = reglas_de_plantilla((job.config or {}).get("templates"))
        # Ordenado: sin ORDER BY, Postgres devuelve otro orden tras un VACUUM y
        # la misma semilla daba otra muestra.
        # El mismo alcance que el boton de #40 (`urls_del_alcance`): internas,
        # HTML, 200 e INDEXABLES. Sin el `indexable`, el juego de soft 404 se
        # llenaba de paginas de error en noindex, que no son ningun problema.
        filas = session.query(Url.id, Url.url, Url.path).filter(
            Url.job_id == job.id, Url.is_internal.is_(True), Url.is_html.is_(True),
            Url.status_code == 200, Url.indexable.is_(True)).order_by(Url.id).all()
        # El juego sale de lo que el modulo pregunta de verdad: soft 404, de
        # sus candidatos (decision 163), no de todo el censo.
        if modulo.filtrar_urls:
            dentro = set(modulo.filtrar_urls(session, job.id, [f[0] for f in filas]))
            filas = [f for f in filas if f[0] in dentro]
        for uid, url, path in filas:
            # La misma URL en dos censos es UN ejemplo: el primero que la trae.
            if url in vistas:
                continue
            vistas.add(url)
            # La plantilla con el host delante: dos sitios con /blog/:slug son
            # dos plantillas, no una.
            plantilla = f"{urlsplit(url).hostname or ''}|{plantilla_de(path or '/', reglas)}"
            por_plantilla.setdefault(plantilla, []).append((str(job.id), uid, url, plantilla))
    # Todas en el orden estratificado (los `n` primeros son los de siempre) y
    # se van cogiendo saltando los estados repetidos: la misma pagina con y sin
    # www es el mismo ejemplo para Jev y contarlo dos veces infla el juego
    # (decision 169). Se rellena con la siguiente hasta llegar a `n`.
    total = sum(len(v) for v in por_plantilla.values())
    orden = muestra_estratificada(por_plantilla, total, semilla=semilla)
    filas: list[dict] = []
    estados_vistos: set[str] = set()
    for i in range(0, len(orden), 200):
        if len(filas) >= n:
            break
        trozo = orden[i:i + 200]
        for (jid, uid, url, plantilla), e in zip(
                trozo, modulo.construir_estados(session, [uid for _, uid, _, _ in trozo])):
            clave = json.dumps(e.datos, sort_keys=True, ensure_ascii=False, default=str)
            if clave in estados_vistos:
                continue
            estados_vistos.add(clave)
            fila = {"id": url, "job_id": jid, "url_id": uid, "plantilla": plantilla,
                    "pregunta": p.nombre, "version": p.version, "estado": e.datos,
                    "etiqueta": None, "etiquetado_por": None, "nota": ""}
            if opciones is not None:
                fila["opciones"] = opciones
            filas.append(fila)
            if len(filas) >= n:
                break
    return filas


def pregunta_del_juego(juego: list[dict]):
    """La pregunta del juego, con las opciones del cliente si las trae
    (decision 178: `categoria_cliente` pregunta por la taxonomia del job)."""
    from shared.preguntas import obtener

    p = obtener(juego[0]["pregunta"])
    if p.opciones_del_cliente:
        if not juego[0].get("opciones"):
            raise SystemExit(f"{p.nombre}: el juego no trae las opciones del cliente")
        p = p.con_opciones(juego[0]["opciones"])
    return p


def _extraer_grupos(session, p, modulo, jobs: list[str], n: int, semilla: int) -> list[dict]:
    """El juego de un modulo por grupo (decision 175: una busqueda y un par de
    las URLs que se la reparten). Estratificado por host y COMBINACION de
    plantillas del par (ficha + ficha no es lo mismo que listado + ficha), y
    sin repetidos: la misma busqueda con las mismas rutas es un ejemplo aunque
    venga de dos censos o con y sin www (la 169 vio que eso infla la muestra),
    y dos estados identicos, tambien."""
    import uuid
    from urllib.parse import urlsplit

    from shared.enriquecido_canibalizacion import deshacer_par, pares_de_aviso
    from shared.evaluacion_decisiones import muestra_estratificada
    from shared.models import Issue, Job, Url
    from shared.plantillas import plantilla_de, reglas_de_plantilla

    por_plantilla: dict[str, list[tuple]] = {}
    vistas: set[tuple] = set()
    for jid in jobs:
        job = session.get(Job, uuid.UUID(str(jid)))
        if job is None:
            raise SystemExit(f"No existe el job {jid}")
        reglas = reglas_de_plantilla((job.config or {}).get("templates"))
        alcance = [uid for (uid,) in session.query(Url.id).filter(
            Url.job_id == job.id, Url.is_internal.is_(True), Url.is_html.is_(True),
            Url.status_code == 200, Url.indexable.is_(True)).order_by(Url.id)]
        grupos = modulo.filtrar_urls(session, job.id, alcance)
        avisos = sorted({deshacer_par(g)[0] for g in grupos})
        detalles = {}
        for i in range(0, len(avisos), 500):
            detalles.update(session.query(Issue.id, Issue.details).filter(Issue.id.in_(avisos[i:i + 500])).all())
        for gid in grupos:
            iid, k = deshacer_par(gid)
            d = detalles.get(iid) or {}
            par = {kk: (a, b) for kk, a, b in pares_de_aviso(d)}.get(k)
            if par is None:
                continue
            rutas = tuple(sorted(urlsplit(u["url"]).path or "/" for u in par))
            clave = (str(d.get("consulta") or "").casefold(), rutas)
            if clave in vistas:
                continue
            vistas.add(clave)
            host = urlsplit(par[0]["url"]).hostname
            formas = sorted({u.get("plantilla") or plantilla_de(urlsplit(u["url"]).path or "/", reglas)
                             for u in par})
            estrato = f"{host or ''}|{' + '.join(formas)}"
            por_plantilla.setdefault(estrato, []).append((str(job.id), gid, d.get("consulta"), rutas, estrato))
    elegidas = muestra_estratificada(por_plantilla, n, semilla=semilla)
    estados = modulo.construir_estados(session, [gid for _, gid, *_ in elegidas])
    nota = (f"Etiquetar solo si {p.solo_si[0]} es «{p.solo_si[1]}»; si no, dejar vacia."
            if p.solo_si else "")
    filas, iguales = [], set()
    for (jid, gid, consulta, rutas, estrato), e in zip(elegidas, estados):
        crudo = json.dumps(e.datos, sort_keys=True, ensure_ascii=False)
        if crudo in iguales:
            continue
        iguales.add(crudo)
        filas.append({"id": f"{consulta} | {' + '.join(rutas)}", "job_id": jid, "url_id": e.url_id,
                      "grupo": gid, "ambito": e.ambito, "plantilla": estrato, "pregunta": p.nombre, "version": p.version,
                      "estado": e.datos, "etiqueta": None, "etiquetado_por": None, "nota": nota})
    return filas


def responder(juego: list[dict], confirmar: bool, capa=None) -> tuple[dict, list[dict] | None]:
    """(estimacion, respuestas o None si no se confirmo o la capa esta apagada).

    Es la unica via directa a la capa fuera del boton de #40 (decision 159).
    Pide la clave, `DECISIONS_ENABLED=1`, que exista `ENRICHMENT_TOKEN` (16+
    caracteres) y `--confirmar`. Ojo: dentro del contenedor el token esta en el
    entorno, asi que ahi la barrera real es `--confirmar`, no el token. Gasta
    con los MISMOS presupuestos que el boton (el mensual del cliente y el
    global, sembrados desde `decisions` y contados en Redis), un tope de
    ejecucion (la estimacion x 1,5) y deja las decisiones en `decisions`.
    """
    from shared.decisiones import Estado, capa_desde_entorno

    if not juego:
        raise SystemExit("El juego esta vacio")
    p = pregunta_del_juego(juego)
    estados = [Estado(datos=f["estado"], url_id=f.get("url_id"), ambito=f.get("ambito") or "url")
               for f in juego]
    if capa is None:
        # Primero una capa sin red para estimar, y la de verdad con el tope.
        estimacion = capa_desde_entorno().estimar(estados, [p])
        capa = _capa_con_presupuesto(estimacion, capa_desde_entorno)
    estimacion = capa.estimar(estados, [p])
    if getattr(capa, "cortada", None):
        # Sin clave o sin DECISIONS_ENABLED=1 la capa nace cortada: no hay red.
        estimacion["desactivada"] = capa.cortada
        return estimacion, None
    if len((os.getenv("ENRICHMENT_TOKEN") or "").strip()) < 16:
        estimacion["desactivada"] = "falta ENRICHMENT_TOKEN (16 caracteres o mas), como en el boton de #40"
        return estimacion, None
    if not confirmar:
        return estimacion, None
    decisiones = capa.preguntar_sync(estados, [p])
    respuestas = []
    for fila, (d,) in zip(juego, decisiones):
        respuestas.append({"id": fila["id"], "pregunta": p.nombre, "version": p.version,
                           "respuesta": d.respuesta, "confianza": d.confianza,
                           "probabilidades": d.probabilidades, "estado": d.estado, "origen": d.origen,
                           "motivo": d.motivo, "tokens": d.tokens_entrada, "coste": d.coste_usd,
                           "modelo_respuesta": d.modelo_respuesta})
    return estimacion, respuestas


def _capa_con_presupuesto(estimacion: dict, capa_desde_entorno):
    """La capa como la monta el boton de #40: Redis y la BD para los
    presupuestos y las decisiones, y un tope de ejecucion. La fabrica llega
    desde `responder`, la unica funcion que puede importar la capa."""
    try:
        import redis as redis_lib

        from shared.config import REDIS_URL
        from shared.database import SessionLocal

        rconn = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
        rconn.ping()
        sesion_factory = SessionLocal
    except Exception:  # noqa: BLE001 -- sin Redis no se gasta: el tope mensual no se podria contar
        return capa_desde_entorno(max_llamadas=0)
    tope = round(float(estimacion.get("coste_estimado_usd") or 0) * 1.5 + 0.01, 4)
    return capa_desde_entorno(sesion_factory=sesion_factory, redis=rconn, presupuesto_ejecucion_usd=tope,
                              max_llamadas=int(estimacion.get("llamadas") or 0))


def evaluar(juego: list[dict], respuestas: list[dict], objetivo: float) -> dict:
    from shared.evaluacion_decisiones import etiqueta_de_respuesta, metricas
    from shared.evaluacion_decisiones import normalizar_etiqueta

    if not juego:
        raise SystemExit("El juego esta vacio")
    p = pregunta_del_juego(juego)
    otras = sorted({f.get("version") for f in juego} - {p.version})
    if otras:
        raise SystemExit(f"El juego es de la version {otras} y el catalogo esta en la {p.version}: "
                         "una pregunta nueva se mide con su propio juego (o se re-etiqueta).")
    niveles = len(p.opciones) if p.tipo == "score" else None
    por_id = {r["id"]: r for r in respuestas
              if r.get("pregunta") == p.nombre and r.get("version") == p.version}
    filas = []
    invalidas = 0
    # El mismo estado dos veces (la URL con y sin www, la misma ruta en otro
    # host) es la misma entrada para Jev: contarla dos veces infla la muestra
    # y puede sostener un umbral que sin ella no sale (decision 169). Cuenta
    # una vez, con la etiqueta de la primera; si se contradicen, se dice.
    vistos: dict[str, object] = {}
    repetidos = contradictorios = 0
    for f in juego:
        clave = json.dumps(f.get("estado"), sort_keys=True, ensure_ascii=False)
        if f.get("estado") is not None and clave in vistos:
            repetidos += 1
            contradictorios += vistos[clave] != f.get("etiqueta")
            continue
        vistos[clave] = f.get("etiqueta")
        r = por_id.get(f["id"]) or {}
        etiqueta = normalizar_etiqueta(p.tipo, f.get("etiqueta"), p.opciones)
        if f.get("etiqueta") not in (None, "") and etiqueta is None:
            invalidas += 1
        filas.append({"etiqueta": etiqueta,
                      "respuesta": etiqueta_de_respuesta(p.tipo, r.get("respuesta"), niveles),
                      "confianza": r.get("confianza"), "tokens": r.get("tokens"), "coste": r.get("coste")})
    informe = metricas(filas, objetivo=objetivo)
    informe["pregunta"], informe["version"], informe["umbral_actual"] = p.nombre, p.version, p.umbral
    informe["respuestas_de_otra_version"] = sum(
        1 for r in respuestas if r.get("pregunta") == p.nombre and r.get("version") != p.version)
    informe["etiquetas_invalidas"] = invalidas
    informe["estados_repetidos_quitados"] = repetidos
    informe["repetidos_con_otra_etiqueta"] = contradictorios
    ids = {f["id"] for f in juego}
    informe["respuestas_fuera_del_juego"] = sum(1 for r in respuestas if r.get("id") not in ids)
    return informe


def main(argv: list[str] | None = None) -> int:
    _preparar_rutas()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="orden", required=True)
    a = sub.add_parser("extraer")
    a.add_argument("pregunta")
    a.add_argument("--job", action="append", required=True)
    a.add_argument("--n", type=int, default=200)
    a.add_argument("--semilla", type=int, default=0)
    a.add_argument("--salida")
    b = sub.add_parser("responder")
    b.add_argument("juego")
    b.add_argument("--salida", required=True)
    b.add_argument("--confirmar", action="store_true")
    c = sub.add_parser("evaluar")
    c.add_argument("juego")
    c.add_argument("respuestas")
    c.add_argument("--objetivo", type=float, default=None)
    d = sub.add_parser("repetir")
    d.add_argument("primera")
    d.add_argument("segunda")
    d.add_argument("--acuerdo", action="store_true")
    args = ap.parse_args(argv)

    if args.orden == "extraer":
        from shared.database import SessionLocal
        from shared.preguntas import obtener

        s = SessionLocal()
        try:
            filas = extraer(s, args.pregunta, args.job, args.n, args.semilla)
        finally:
            s.close()
        salida = args.salida or f"tests/decisiones/{args.pregunta}/v{obtener(args.pregunta).version}.jsonl"
        _escribir(salida, filas)
        plantillas = len({f["plantilla"] for f in filas})  # con el host: dos sitios son dos
        print(f"{len(filas)} ejemplos de {plantillas} plantillas en {salida}. Falta etiquetarlos "
              "(campo `etiqueta` y `etiquetado_por`).")
    elif args.orden == "responder":
        estimacion, respuestas = responder(_leer(args.juego), args.confirmar)
        print(json.dumps(estimacion, ensure_ascii=False, indent=1))
        if estimacion.get("desactivada"):
            print(f"La capa de decisiones esta desactivada ({estimacion['desactivada']}): no se ha "
                  "llamado a nadie.")
            return 2
        if respuestas is None:
            print("Sin --confirmar no se llama a Jev.")
            return 0
        _escribir(args.salida, respuestas)
        sin = [r for r in respuestas if r.get("respuesta") is None]
        print(f"{len(respuestas)} respuestas en {args.salida}")
        if sin:
            motivos = sorted({r.get("motivo") or "?" for r in sin})
            print(f"Ojo: {len(sin)} sin respuesta ({', '.join(motivos)}); cuentan como no evaluables.")
            return 3
    elif args.orden == "evaluar":
        from shared.evaluacion_decisiones import PRECISION_OBJETIVO

        informe = evaluar(_leer(args.juego), _leer(args.respuestas), args.objetivo or PRECISION_OBJETIVO)
        print(json.dumps(informe, ensure_ascii=False, indent=1))
    elif args.orden == "repetir":
        from shared.evaluacion_decisiones import acuerdo, etiqueta_de_respuesta, repetibilidad
        from shared.preguntas import obtener

        primera, segunda = _leer(args.primera), _leer(args.segunda)
        claves = {(r.get("pregunta"), r.get("version")) for r in primera + segunda}
        if len(claves) != 1 and not args.acuerdo:
            raise SystemExit(f"Los dos ficheros mezclan preguntas o versiones: {sorted(claves, key=str)}")

        def vocabulario(filas):
            salida = {}
            for r in filas:
                p = obtener(r["pregunta"])
                niveles = len(p.opciones) if p.tipo == "score" else None
                salida[r["id"]] = etiqueta_de_respuesta(p.tipo, r.get("respuesta"), niveles)
            return salida

        uno, dos = vocabulario(primera), vocabulario(segunda)
        print(json.dumps((acuerdo if args.acuerdo else repetibilidad)(uno, dos), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
