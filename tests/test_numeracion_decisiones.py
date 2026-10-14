"""Que dos decisiones de `docs/decisiones/` no lleven el mismo número.

Es un choque garantizado entre ramas largas: dos sesiones trabajando a la vez
cogen «el siguiente hueco» a la vez. Nos mordió tres veces el 2026-10-13, y la
última estuvo a punto de entrar — una decisión numerada 79 cuando esa ya era «un
`<dialog>` cerrado es contenido» (#27). Lo cazó un aviso entre sesiones, que es
un sitio malísimo para cazarlo: aquí falla en el rebase y en CI.

Dos matices del formato, que no son despistes:

- **Los sufijos son legítimos y cuentan aparte**: `10b` y `12b` matizan a la 10
  y a la 12 sin ser decisiones nuevas.
- **Viven repartidas por tema** en `docs/decisiones/*.md` (antes era una
  sola seccion de CLAUDE.md), asi que el choque puede estar en dos ficheros
  distintos: se miran todos juntos. Y el indice `README.md` tiene que
  listarlas todas, o una decision nueva queda escrita donde nadie la busca.

Para no chocar, el numero se reserva entre sesiones con
`python3 .claude/hooks/sesiones.py numero-decision <id-sesion>`.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DECISIONES = RAIZ / "docs" / "decisiones"
INDICE = DECISIONES / "README.md"

# `34. **El PageRank en tres columnas...` / `10b. **El hero tambien...`
# Con re.S porque el titulo en negrita suele partirse en dos lineas: sin el,
# 21 de las 94 decisiones no casaban y sus numeros quedaban sin comprobar.
_DECISION = re.compile(r"^(\d+[a-z]?)\.\s+\*\*(.+?)\*\*", re.M | re.S)
# Fila del indice por numero: `| 34 | titulo | [`fichero`](fichero.md#d34) |`
_FILA_INDICE = re.compile(r"^\|\s*(\d+[a-z]?)\s*\|.*\]\((\S+?\.md)#d\1\)", re.M)


def _decisiones() -> list[tuple[str, str]]:
    """Todas las decisiones de los ficheros de tema, sin el indice: su tabla
    no es una lista numerada pero mejor no darle ocasion de parecerlo."""
    return [(m.group(1), m.group(2))
            for f in sorted(DECISIONES.glob("*.md")) if f != INDICE
            for m in _DECISION.finditer(f.read_text())]


def _ficheros_de_cada_decision() -> dict[str, str]:
    return {m.group(1): f.name
            for f in DECISIONES.glob("*.md") if f != INDICE
            for m in _DECISION.finditer(f.read_text())}


def test_ninguna_decision_repite_numero():
    numeros = collections.Counter(n for n, _ in _decisiones())
    repetidos = {n: v for n, v in numeros.items() if v > 1}
    if repetidos:
        detalle = []
        for numero in repetidos:
            titulos = [t for n, t in _decisiones() if n == numero]
            detalle.append(f"  {numero}: " + " | ".join(t[:60] for t in titulos))
        raise AssertionError(
            "Hay decisiones con el mismo numero en docs/decisiones/. Pasa cuando dos "
            "ramas largas cogen 'el siguiente hueco' a la vez; coge el "
            f"siguiente libre y renumera:\n" + "\n".join(detalle))


def test_el_fichero_se_lee_y_tiene_decisiones():
    """Si la regex deja de casar, el test de arriba pasaria vacio y no
    comprobaria nada — que es peor que no tenerlo."""
    assert len(_decisiones()) > 50


def test_un_sufijo_cuenta_aparte_de_su_numero():
    """`10b` matiza a la 10 sin ser una decision nueva: las dos pueden existir."""
    numeros = {n for n, _ in _decisiones()}
    con_sufijo = {n for n in numeros if not n.isdigit()}
    assert con_sufijo, "si desaparecen los sufijos, revisar este criterio"
    for n in con_sufijo:
        assert n[:-1] in numeros, (
            f"la decision {n} matiza a la {n[:-1]}, que no existe")


def test_el_indice_lista_cada_decision_en_su_fichero():
    """Una decision que no esta en el indice no la encuentra nadie, y una fila
    que apunta a otro fichero manda a leer el sitio equivocado."""
    reales = _ficheros_de_cada_decision()
    indice = {m.group(1): m.group(2) for m in _FILA_INDICE.finditer(INDICE.read_text())}
    faltan = sorted(set(reales) - set(indice))
    sobran = sorted(set(indice) - set(reales))
    mal = sorted(n for n in set(reales) & set(indice) if reales[n] != indice[n])
    assert not (faltan or sobran or mal), (
        f"docs/decisiones/README.md no cuadra: faltan {faltan}, sobran {sobran}, "
        f"apuntan a otro fichero {mal}")


def test_claude_md_ya_no_guarda_decisiones():
    """CLAUDE.md es el indice y se carga en cada sesion: las decisiones van en
    docs/decisiones/. Si vuelve a crecer, este test lo canta."""
    texto = (RAIZ / "CLAUDE.md").read_text()
    assert not _DECISION.search(texto), "hay una decision numerada en CLAUDE.md"
    assert len(texto.splitlines()) < 250
