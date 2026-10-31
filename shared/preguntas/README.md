# Cómo se escribe una pregunta a Jev

Guía de estilo del catálogo (#9). Lo que hace cumplir el código está en
`Pregunta` (`__init__.py`); aquí va el resto: lo que se aprendió midiendo, con
la decisión donde se midió. Una pregunta nueva, o una versión nueva, se mide
con `scripts/evaluar_decisiones.py` antes de que su respuesta cuente para nada
(#8, decisión 159).

## Qué se pregunta

- **Un hallazgo por pregunta.** «¿Es un soft 404?» mezclaba dos cosas: un
  error disfrazado se arregla, una página de utilidad no. Se partió en
  `es_soft_404` y `es_pagina_de_utilidad` (169). Si dos respuestas llevan a dos
  arreglos distintos, son dos preguntas.
- **No se pregunta lo que resuelve una regla.** Un title vacío, solo la marca
  o de pocas palabras lo dice el código, sin gastar y sin dudar (177). La regla
  va antes y, si decide, no se llama a Jev.
- **Solo sobre lo sospechoso.** El soft 404 se pregunta a las páginas que las
  reglas ya marcan como dudosas, no a todo el censo (163). Por plantilla
  cuando la respuesta es de plantilla: el tipo de página se pregunta a una
  muestra de cada una y se propaga (178).
- **Lo que depende de otra respuesta se condiciona con `solo_si`** y va en su
  propia llamada: su instrucción da por hecha la otra respuesta y la
  empujaría si viajaran juntas (175).

## Qué tipo

- **`noul`** para lo binario o verificable: «la página muestra un precio».
  Con sus dos criterios, `true` y `false`, escritos.
- **`choice`** con opciones **mutuamente excluyentes**, cada una con su
  descripción corta, y una opción de escape (`other`, `none`) para lo que no
  encaja. Pocas: si la taxonomía es grande, en dos niveles (#12). Cuando dos
  opciones pueden solaparse, **reglas en orden** («la primera que se cumpla»)
  en la instrucción (180).
- **`score`** solo para un grado de verdad ordenado (de 2 a 10 niveles), con
  cada nivel descrito; no para categorías.

## Cómo se redacta

- **Instrucciones en inglés neutro; el estado, en su idioma.** Jev es
  multilingüe, pero la instrucción tiene que ser la misma para todos los
  clientes. Las medidas de 176-185 se hicieron así.
- **Con el criterio de quien revisa.** La pregunta dice lo que una persona
  usaría para etiquetar, no una intuición del modelo: «legal» es lo que un
  revisor llama legal (169). Si la revisión humana discrepa por una
  definición, se cambia la definición, no el juego.
- **Los ejemplos de las instrucciones no salen de los juegos.** Si un title
  del juego aparece como ejemplo, la medida se infla con ese caso. Un test
  compara los ejemplos con las huellas de los juegos (`huellas`, 182).
- **Lo que el estado no puede decir no se pregunta.** Si los fragmentos son
  parciales, un dato que no aparece no es una contradicción: un `lowPrice` es
  un mínimo, no un precio exacto (184).

## Versiones

- **Cambiar el texto es cambiar la versión.** La versión va en la clave de la
  caché; un texto nuevo con la versión vieja recibe las respuestas del texto
  viejo. La huella de cada (nombre, versión) está en `HUELLAS` y un test lo
  vigila.
- **Antes de medir un borrador, súbele la versión.** Dos borradores no
  comparten número: el de la 184 se midió con la versión de otro y recibió sus
  respuestas de la caché (coste 0, medida falsa).
- **El modelo va fijado** (`typesafe/jev-1.13`), nunca `-latest`.

## Cuándo cuenta una respuesta

- **Sin umbral, todo es `incierto`.** El umbral sale de `evaluar` sobre un
  juego etiquetado por personas (a ciegas, en «Revisar a Jev», 211): la
  confianza más baja con la que lo decidido llega al 90 % con la cota de
  Wilson, con al menos 35 ejemplos por encima (los que hacen falta para que un
  100 % demuestre el 90 %, 231).
- **Una respuesta rara se mide con un juego de precisión** (179): en un juego
  aleatorio salen demasiado pocas para decir nada.
- `evaluar --informe` guarda el informe junto al juego (`INFORME_v<n>.md`):
  acierto por clase, calibración, curva de cobertura, umbral y coste.
