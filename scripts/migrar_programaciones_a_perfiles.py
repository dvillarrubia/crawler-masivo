"""Cuelga de un perfil las programaciones de copia fija (decision 193).

Antes una programacion guardaba una COPIA congelada de las semillas y la
configuracion del rastreo del que se creo, y no dependia de ningun perfil: los
cambios del cliente no le llegaban, y su serie de evolucion e incidentes
(«programacion:<id>») iba aparte de la del perfil. En Lopesan habia tres
programaciones «lopesan.com (semanal)» con las mismas 3.040 semillas, dos
series iguales en la grafica y un perfil «lista» que nadie lanzaba (V06).

Para cada programacion SIN perfil (o con perfil pero de copia fija):

1. Busca un perfil del mismo cliente con exactamente las mismas semillas; si
   no hay, crea uno («Sitio completo», o «Sitio completo 2» si el nombre esta
   cogido) con esas semillas y, como config, solo lo que la copia tiene de
   distinto respecto al cliente (no la copia entera: asi los cambios del
   cliente vuelven a llegar). Las activas van primero, para que el perfil
   salga de la que de verdad se lanza.
2. La cuelga del perfil (`hereda_perfil`): se queda sin semillas propias y con
   solo lo que la diferencia del perfil (`config` = lo propio).
3. Une su historia a la del perfil: sus rastreos reciben `profile_id` y sus
   incidentes pasan a la serie «perfil:<id>» (`unir_serie_al_perfil`), para
   que la grafica no parta la serie en dos.

Lo que el siguiente disparo lanzaria NO cambia: se comprueba para cada una
(config efectiva antes == despues) y, si no cuadra, esa programacion no se
toca y se dice por que.

Programaciones sin cliente: no se tocan (un perfil es de un cliente).

Uso (dentro del contenedor de la API o del crawler):

    python scripts/migrar_programaciones_a_perfiles.py            # simulacion
    python scripts/migrar_programaciones_a_perfiles.py --confirmar

Sin `--confirmar` no escribe nada: dice que perfiles crearia o reutilizaria y
que programaciones colgaria de cada uno.
"""

from __future__ import annotations

import argparse
import os
import sys


def _preparar_rutas() -> None:
    aqui = os.path.dirname(os.path.abspath(__file__))
    raiz = os.path.dirname(aqui)
    if raiz not in sys.path:
        sys.path.insert(0, raiz)


_preparar_rutas()

NOMBRE_PERFIL = "Sitio completo"


def _semillas(lista) -> frozenset[str]:
    return frozenset(u.strip() for u in (lista or []) if u and u.strip())


def _nombre_libre(perfiles_del_cliente, base: str = NOMBRE_PERFIL) -> str:
    usados = {p.nombre for p in perfiles_del_cliente}
    if base not in usados:
        return base
    n = 2
    while f"{base} {n}" in usados:
        n += 1
    return f"{base} {n}"


def migrar(db, *, confirmar: bool = False) -> list[dict]:
    """Devuelve una linea por programacion con lo que hizo (o haria). Con
    `confirmar=False` deshace todo al final: la simulacion recorre el mismo
    camino que la migracion de verdad, sin commit."""
    from shared.models import Client, CrawlProfile, Programacion
    from shared.programaciones import (
        capa_que_reproduce, con_defaults, config_del_momento, unir_serie_al_perfil,
    )

    progs = (db.query(Programacion)
             .filter(Programacion.hereda_perfil.is_(False))
             .order_by(Programacion.activa.desc(), Programacion.created_at.desc())
             .all())
    salida: list[dict] = []
    for prog in progs:
        linea = {"programacion": str(prog.id), "nombre": prog.nombre,
                 "activa": bool(prog.activa), "semillas": len(prog.seeds or [])}
        if prog.cliente_id is None:
            salida.append({**linea, "accion": "omitida", "motivo": "sin cliente"})
            continue
        cliente = db.get(Client, prog.cliente_id)
        mias = _semillas(prog.seeds)
        if not mias:
            salida.append({**linea, "accion": "omitida", "motivo": "sin semillas"})
            continue
        if ((prog.config or {}).get("http") or {}).get("basic_auth_password"):
            # Un perfil no guarda contrasenas (decision 83): moverla alli la
            # dejaria a la vista en GET /api/clients/<id>/profiles.
            salida.append({**linea, "accion": "omitida",
                           "motivo": "lleva contrasena de autenticacion basica: pasala a mano"})
            continue
        # Lo que lanzaria HOY, con los defaults debajo (una copia vieja puede
        # no tener las claves que JobConfig gano despues).
        # (Sin deducir claves: el worker manda la copia de una de copia fija
        # tal cual.)
        efectiva = con_defaults(prog.config or {})
        # Un savepoint por programacion: si la comprobacion de abajo falla, el
        # perfil que se acaba de crear se deshace con ella (con --confirmar
        # quedaba grabado, huerfano, porque `flush` ya lo habia insertado).
        punto = db.begin_nested()

        perfiles = (db.query(CrawlProfile)
                    .filter(CrawlProfile.cliente_id == prog.cliente_id).all())
        perfil = None
        if prog.profile_id is not None:
            actual = db.get(CrawlProfile, prog.profile_id)
            if actual is not None and _semillas(actual.seeds) == mias:
                perfil = actual
        if perfil is None:
            perfil = next((p for p in perfiles if _semillas(p.seeds) == mias), None)
        creado = perfil is None
        if creado:
            perfil = CrawlProfile(
                cliente_id=prog.cliente_id, nombre=_nombre_libre(perfiles),
                rol="propio", seeds=list(prog.seeds), activo=True,
                config=capa_que_reproduce(
                    lambda capa: config_del_momento(cliente, None, capa), efectiva),
                notas=f"Creado al migrar la programacion «{prog.nombre}» (decision 193).")
            db.add(perfil)
            db.flush()

        propia = capa_que_reproduce(
            lambda capa: config_del_momento(cliente, perfil, capa), efectiva)
        despues = config_del_momento(cliente, perfil, propia)
        if despues != efectiva:
            # No deberia pasar; si pasa, mejor no tocarla que cambiar en
            # silencio lo que rastrea el lunes. El rollback del savepoint se
            # lleva tambien el perfil recien creado.
            punto.rollback()
            salida.append({**linea, "accion": "omitida",
                           "motivo": "la configuracion heredada no reproduce la copia"})
            continue

        prog.profile_id = perfil.id
        prog.hereda_perfil = True
        prog.seeds = []
        prog.config = propia
        db.flush()
        movidos = unir_serie_al_perfil(db, prog, perfil)
        punto.commit()
        salida.append({**linea, "accion": "colgada",
                       "perfil": perfil.nombre, "perfil_nuevo": creado,
                       "config_del_perfil": sorted((perfil.config or {}).keys()) if creado else None,
                       "lo_propio": sorted(propia.keys()), **movidos})
    if confirmar:
        db.commit()
    else:
        db.rollback()
    return salida


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--confirmar", action="store_true",
                        help="Aplicar los cambios (sin esto, solo simula)")
    args = parser.parse_args()

    from shared.database import SessionLocal

    db = SessionLocal()
    try:
        lineas = migrar(db, confirmar=args.confirmar)
    finally:
        db.close()
    modo = "APLICADO" if args.confirmar else "SIMULACION (nada escrito; --confirmar para aplicar)"
    print(modo)
    for l in lineas:
        if l["accion"] == "omitida":
            print(f"  - {l['nombre']} ({l['programacion'][:8]}): se deja como esta — {l['motivo']}")
            continue
        perfil = (f"perfil NUEVO «{l['perfil']}» (config propia del perfil: "
                  f"{', '.join(l['config_del_perfil']) or 'nada'})" if l["perfil_nuevo"]
                  else f"perfil existente «{l['perfil']}»")
        print(f"  - {l['nombre']} ({l['programacion'][:8]}, "
              f"{'activa' if l['activa'] else 'en pausa'}, {l['semillas']} semillas) -> {perfil}; "
              f"lo propio: {', '.join(l['lo_propio']) or 'nada'}; "
              f"{l['rastreos']} rastreos y {l['incidentes']} incidentes pasan a la serie del perfil"
              + (f" ({l['incidentes_cerrados']} cerrados por repetidos)" if l["incidentes_cerrados"] else ""))
    if not lineas:
        print("  No hay programaciones de copia fija.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
