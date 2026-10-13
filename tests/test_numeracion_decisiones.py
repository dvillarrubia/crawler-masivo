"""Que dos decisiones de CLAUDE.md no lleven el mismo número.

Es un choque garantizado entre ramas largas: dos sesiones trabajando a la vez
cogen «el siguiente hueco» a la vez. Nos mordió tres veces el 2026-10-13, y la
última estuvo a punto de entrar — una decisión numerada 79 cuando esa ya era «un
`<dialog>` cerrado es contenido» (#27). Lo cazó un aviso entre sesiones, que es
un sitio malísimo para cazarlo: aquí falla en el rebase y en CI.

Dos matices del formato, que no son despistes:

- **Los sufijos son legítimos y cuentan aparte**: `10b` y `12b` matizan a la 10
  y a la 12 sin ser decisiones nuevas.
- **El orden NO es secuencial a propósito.** El fichero agrupa por tema (34,
  35, 41, 42… y más abajo 17, 20, 21), así que esto comprueba duplicados y
  nada más. Exigir orden obligaría a reescribir el documento entero por cada
  decisión nueva, que es justo lo contrario de lo que se quiere.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CLAUDE_MD = RAIZ / "CLAUDE.md"

# `34. **El PageRank en tres columnas...` / `10b. **El hero tambien...`
_DECISION = re.compile(r"^(\d+[a-z]?)\.\s+\*\*(.+?)\*\*", re.M)
_SECCION = "## Critical Design Decisions"


def _cuerpo_de_la_seccion() -> str:
    """Solo la seccion de decisiones.

    El fichero tiene mas listas numeradas —la de trabajo pendiente, sin ir mas
    lejos, empieza otra vez por 1— y mirar el documento entero da duplicados
    que no lo son. Lo descubri con este mismo test: su primera version cantaba
    cuatro choques que eran esa otra lista.
    """
    texto = CLAUDE_MD.read_text()
    i = texto.index(_SECCION) + len(_SECCION)
    siguiente = re.search(r"^## ", texto[i:], re.M)
    return texto[i:i + siguiente.start()] if siguiente else texto[i:]


def _decisiones() -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2))
            for m in _DECISION.finditer(_cuerpo_de_la_seccion())]


def test_ninguna_decision_repite_numero():
    numeros = collections.Counter(n for n, _ in _decisiones())
    repetidos = {n: v for n, v in numeros.items() if v > 1}
    if repetidos:
        detalle = []
        for numero in repetidos:
            titulos = [t for n, t in _decisiones() if n == numero]
            detalle.append(f"  {numero}: " + " | ".join(t[:60] for t in titulos))
        raise AssertionError(
            "Hay decisiones con el mismo numero en CLAUDE.md. Pasa cuando dos "
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
