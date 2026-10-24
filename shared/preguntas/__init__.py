"""Catalogo versionado de preguntas a Jev (#7; el catalogo de verdad es #9).

Aqui solo va la ESTRUCTURA y una pregunta de ejemplo. Diseñar las preguntas
es #9 y ninguna llega a produccion sin pasar por el juego de evaluacion y la
calibracion de #8: una pregunta sin evaluar es una opinion con decimales.

Reglas que hace cumplir `Pregunta`:

- **Cambiar el texto es cambiar la version.** La version forma parte de la
  clave de cache (`shared/decisiones.clave_cache`): si se retoca la
  instruccion sin subirla, la cache sigue sirviendo respuestas a la pregunta
  vieja como si fueran de la nueva. `huella` permite a un test detectarlo.
- **El modelo va fijado** (`typesafe/jev-1.13`), nunca `jev-latest`: el alias
  se mueve cuando sale una version nueva («An alias moves when a new release
  ships», `docs.typesafe.ai/models`), y entonces la cache y los umbrales
  calibrados en #8 dejarian de significar lo que significaban.
- **Las instrucciones son obligatorias.** El esquema de TypeSafe las deja
  opcionales, pero el de OpenRouter (`DecisionsChoiceQuestion` y hermanas) y
  la referencia publica de TypeSafe (`docs.typesafe.ai/api`) las exigen. Se
  manda lo que valen los dos.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

# Verificado el 2026-10-13 en `GET https://openrouter.ai/api/v1/models/
# typesafe/jev-1.13/endpoints`: un unico endpoint, proveedor TypeSafe,
# 0,042 $/M tokens de entrada, salida gratis, 32k de prompt y 64k de contexto.
MODELO_DEFECTO = "typesafe/jev-1.13"

TIPOS = ("choice", "score", "noul")


@dataclass(frozen=True)
class Pregunta:
    nombre: str
    tipo: Literal["choice", "score", "noul"]
    instrucciones: str
    version: int
    # choice: {etiqueta: descripcion|None}; score: [nivel0, nivel1, ...] en
    # orden; noul: {"true": ..., "false": ...} o None.
    opciones: Any = None
    modelo: str = MODELO_DEFECTO
    # Confianza por debajo de la cual la respuesta queda `incierto`. La pone
    # #8 al calibrar; hasta entonces no hay umbral y nada es "decidido" con
    # aplomo que no tiene.
    umbral: float | None = None
    descripcion: str = ""
    # Las de ejemplo no se usan en produccion: estan para fijar la forma.
    ejemplo: bool = False
    etiquetas: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if self.tipo not in TIPOS:
            raise ValueError(f"tipo desconocido {self.tipo!r}")
        if not self.instrucciones or not self.instrucciones.strip():
            raise ValueError(f"{self.nombre}: las instrucciones son obligatorias")
        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError(f"{self.nombre}: la version es un entero >= 1")
        if self.modelo.endswith("-latest"):
            raise ValueError(f"{self.nombre}: el modelo va fijado, no {self.modelo!r}")
        if self.tipo == "choice":
            if not isinstance(self.opciones, dict) or len(self.opciones) < 2:
                raise ValueError(f"{self.nombre}: un choice necesita >= 2 opciones")
            if len(self.opciones) > 255:  # tope de docs.typesafe.ai/api
                raise ValueError(f"{self.nombre}: un choice admite 255 opciones")
        elif self.tipo == "score":
            if not isinstance(self.opciones, (list, tuple)) or not 2 <= len(self.opciones) <= 10:
                raise ValueError(f"{self.nombre}: un score lleva de 2 a 10 niveles")
        elif self.opciones is not None:
            # En OpenRouter `DecisionsNoulQuestionCriteria` exige las DOS
            # claves; en TypeSafe son opcionales. Se piden las dos.
            if not isinstance(self.opciones, dict) or set(self.opciones) != {"true", "false"}:
                raise ValueError(f"{self.nombre}: los criterios de un noul son true y false")

    def a_api(self) -> dict:
        """La pregunta como la espera `/systemone`."""
        q: dict[str, Any] = {"type": self.tipo, "instructions": self.instrucciones}
        if self.tipo == "score":
            q["criteria"] = list(self.opciones)
        elif self.opciones is not None:
            q["criteria"] = dict(self.opciones)
        return q

    @property
    def huella(self) -> str:
        """Hash de lo que se envia. Un test del catalogo guarda la huella de
        cada (nombre, version): si cambia el texto y no la version, falla."""
        crudo = json.dumps(self.a_api(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(crudo.encode("utf-8")).hexdigest()[:16]


def _cargar() -> dict[str, Pregunta]:
    from shared.preguntas import ejemplo, soft404

    catalogo: dict[str, Pregunta] = {}
    for p in ejemplo.PREGUNTAS + soft404.PREGUNTAS:
        if p.nombre in catalogo:
            raise ValueError(f"pregunta duplicada: {p.nombre}")
        catalogo[p.nombre] = p
    return catalogo


CATALOGO: dict[str, Pregunta] = _cargar()


def obtener(nombre: str) -> Pregunta:
    try:
        return CATALOGO[nombre]
    except KeyError:
        raise KeyError(f"pregunta desconocida: {nombre!r}") from None
