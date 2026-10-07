# Revisión del top 50 por PageRank · 7 de octubre de 2026

Qué se revisa aquí: si el orden que produce el PageRank interno es defendible
delante de un cliente, y si hay algo absurdo arriba (que es como se descubrió
el caso de re-magazine de la decisión 29, donde la home era 1.ª por el logo de
un aviso de navegador antiguo).

Tres censos, los tres re-analizados con el código de M1:

| censo | URLs | job |
|---|---|---|
| blogs.uoc.edu | 34.704 | `531dbc5e` |
| www.uoc.edu | 28.497 internas | `023f6355` |
| progym | 4.320 | `1f929d93` |

---

## Lo primero: el orden no informa igual en los tres

Contando cuántos valores **distintos** hay entre los puestos 2 y 50:

| censo | valores de inlinks únicos | valores de `pagerank_score` | rango del score |
|---|---|---|---|
| www.uoc.edu | **39** | 12 | 87-99 |
| blogs.uoc.edu | **33** | 9 | 88-96 |
| progym | **2** | 3 | 95-97 |

En progym, del 2.º al 50.º **todas las páginas tienen exactamente 3.572
enlaces entrantes únicos y 95 puntos**. No es un fallo del cálculo: es que el
único enlazado interno del sitio es un megamenú que enlaza todas las categorías
desde todas las páginas, así que internamente **todas valen lo mismo**. El
orden dentro de ese bloque es ruido y no hay que leerlo como una prioridad.

En los dos sitios de la UOC sí discrimina, y mucho.

**Conclusión de método:** antes de entregar un «top por PageRank» hay que mirar
cuántos valores distintos tiene. Si el top es un empate, el hallazgo es el
empate.

---

## blogs.uoc.edu — correcto, con un patrón que es hallazgo

**45 de las 50 son 200 e indexables**; las otras 5 son redirecciones.

Arriba están las **home de cada blog del multisite** (`/economia-empresa/ca/`,
`/informatica/es/`, `/ciudad/`, `/edcp/ca/`…) y algunas secciones de blog. Es
lo correcto: en un multisite cada blog es un sitio y su home recibe el menú y
la barra lateral de todos sus posts.

Dos matices que hay que saber al leerlo:

- **El orden entre blogs lo decide el tamaño del blog, no su importancia.**
  `/informatica/es/recursos/` es 6.ª con 236 palabras, por encima de la home de
  cualquier otro blog, porque el blog de informática tiene más posts y su barra
  lateral la enlaza 3.456 veces. Es un dato del sitio, no un error; pero
  jerarquizar por tipo de página es trabajo de #12, no del PageRank.
- **Ninguna página legal en el top 50.** El techo por repetición de la decisión
  29 está haciendo su trabajo: sin él, una política de privacidad enlazada
  desde todos los pies flotaría hasta arriba.

**Hallazgo para el cliente.** Las 5 redirecciones del top 50 son el mismo
patrón: la URL del blog **sin idioma** redirige a la versión con idioma, y es
la que usan los enlaces internos.

| puesto | URL | enlaces únicos | destino |
|---|---|---|---|
| 17 | `/economia-empresa` | 2.228 | `/economia-empresa/ca/` |
| 20 | `/edcp` | 2.212 | `/edcp/ca/` |
| 22 | `/economia-empresa/qui-som/` | 1.149 | `/economia-empresa/ca/…` |
| 33 | `/idiomes` | — | `/idiomes/es/` |
| 39 | `/comunicacio` | — | `/comunicacio/es/` |

Son miles de enlaces internos gastando un salto. Acción: apuntar los enlaces
del menú a la URL final.

---

## www.uoc.edu — el mejor de los tres, y un hallazgo de primer orden

**49 de las 50 son 200 e indexables.** Arriba está el catálogo de estudios
(`/estudis/masters`, `/estudios/grados`, `/estudis/diplomes`), las tres homes
de idioma y la investigación. Para una universidad, el catálogo de titulaciones
*es* el núcleo comercial: un auditor humano ordenaría igual.

**Lo que mejor demuestra que la métrica no cuenta enlaces:** los puestos 11 a
16 tienen entre **12 y 33 enlaces entrantes** y puntúan 90-94, por encima de
páginas con **6.000**. Reciben pocos enlaces, pero desde el hub del catálogo,
que es la página más fuerte del sitio y reparte entre pocos destinos. Es
PageRank funcionando como debe, y conviene explicarlo en el informe porque es
justo lo que un cliente cuestiona.

### El hallazgo: la 2.ª página más fuerte del sitio es `noindex`

`www.uoc.edu/es/estudios/latam/latinoamerica` es la **2.ª por PageRank** y está
en **noindex**.

| | |
|---|---|
| enlaces entrantes únicos | **16.509** |
| instancias por posición | **20.255 en el pie**, 5.246 en el menú, 6 en contenido |
| ancla | «La UOC a Llatinoamèrica» |
| PageRank que acumula | **0,51% de todo el sitio — 144 veces la página media** |

El sitio vuelca la autoridad de **todos** sus pies y menús en una página que le
ha dicho a Google que no indexe. Por el criterio de la decisión 38, esa página
recibe pero no reparte: esa autoridad **se tira**. Para dimensionarlo, el total
que acaba en páginas no indexables en ese censo es el **10,44%**, y esta sola
página es la vigésima parte de todo ese desperdicio.

Acción, y es binaria: o la landing de Latinoamérica tiene que posicionar —y
entonces el `noindex` sobra— o no, y entonces no debería estar en el pie de
16.509 páginas.

---

## progym — el orden es un empate, y eso es el hallazgo

**48 categorías, 1 contacto, 1 home** en el top 50; 49 de 50 indexables.

- Del 2.º al 50.º, **todos con 3.572 enlaces entrantes y 95 puntos**. El
  megamenú enlaza todas las categorías desde todas las páginas: internamente el
  sitio no prioriza nada. La **home es la 33.ª**, empatada con el resto, porque
  el logo la enlaza desde las mismas 3.581 páginas y ni más ni menos.
- **`/contact` es la 3.ª.** No es un error del cálculo: está en la plantilla de
  todo el sitio. En un e-commerce es autoridad que no va a vender nada.
- **La 1.ª del sitio tiene CERO enlaces entrantes.**
  `/crosstraining/fuerza.html` solo se alcanza por un 301 desde
  `/crossfit/fuerza.html`, la URL del esquema antiguo, que es la que todo el
  sitio sigue enlazando. Hereda el PageRank por la arista del salto (decisión
  27). Es el caso que destapó el aviso nuevo
  `solo_enlazada_por_redireccion` (decisión 63).

**Oportunidad, y es la grande:** progym no tiene **ni un solo enlace interno
editorial** hacia sus categorías. Todo su enlazado es menú. Enlazar desde las
fichas y desde el contenido es lo que haría que el PageRank interno dijera algo
sobre qué categorías importan.

---

## Veredicto

El modelo no produce ninguna anomalía en los tres censos: nada absurdo arriba,
las páginas legales no flotan, y los casos llamativos —la `noindex` de la UOC,
la 1.ª sin enlaces de progym, las redirecciones de blogs— son **hallazgos
reales del sitio**, no artefactos del cálculo. En los tres, lo que el top 50
dice de cada sitio coincide con lo que diría un auditor mirando la plantilla.

## Lo que propongo cambiar a raíz de esto

**Decir cuándo el orden no discrimina.** Es barato: guardar en
`jobs.pagerank_resumen` cuántos valores distintos de `pagerank_score` tiene el
top 50 y avisar cuando sean pocos, igual que ya se avisa de `grafo_fiable`. Sin
eso, un cliente lee una prioridad donde solo hay un empate — y en uno de los
tres censos revisados el top entero es un empate. **No lo he implementado:
queda como propuesta.**
