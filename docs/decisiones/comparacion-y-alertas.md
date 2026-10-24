# Decisiones: comparación de censos, alertas y avisos

> Movidas tal cual desde `CLAUDE.md` (2026-10-14). Indice de todas las
> decisiones en [`README.md`](README.md). La numeracion es historica y se
> conserva: los commits, issues y el resto de decisiones citan por numero.

<a id="d67"></a>
67. **Comparar dos censos: lo que no se puede afirmar pesa más que lo que
   sí** (#32) — `shared/comparacion.py` responde «qué cambió entre estos dos
   rastreos del mismo sitio», y vive en `shared/` porque lo necesitan la API
   (que no lleva `analysis/` en su imagen) y el worker. Lo interesante son las
   cuatro cosas que se **niega** a decir:
   - **Un censo truncado no permite afirmar ausencias.** Si uno paró por
     `max_urls`, por tiempo o estancado, «han desaparecido 4.000 URLs» es
     falso: no se llegó a ellas. El resultado se marca no concluyente y las
     ausencias se cuentan aparte, sin afirmarlas.
   - **Dos orígenes distintos no se comparan**, y la comprobación es por HOST
     y no por dominio registrable: `pre.x.com` y `x.com` comparten dominio
     pero no casa ni una URL, así que el resultado sería «ha desaparecido el
     sitio entero». Con `map_host=pre.x.com=x.com` sí, porque entonces es
     explícito. Comprobado contra datos reales: se niega a comparar el censo
     de Saunier con el de Lopesan.
   - **Si no se sabe con qué versión se hizo alguno de los dos, se dice.**
     Desconocido no es igual: callarse equivale a afirmar que se hicieron con
     el mismo código. Medido con dos censos reales de progym anteriores al
     sello (decisión 66): **439 páginas cambian su recuento de palabras y el
     cambio es NUESTRO** —la extracción de contenido de M1—, no del sitio.
   - **La barra final SÍ hace dos URLs.** `/a` y `/a/` pueden servir cosas
     distintas y normalmente una redirige a la otra; emparejarlas taparía que
     un sitio ha cambiado de convención. El orden de los parámetros no, que
     eso sí es la misma URL.
   Compara `status_code`, `indexability_status`, `canonical_href`, `title`,
   el primer h1 que se pinta y `word_count` (con un umbral del 20%, que por
   debajo es ruido de plantilla). Solo páginas HTML: el `title` de un PDF no
   dice nada. Endpoint `GET /api/jobs/{id}/diff/{otro_id}` y pestaña
   **Comparar** en la ficha del rastreo, que solo ofrece rastreos completados
   del mismo host.
   El umbral de palabras se mide sobre el **mayor** de los dos valores, no
   sobre el «antes»: si no, comparar A con B y B con A da resultados
   distintos. Lo vi usando la vista con dos censos reales de progym —1.639
   páginas en un sentido y 439 en el otro, con los mismos datos—, y pasar de
   100 a 130 palabras es el mismo cambio que de 130 a 100.
   «Antes» y «ahora» los pone la **fecha**, no el orden de los parámetros: una
   página que sale del índice y una que entra son hallazgos opuestos, y el
   endpoint los rotulaba por el orden de los argumentos, así que elegir el
   censo viejo en el desplegable daba la vuelta al informe entero.

<a id="d68"></a>
68. **Un cambio que afecta a una parte grande de las indexables no es trabajo
   editorial** — es una plantilla, una configuración o un despliegue, y tiene
   que verse como una alerta, no como una fila más del diff. En Lopesan, 2.367
   páginas canonicalizadas a `webserver-lopesan-prd.lfr.cloud` se entregaron
   como un aviso `info` entre 315.119 incidencias, el 91% de ellas
   `image_missing_alt`. `shared/comparacion.py` emite nueve reglas, cada una
   con **qué decide Google** (regla 0) y con su denominador: «2.367 páginas» no
   dice nada sin «de 2.379», y el conjunto no es el mismo para todas (una
   página que ENTRA en el índice se cuenta sobre las que no estaban).
   Umbral: 5% de las indexables con un piso de 20 páginas —en un censo de 30
   URLs «el 10%» son tres— **o** el 50% de una forma de ruta, porque una
   plantilla entera rota se diluye en el total: las 2.367 de Lopesan son el
   99,5% de su plantilla y el 11% del sitio. Un aviso por hallazgo (decisión
   36): la página que pierde el índice *porque* le pusieron un canonical fuera
   no se cuenta además en `salen_del_indice`.
   Medido contra seis parejas de censos reales. El control —Lopesan 8-sep
   contra 9-sep, un día de diferencia— **no dispara nada**, y Saunier con un
   mes de diferencia tampoco; el fallo real dispara `canonical_a_otro_host`
   con **2.319 de 4.193 (55,3%)** y plantillas enteras al 100%.
   `canonical_a_otro_host` es la única que NO exige que la página fuera
   indexable: lo que abandona el sitio es el **destino** de la consolidación.
   De las 2.367 de Lopesan, **1.832 ya estaban canonicalizadas** a una URL
   legítima, así que la regla de «era indexable y deja de serlo» las descartaba
   y la alerta decía 476 de 2.271 (21%) en vez de 55,3%.

<a id="d70"></a>
70. **Dos rastreos del mismo host pueden ser dos censos distintos** — el
   guardia de #32 mira el host (decisión 3) y los dos censos de cst.gov.sa del
   mismo día lo pasaban: uno sembrado en el árbol castellano y otro en el
   inglés, **0 semillas en común de 4.780**. La comparación afirmaba que
   **habían desaparecido 834 páginas** que nunca estuvieron en el alcance.
   Las ausencias solo se afirman si **cada semilla del censo anterior está
   también en el posterior**. No es un umbral, es una contención, y separa los
   casos sin ajustar nada: Lopesan 2.935/2.935, el canario de penguin
   1.950/1.950, CST 0/4.780. La Jaccard no sirve aquí — el canario de penguin
   da 0,022 contra su censo completo y es un subconjunto limpio. Las semillas
   viajan al comparador pero no a la respuesta (87.429 en penguin): queda
   `n_semillas`. Y si los dos censos difieren en `render_js`, un aviso: el que
   renderiza ve enlaces que el otro no, que es lo que mide `js_check`
   (decisión 35). Eso último no está medido —ninguna pareja de censos difiere
   en el render— y sale de ese mecanismo, no de una cifra.

<a id="d71"></a>
71. **Una alerta a la que hay que ir no es una alerta** (#36) — la comparación
   de #32 solo existía si alguien entraba en la pestaña y elegía dos censos en
   un desplegable. Nadie hace eso para enterarse de que media sección se ha ido
   del índice: en Lopesan pasó y se entregó como un aviso `info` entre 315.119
   incidencias. Ahora, al cerrar un rastreo, el worker lo compara con el censo
   anterior del mismo sitio (`_comparar_con_el_censo_anterior`, junto a
   `_comprobar_render_js`), guarda el veredicto en `jobs.comparacion` y lo pinta
   **arriba del todo** en la ficha del rastreo, antes de las cifras. Las alertas
   críticas suben además al log del worker.
   Es best-effort a propósito: si falla, el job NO se marca fallido. Lo
   rastreado y lo analizado valen igual, y la comparación se puede pedir
   después por el endpoint; lo contrario sería dejar en `failed` un censo
   correcto por no poder compararse con otro.
   El censo de referencia se busca **en lotes, de más reciente a más antiguo**,
   no con un `limit`: con un tope fijo, en una instalación con varios clientes
   los rastreos de los demás se comen la ventana y el censo anterior de ESE
   sitio queda fuera — y la ausencia de alerta se lee igual que «no ha cambiado
   nada». El host no se puede filtrar en SQL sin atarse a Postgres, porque
   `seeds` es JSON y la primera semilla no es una columna.
   Y las dos consultas que leen un censo (`paginas_de_censo`, `resumen_de_job`)
   se mudan del router a `shared/comparacion.py`: las necesitan el endpoint y
   el worker, y tenerlas por duplicado es como se llega a dos cifras que dicen
   medir lo mismo y no coinciden (decisiones 23 y 39).

<a id="d77"></a>
77. **Una alerta que espera a que alguien entre no avisa** — la última pieza
   de la cadena, y la que faltaba: el aviso crítico salía en la ficha del
   rastreo y había que abrir el panel. El fallo de Lopesan lo pillamos porque
   re-rastreamos ese día por otro motivo. `shared/avisos.py` manda un
   **webhook genérico** cuando la comparación encuentra alertas críticas.
   Genérico y no una integración con Slack ni un servidor de correo por dos
   razones: el VPS ya ejecuta n8n, así que desde ahí se enruta a donde haga
   falta sin meter credenciales en el crawler, y el cuerpo lleva `text` con el
   resumen, de modo que apuntarlo a un *incoming webhook* de Slack también
   vale. Con `urllib` y no `requests`: una dependencia menos en el camino que
   corre después de CADA rastreo.
   Dos criterios: **solo las críticas** —un aviso que llega cada semana con
   cosas que no hay que mirar se deja de leer, y entonces no avisa de nada— y
   el aviso dice **qué pasa y sobre cuántas páginas**, con lo que decide
   Google, no «hay 3 alertas»: uno que obliga a abrir otra cosa para
   entenderlo avisa a medias, que es el problema que viene a resolver.
   Best-effort con el `try` cubriendo la función entera (decisión 74): esto
   corre al cerrar un rastreo, y que un webhook caído dejara un censo en
   `failed` sería cambiar un problema por otro peor.

<a id="d78"></a>
78. **Un rastreo programado se compara con el anterior de SU serie** — la
   regla «el censo completado más reciente del mismo host» elige mal en cuanto
   alguien lanza algo a mano. Comprobado en producción el 2026-10-09: el
   segundo canario de Lopesan se comparó con el primero —**117 URLs en común
   de 250**— en vez de con el censo completo. Llevado a un rastreo semanal de
   5.000 páginas, se compararía contra un canario de 250 que alguien lanzó el
   martes.
   Y no es cosmético: **las alertas se miden sobre las páginas que están en
   los dos censos**. Si la referencia cubre 250 páginas y el fallo afecta a
   2.319 que no están entre ellas, la alerta NO salta. El guardia de alcance
   (decisión 70) evita afirmar ausencias falsas, pero no evita ese silencio, y
   un silencio se lee igual que «no ha cambiado nada» — que es contra lo que
   existen las decisiones 67 a 77.
   `jobs.programacion_id` liga cada rastreo con la programación que lo lanzó.
   La primera ejecución de una serie cae a la regla del host, y un rastreo
   lanzado a mano se queda con esa regla porque es lo único que se sabe de él.

<a id="d86"></a>
86. **El respaldo elige por ALCANCE, no por fecha** — la otra cara de la
   decisión 78, y me la encontré el mismo día en producción: el primer censo
   semanal de Lopesan (3.040 semillas) eligió como referencia un **rastreo de
   prueba de 40 URLs** de cuatro días antes, porque era el más reciente de ese
   host. **40 URLs en común de 3.884.** Los guardias hicieron su trabajo —no
   concluyente, sin alertas, nada falso afirmado— pero con 40 páginas en común
   no se habría visto nada **aunque el sitio se hubiera roto**, y un silencio
   se lee igual que «no ha cambiado nada».
   Entre los candidatos del mismo host se prefiere el que **abarque** lo
   mismo: que sus semillas cubran las de este censo, que es la contención de
   la decisión 70. Si ninguno la cumple se devuelve el más reciente, que es lo
   de antes — comparar con algo del mismo sitio sigue siendo mejor que no
   comparar con nada. Un candidato que abarca MÁS también vale: si cubre todas
   mis semillas y alguna más, las páginas comunes siguen siendo todas las
   mías.

<a id="d88"></a>
88. **Comparar páginas no contesta «qué arregló el cliente»** (#32) — la
   comparación entre censos miraba `status_code`, canonical, título, h1 y
   palabras, que dice qué cambió en el SITIO. Lo que se mira en un seguimiento
   mensual es otra cosa: qué incidencias se arreglaron, cuáles son nuevas y
   cuáles siguen ahí. Eso exige identificar «el mismo hallazgo» entre dos
   censos, y es `(issue_type, url, group_key)`: con tipo y URL no basta porque
   un hreflang mal escrito emite una incidencia **por código de idioma** y un
   título duplicado pertenece a un grupo.
   `issues.group_key` lo rellenan las **once reglas** que emiten más de una
   incidencia por URL o que agrupan URLs (duplicados de título, description,
   h1 y contenido; los tres de hreflang; datos estructurados; imágenes;
   cabeceras por host; canonical a otro dominio). Es un SHA-256 truncado a 16
   del valor normalizado —caja y espacios, como el `content_hash` de la
   decisión 47— porque una description duplicada puede tener 1.000 caracteres
   y la clave se repite en cada fila del grupo. Las reglas que emiten como
   mucho una incidencia por URL van a NULL a propósito: ahí el par (tipo, URL)
   ya identifica el hallazgo, y una clave inventada puede cambiar entre
   versiones del código y fingir que el problema se resolvió. Los casi
   duplicados tampoco llevan clave: no pertenecen a un grupo estable sino a un
   conjunto de parecidos que cambia cada censo.

<a id="d89"></a>
89. **Una incidencia cuya página ya no está en el censo NO está resuelta** —
   el guardia del censo truncado (decisión 67) no basta, y esto fue lo que más
   cerca estuvo de acabar en un informe de cliente. Medido con dos censos
   reales de progym que terminaron los dos enteros, pero en los que
   desaparecieron 1.205 URLs: el diff decía **4.794 resueltas**, y las tres
   primeras clases —1.152 `noindex_page`, 1.146 `canonical_missing`, 938
   `hreflang_missing_return`— eran páginas que sencillamente no se volvieron a
   visitar. Con el arreglo: **125 resueltas** y 4.669 en «no se sabe».
   Decirle a un cliente que arregló cuatro mil cosas que no tocó es peor que no
   darle ninguna cifra. Y el diff de incidencias va dentro de su propio `try`:
   las alertas críticas de página —media sección fuera del índice— son lo que
   hay que entregar aunque el recuento falle.

<a id="d91"></a>
91. **Dos censos analizados con código distinto no se comparan por
   incidencias** — un aviso no basta. La decisión 72 ya avisaba de que las
   versiones diferían, y el aviso no impide que la cifra sea falsa ni que
   alguien la copie a un informe. Medido el 2026-10-13, el día que se añadió
   `group_key` (decisión 88): el censo de referencia de Lopesan tenía **11.414
   incidencias y CERO claves**, y el siguiente las tendría todas. Ningún
   hallazgo agrupado —duplicados, hreflang, imágenes, datos estructurados—
   habría casado, y los miles afectados habrían salido como «resuelto» **y**
   «nuevo» a la vez, en el primer informe automático.
   Es la misma regla que las ausencias de un censo truncado (decisión 67): lo
   que no se puede afirmar, no se afirma. Y **desconocido tampoco vale**: un
   censo anterior al sello pudo analizarse con cualquier cosa, y callarse
   equivale a decir que fue con el mismo código.
   El motivo que se devuelve dice **cómo arreglarlo** —volver a analizar el más
   antiguo con el código de hoy—, porque es barato: 246 s en ese censo. Un «no
   se puede» sin salida convierte la función en un adorno. La comparación de
   PÁGINAS sigue, y con ella las alertas críticas: esas salen del rastreo, no
   del análisis.

<a id="d93"></a>
93. **Callarse no basta: el censo de referencia se pone al día solo** — la
   decisión 91 evita la mentira y deja la función **inservible en la
   práctica**. Se despliega a diario, así que entre dos rastreos de una serie
   semanal casi siempre habrá cambiado el analizador. Visto en el **primer
   rastreo automático** de Lopesan, el 2026-10-14: los dos censos salieron con
   `6ae6004` y `c5d863e` y el diff de incidencias no dio ni una cifra. Todo lo
   demás funcionó —se disparó solo a las 03:00, 5.281 URLs, eligió el censo de
   su propia serie, sin alertas— y la parte que más trabajo costó fue la única
   que no sirvió.
   Ahora, si el censo de referencia se analizó con otro código, el worker lo
   **vuelve a analizar** antes de comparar. Cuesta 246 s en ese censo, contra
   las 3 h del rastreo que acaba de terminar.
   Lo que NO es gratis: **cambia las cifras guardadas del censo anterior**. Si
   alguien exportó un CSV, ya no cuadrará. Por eso viaja en el resultado como
   `anterior_reanalizado`, se dice en la ficha del rastreo y se puede apagar
   con `REANALIZAR_LA_REFERENCIA=0`. Si no se re-analiza cuando hacía falta, se
   vuelve al comportamiento de la 91 —sin cifras de incidencias, con las
   alertas de página intactas— y el porqué va en `anterior_sin_reanalizar` y
   en el motivo del diff de incidencias, que es lo que se pinta.
   - **El analizador lo pasa el worker** (`comparar_con_el_anterior(db, job,
     reanalizar=run_analysis)`); `shared/comparacion.py` no importa
     `analysis/`, porque lo carga también la API y su imagen no lo lleva
     (decisiones 69 y 71). La primera versión lo importaba con un
     `except ImportError`, y su test, al parchear
     `analysis.analyzer.run_analysis`, importaba el analizador entero y
     tumbaba el trabajo de la API en CI (sin numpy).
   - **Que `run_analysis` vuelva no es haber analizado**: si otro proceso
     tiene el candado del job se retira sin hacer nada. Manda el sello
     `analisis_version` que deja `run_all` al terminar, releído de la BD.
   - **Un censo con los enlaces archivados (decisión 95) no se re-analiza
     nunca desde aquí** (cambiado en la decisión 150: ahora se re-analiza y
     `run_analysis` restaura y vuelve a archivar). La referencia es por definición un censo superado,
     que es justo lo que se archiva, y sin filas en `links` el análisis
     pondría a cero entrantes, PageRank y profundidad y llenaría el censo de
     huérfanas. Tampoco se restaura: devolver a `links` lo que el archivo
     sacó (restaurar www.uoc.edu son 253 s; Druni, 139 M de filas) para una
     cifra de un informe deshace la 95. Se vuelve a la 91 con el motivo y los
     dos comandos que lo arreglan (`archivar_enlaces.py --restaurar` y
     `python -m analysis.analyzer`). En el flujo normal no llega a pasar: el
     worker compara **antes** de archivar, y `candidatos` no archiva nada de
     menos de `ARCHIVAR_DIAS_MINIMOS` ni el último censo de una programación.
     Lo que sí podía pasar es la carrera: el cierre de OTRO rastreo del mismo
     sitio archivando la referencia **mientras** se re-analiza. Por eso
     `archivo_enlaces.archivar` toma el mismo candado que el análisis
     (`shared/candados.py`, advisory lock de transacción hasta el commit que
     borra los enlaces): si se está analizando, no archiva (`SeEstaAnalizando`,
     la siguiente pasada lo hará); si se está archivando, el análisis no entra
     y luego ve la marca y se niega. Probado contra Postgres en
     `test_no_se_archiva_un_censo_que_se_esta_analizando`, que falla quitando
     el candado.

<a id="d127"></a>
127. **Lo extraído a medida también se compara entre censos, y las alertas
   saltan por segmento de negocio, no solo por forma de ruta** (Ola 3.4 y lo
   que quedaba de 3.3). Dos piezas en `shared/comparacion.py`, las dos dentro
   de `comparar_censos` y por tanto en `jobs.comparacion` (el worker) y en
   `GET /diff` (la API):

   - **(a) Los valores de `custom_extraction` (104) son campos comparables.**
     Por cada regla, sobre las páginas comunes y sin los muros (103): qué
     cambia de valor (`extraido_cambia`), qué deja de encontrarse en la misma
     URL (`extraido_desaparece`), qué aparece (`extraido_aparece`, info) y,
     para una búsqueda, qué deja de contener o pasa a contener lo buscado
     (`busqueda_deja_de_contener`, `busqueda_pasa_a_contener`, info). Cada una
     con su denominador: un cambio se cuenta sobre las que tenían valor en los
     dos, una desaparición sobre las que lo tenían antes. Mismo umbral que la
     68 (5 % con piso de 20, o el 50 % de una plantilla o un segmento). Un
     número se compara como número y la alerta dice cuántos suben, cuántos
     bajan y la variación mediana; un texto, las cinco transiciones más
     comunes («InStock → OutOfStock: 2.300»). Son `warning`, no `critical`: no
     es una decisión de Google por sí misma, y el webhook (77) solo lleva las
     críticas.
   - **Lo que no se puede afirmar, no se afirma**, con cuatro guardias propios:
     (1) solo se compara una regla que exista **con el mismo nombre en los dos
     censos** (`jobs.config`, `reglas_a_medida`), y la que falta en uno sale en
     `extraido.no_comparadas` con el motivo; (2) **ni con el mismo nombre si la
     definición cambió** (otra expresión, otro `devuelve`, otro separador):
     una diferencia puede ser de la regla y no del sitio, la idea de la 91 con
     la extracción; el rótulo y `solo_urls` no cuentan (el alcance ya lo marca
     la falta de fila), y solo se comparan los campos que están en los dos
     `config` para que un config guardado sin los valores por defecto no se
     lea como «la regla cambió»; (3) **sin fila no es vacío** (104): una URL
     que tiene fila en un censo y no en el otro (pasó a 404, cayó fuera de
     `solo_urls`, se rastreó antes de la regla) no es un cambio y se cuenta
     como `sin_dato`, y solo `ok` y `sin_coincidencia` son un dato —
     `tiempo_agotado`, `pagina_bloqueada`, `incompleta`… son `sin_saber`—; (4)
     un valor del DOM renderizado y otro del HTML de la respuesta
     (`custom_extraction.render`) no leen la misma página: `render_distinto`.
     Los guardias de página siguen: censo bloqueado, sin alertas (103); sin
     fecha en los dos, sin alertas (67); el truncado y el de alcance (67, 70)
     solo afectan a las ausencias, y aquí no se afirma ninguna. La 91 no se
     aplica: el valor lo escribe el rastreo, no el análisis, y la diferencia de
     versión del crawler ya va en los avisos (66).
   - **(b) Las alertas de la 68 saltan también por segmento** (`urls.segmento`,
     125). Las bases se cuentan por (forma de ruta, segmento) y cada alerta
     lleva `segmentos` además de `plantillas`, con `por` = `sitio`,
     `segmento` o `plantilla`. Solo los segmentos que pone una regla: el de
     repuesto es la forma de la ruta, que ya es la plantilla, y `(externa)` o
     `(recurso)` no son páginas del sitio. **Un aviso por hallazgo** (36): si
     las páginas de un segmento son exactamente las de una plantilla (la regla
     `^/blog/` y la forma `/blog/:slug`), sale una vez, como segmento y con la
     plantilla al lado (`plantilla`), y la plantilla no se repite en su lista.
     El segmento es el del censo **anterior**, nunca el del nuevo: una regla
     de segmento puede leer `indexable`, el código o un valor extraído (125), y
     en el censo nuevo «no indexables» o «agotados» son justo las páginas que
     cambiaron, así que 25 de 1.000 fichas fuera del índice salían como
     «no indexables 25/25 = 100 %». El porcentaje se cuenta sobre la población
     de antes del cambio. Si el anterior no tiene segmento (previo a la
     columna, sin re-segmentar) no se usa el del nuevo: queda la plantilla y un
     aviso que dice cómo re-segmentarlo. (Lo encontró la revisión de la PR,
     con dos tests que fallaban: el segmento sacado del resultado.) El caso que lo justifica, en test: 40 fichas repartidas en 40
     formas de ruta en un sitio de 1.000 páginas cambian todas de título; sin
     segmentos no salta nada (ninguna plantilla llega al piso de 20 y el sitio
     se queda en el 4 %), con ellos salta «fichas 40/40 = 100 %».

   **Medido** (2026-10-18, base local en solo lectura). **No hay ningún censo
   con extracción a medida**: la base local no tiene aún la tabla
   `custom_extraction` ni `urls.segmento`. Así que la parte (a) va probada con
   fixtures (`tests/test_comparar_extraido.py`, 21 casos, dos de ellos ejecutando
   `comparar_con_el_anterior` contra SQLite), y contra datos reales se
   midió simulando dos extracciones con lo que ya se guarda —`og:type` y
   `og:title` de `html_meta`, con fila solo en las HTML 2xx como haría la
   104— y los segmentos aplicando `shared.segmentos` en memoria sobre cuatro
   parejas de censos:

   | pareja | comunes | cambios de `og:title` / `og:type` | alertas sin → con segmentos |
   |---|---|---|---|
   | Lopesan 27-jul contra 27-jul (control) | 7.082 | 0 / 0 de 5.682 | 0 → 0 |
   | Lopesan 27-jul contra 6-oct | 4.157 | 4 / 0 de 4.133 | 2 → 2 |
   | progym 6-oct contra 7-oct | 3.592 | 0 / 0 de 3.581 | 1 → 1 |
   | progym 6-oct contra 7-oct (M1 + nofollow) | 4.176 | 0 / 0 de 4.170 | 1 → 1 |

   Ninguna alerta de extracción salta por la variación normal de un sitio, y
   los segmentos no añaden ni quitan ninguna alerta en esas parejas: cambian
   cómo se leen. La de Lopesan es el fallo de la 68 (`canonical_a_otro_host`,
   2.333 de 4.157) y ahora dice **«hoteles 2.151 de 2.167, 99,3 %»** en vez de
   solo `/:idioma/hotels/spain/:slug (8n) 199/199`, con el segmento escrito por
   ruta (`^/(es|en|de)/(hoteles|hotels)/`). Comparar cuesta 0,2-0,5 s en esas
   parejas; leer `custom_extraction` es una consulta por `job_id`
   (`ix_cext_job_nombre_estado`), sin unir con `urls` (37), y no está medida
   contra una tabla real.

   **Fuera de esta PR**: la columna `cambios` del Explorador (`?cambios=`) no
   mira la extracción; la evolución por segmento entre más de dos censos (5.3).

<a id="d128"></a>
128. **Una lista de URLs clave es un censo, y su veredicto no puede decir
   «pasa» sin saberlo** (Ola 5.5). La pregunta de un CI de despliegue es
   «¿estas 50 URLs siguen respondiendo, indexables y con su canonical?», y
   ninguna pieza la contestaba:
   - **El modo lista es `max_depth=0`.** El spider ya no seguía enlaces con
     profundidad 0 (`depth < max_depth`), pero la API lo rechazaba (`ge=1`), el
     formulario lo convertía en 3 (`parseInt(...) || 3`), el sitemap se leía por
     defecto —una lista de 50 sembraba el sitemap entero a profundidad 1— y al
     **reanudar** se rastreaba la frontera, que en una lista son los destinos de
     los enlaces de las semillas. Ahora `JobConfig` admite 0, con 0 apaga
     `use_sitemap` **si la clave no viene** (pedirlo explícitamente vale, y por
     `__dict__` para que el `max_depth: 0` de un job no pise el `use_sitemap`
     de su perfil al fusionar), y una lista no rastrea la frontera. El destino
     de una semilla que redirige sí se rastrea: una redirección no es un clic
     (decisión 27).
   - **Umbral absoluto en listas**: `analysis_thresholds.alerta_lista_min_paginas`
     (por defecto **1**, configurable por cliente, perfil o job). En una lista
     una alerta salta con ese número de páginas, sin el 5 % ni el piso de 20 de
     la decisión 68: ese piso existe para no alertar por la variación normal de
     un sitio de miles de páginas (decisión 13), y en una lista cada URL la
     eligió alguien. Con él, 10 de 50 URLs clave en noindex **no alertaban**.
     En `shared/comparacion.py` es un parámetro (`umbral_absoluto`) y la
     alerta sale con `por: "lista"`; el resto vive en `shared/modo_lista.py`.
   - **La referencia de una lista se recorta a la lista** (`acotar_a_la_lista`):
     si es el censo completo del sitio, sus demás páginas no «desaparecen», no
     se pidieron. Sin el recorte, el guardia de alcance (70) lo dejaba pasar
     —la semilla del completo, la home, está en la lista— y la comparación
     habría dicho «desaparecen» miles de páginas.
   - **`GET /api/jobs/{id}/veredicto`**: `pasa`, `falla` o `no_concluyente`,
     con `motivos` y `no_concluyente_por`. **200 siempre** que el job exista (404
     si no): un CI no puede confundir «el despliegue está roto» con «la API se ha
     caído». `falla` = algo afirmable: una alerta crítica de `jobs.comparacion`,
     o una URL de la lista que no responde 200, que robots.txt prohíbe, que era
     indexable y ya no lo es, o que cambia de canonical (una causa por URL,
     decisión 36: si cambia el canonical no se cuenta además como «deja de ser
     indexable»). Un rastreo `failed` por `robots_bloquea_todo` (decisión 32)
     es `falla`, no duda: es el `Disallow: /` de preproducción llegando a
     producción. **Lo no concluyente no es `pasa`**: sin terminar, fallido,
     sin referencia, truncado, URLs de la lista sin rastrear o sin referencia, y
     un censo **bloqueado**, que es `no_concluyente` aunque todo dé 403 porque
     esos códigos son los del muro (103). Las alertas miden el censo (también
     uno que no es lista) y los fallos la lista, pero **un hallazgo es un
     motivo** (36): si la muestra de una alerta crítica está en el grupo URL a
     URL que dice lo mismo (`rotas` → `no_responde_200`, `salen_del_indice` →
     `deja_de_ser_indexable`, los dos de canonical → `cambia_el_canonical`) y
     ese grupo tiene al menos sus páginas, la alerta no se repite: su regla y
     lo que decide Google viajan con el grupo. En una lista pasa siempre —la
     comparación se acota a ella— y el CI contaba 20 problemas donde había 10.
   - **La referencia de cada URL es su último estado que pasó, no el censo
     anterior.** Comparar solo con el anterior pone en verde lo que sigue
     roto: el CI falla, alguien relanza la lista, el censo nuevo se compara con
     el roto, no hay cambio y sale `pasa`. Se sigue la cadena de
     `comparacion.comparado_con` (10 censos como mucho) y el estado de una URL
     solo pasa a ser su referencia si ese censo no fallaba en ella; un noindex
     intencionado falla hasta que la URL sale de la lista. Fuera de una lista,
     solo el anterior.
   - **Una lista no es referencia de un rastreo que sigue enlaces**
     (`censo_anterior`). La lista de QA suele llevar la home, así que sus
     semillas «abarcan» las de un censo sembrado en la home (decisión 86) y,
     siendo la más reciente —corre tras cada despliegue—, ganaba: el censo
     completo medía sus alertas sobre 50 páginas en común, el silencio de las
     decisiones 78 y 86. Al revés sí vale: una lista se compara con un
     completo, recortado.
   - **El formulario con un perfil en modo lista** precarga la configuración
     PARCIAL, sin `use_sitemap`, y la casilla marcada por defecto mandaba
     `use_sitemap: true` explícito: ahora `_formDesdeConfig` la desmarca.
   - **En una lista, la comparación «no concluyente» no es duda** si cada URL
     de la lista tiene referencia: lo que protege esa marca —afirmar ausencias
     que el anterior, truncado, quizá no vio— se mira URL a URL. Medido: con
     los canarios truncados de blogs.uoc.edu (`4eaf885d`) y www.uoc.edu
     (`95ecd56d`) como referencia de sus censos completos, una lista de las 50
     URLs indexables de más PageRank salía `no_concluyente` con las 50
     presentes y sin un cambio; ahora `pasa`.

   Medido sobre `crawler_db` local en solo lectura, con la lista = las N
   indexables de más `pagerank_score` del censo anterior. Control —progym
   6-oct contra 7-oct (`8c513c30` → `1f929d93`)—: con N=50 y N=500, **0 alertas
   críticas** con umbral 1 y veredicto `pasa`; sí sale `pierden_contenido`
   (warning, 48 de 50 y 148 de 500), que es el cambio NUESTRO de la extracción
   M1 entre esos dos censos (decisión 67) y no entra en el veredicto, que solo
   mira las críticas. Fallo simulado sobre el mismo par —10 de las 50 a
   `Noindex` en memoria—: sin umbral absoluto **0 alertas críticas**; con él,
   `salen_del_indice` **10 de 50** y veredicto `falla` (un motivo,
   `deja_de_ser_indexable` 10, con la regla de la alerta). Comparar y juzgar una lista de 500 cuesta 0,2 s.
   La base local va por detrás del esquema (sin `health`, `response_class` ni
   `canonical_distintos`), así que se midió con SQL directo y esas columnas a
   NULL: lo que dependa de muros y canonicals múltiples no está medido sobre
   datos reales, solo en tests.

   Queda abierto: `censo_anterior` sigue eligiendo por alcance de semillas
   (86), así que la primera ejecución de una lista cae al censo más reciente
   del host —que con el recorte funciona si ese censo vio las URLs, y si no
   sale como `sin_referencia_url`—; el diff manual (`/diff/{otro}`) no aplica el
   modo lista; y con umbral 1 los avisos `warning` (título, contenido) de una
   lista saltan con una página: no van al veredicto ni al webhook, pero sí a la
   ficha.

<a id="d130"></a>
130. **La evolución de un perfil se agrega por ejecución, sin leer `links`, y
   un punto que no es un censo entero no hace línea** (Ola 5.3, #36 F3). La
   comparación de #32 contesta «qué cambió entre estos dos»; la pregunta del
   seguimiento mensual es otra: ¿cómo va este sitio desde que lo llevamos?
   Contestarla leyendo cada censo cada vez que alguien abre la ficha del
   cliente es leer `urls`, `issues` —y, para la profundidad o el PageRank,
   `links`— de 20 o 50 censos.
   - **Una fila por ejecución en `profile_runs`** (`shared/evolucion.py`):
     URLs internas por grupo de estado (2xx, 3xx, 4xx, 5xx, bloqueadas),
     páginas HTML 2xx e indexables (el criterio de `/stats` y de la serie),
     clics medios desde la home de las indexables con camino y cuántas no lo
     tienen (90), incidencias por severidad, URLs con error y los 10 tipos más
     frecuentes, el PageRank desperdiciado e indexable de
     `jobs.pagerank_resumen` y las dos versiones. Tres consultas por `job_id`
     —una sobre `urls`, dos sobre `issues`— y **ninguna sobre `links`** (37):
     el test captura el SQL que se ejecuta y falla si aparece la tabla.
   - **Solo cifras.** De quién es (cliente, perfil, programación), su tipo y
     su estado se leen de `jobs` al pedir la evolución: un job se puede
     reasignar o re-etiquetar después (`PATCH /api/jobs/{id}`), y una copia
     aquí se quedaría desfasada. `analisis_version` sí se copia: es la versión
     con la que se contaron ESTAS incidencias, y si ya no coincide con la del
     job el punto sale `desfasado` hasta que se rehaga. El host de la primera
     semilla también se copia (`profile_runs.host`), porque las semillas no
     cambian y es lo único que hace falta de ellas: leer `jobs.seeds` de cada
     ejecución para nombrar la serie por host costaba **233 kB por job** en el
     censo de Lopesan sembrado desde el sitemap (3.040 semillas), y la portada
     lee todos los terminados de todos los clientes. Solo los rastreos sin
     agregar y sin perfil ni programación leen sus semillas.
   - **Quién la escribe.** `run_analysis` al terminar —cubre el análisis
     automático, el re-análisis a mano y el de la referencia (93), que cambia
     las cifras de un censo viejo— y el worker al cerrar el rastreo, para lo
     que no se analiza (un rastreo bloqueado o sin URLs, 28, 32, 103) o cuyo
     análisis reventó: también son un punto de la serie, pintado aparte. El
     worker no recuenta si la fila ya es de la versión del análisis actual;
     solo pone al día la foto del estado. Las dos llamadas son best-effort con
     el `try` cubriendo la función entera, import incluido (74, 77): un fallo
     no deja `failed` un censo correcto (test que hace reventar el registro y
     comprueba que el análisis se sella igual).
   - **Lo que no es un punto comparable no hace línea ni escala**: fallido,
     estancado, truncado (los motivos de la decisión 67), canario o prueba,
     lista (128), sin agregar o desfasado. Se devuelven con `comparable=false`
     y su motivo, y la gráfica los pone abajo con una ×. Un rastreo bloqueado
     de 3 URLs en mitad de una serie de 5.000 hundiría la escala y se leería
     como «el sitio se ha caído».
   - **Un corte donde cambió la versión del análisis** (72), en el punto
     posterior (`cambio_version`): el tramo sale discontinuo con una «v» que
     dice de qué versión a cuál, y la tendencia del panel no dice «sube» ni
     «baja» a través de él (`no_comparable`, como la 91). Desconocida cuenta
     como cambio. El cambio de versión del RASTREO se marca también (una línea
     tenue) pero no corta la tendencia, igual que en la 91: con un despliegue
     por semana cortaría todas.
   - **Una serie por perfil**; sin perfil, por programación, y si no, por host.
     Dos perfiles del mismo cliente (el sitio y el blog, o un competidor)
     mezclados en una línea serían censos de sitios distintos.
   - **Panel de cartera**: la portada pasa de tarjetas a una tabla con el
     último censo de cada cliente y la tendencia de errores de su serie con el
     censo comparable más reciente (línea pequeña de los últimos 8, partida
     donde cambió la versión). Una consulta para todos los clientes, y si la
     tabla no existe todavía el panel sale sin tendencia en vez de fallar.

   Relleno hacia atrás: `scripts/rellenar_evolucion.py`, de uno en uno, con
   `statement_timeout` por job y un SAVEPOINT cada uno; `--simular` lo hace
   todo en una transacción que se deshace (creando la tabla dentro). Medido
   sobre `crawler_db` local el 2026-10-18 con `--simular`: **46 jobs
   terminados en 4,6 s**, el más lento 1,32 s (el censo de Lopesan de julio,
   8.764 URLs y **841.901 incidencias**), Druni (60.399 URLs) 0,90 s, y 0
   fallos; tras el ROLLBACK, `profile_runs` no existe. La migración solo crea
   la tabla (`migrar_profile_runs`), sin ALTER sobre `jobs` ni `urls`; su FK a
   `jobs` pide un candado compatible con lecturas, y bajo el `lock_timeout` de
   15 s falla en vez de esperar a un análisis en marcha.

   Queda abierto: la evolución por segmento de negocio (125), que necesitaría
   el agregado por `urls.segmento`; reglas y umbrales sobre estas métricas
   (5.2); y el informe exportable (6.2), que leerá de aquí.

<a id="d131"></a>
131. **Comparar también el enlazado, el marcado y el hreflang; separar lo que
   se ha roto de lo que se ha publicado mal; y una migración se empareja con
   reglas, no se lee como «ha desaparecido el sitio»** (Ola 5.4 de #32). Tres
   piezas en `shared/comparacion.py`, las tres en `comparar_censos` /
   `comparar_incidencias` y por tanto en `jobs.comparacion` (worker) y en
   `GET /diff` (API):

   - **(a) Seis campos más por página**: páginas que la enlazan
     (`unique_inlinks_count`, no instancias: un menú que repite el enlace no es
     otra página), `click_depth`, `pagerank_score`, meta description, tipos de
     datos estructurados (sin los nodos sin tipo, 51) e idiomas del hreflang
     (sin los del `<body>`, 56, y sin caja). Van a la tabla de cambios y a seis
     reglas nuevas: `se_alejan_de_la_home` (+2 clics o sin camino),
     `pierden_enlaces_internos` (la mitad **y** 10 o más),
     `pierden_pagerank` (25 puntos, una década, decisión 34) —una sola familia:
     un menú que deja de enlazar una sección aleja, quita entrantes y quita
     PageRank a la vez, y es un hallazgo (36), el primero de los tres—,
     `pierden_datos_estructurados` y `pierden_hreflang` (sobre las que los
     tenían, no sobre todas las indexables: «30 de 1.000» es «30 de 30») y
     `cambia_la_description` (info: Google la reescribe a menudo). Todo
     warning o info: el webhook (77) solo lleva las críticas, y ninguna de
     estas saca una página del índice.
   - **El grafo solo se compara con los dos censos enteros y del mismo
     alcance.** Con uno truncado o con semillas que no se contienen (67, 70),
     las páginas que enlazaban pueden no haberse rastreado y «pierde enlaces»
     sería falso: la tabla dice `total: null` con el motivo, no «0». Igual con
     una medida que uno de los dos censos no tiene (anterior a la columna:
     NULL en todas es «no se sabe»), y solo entre páginas 200 en los dos: si
     pasa a 404 ya es `rotas`. Tampoco con una **lista** (`max_depth` 0,
     decisión 128) ni con dos profundidades máximas distintas —lo que uno no
     rastreó no enlaza, y frente al censo completo todas las URLs de la lista
     «perderían» sus entrantes—, ni con dos **versiones del analizador** o
     una desconocida: entrantes, profundidad y PageRank los calcula el
     análisis, no el rastreo, y un re-análisis los mueve sin que cambie el
     sitio (38, 57). Es la regla de la 91 llevada al grafo; lo que sale del
     rastreo (description, marcado, hreflang) sí se sigue comparando. Un
     JSON-LD con `json_invalido` (80) no cuenta como tipo: Google tira el
     bloque entero, así que perderlo así es perder el resultado enriquecido.
   - **(b) Las incidencias nuevas se separan** en `en_url_existente` (la URL
     estaba y ahora tiene el problema: una regresión) y `en_url_nueva` (se ha
     publicado así). Mezcladas, «300 nuevas» no dice si alguien estropeó lo que
     había. Con el anterior truncado la URL «nueva» puede ser una que no se
     alcanzó: `url_nueva_afirmable: false` y la interfaz lo dice así. Las
     páginas que antes fueron un muro (103) no cuentan como existentes.
   - **(c) Mapeo de rutas por regex** (`map_rutas`, lista JSON de
     `[patrón, reemplazo]`): reescribe la **ruta + query** de las URLs del
     censo **anterior** —el que diga la fecha, no el desplegable— antes de
     emparejar, también sus semillas (si no, el guardia 70 declara otro
     alcance) y su canonical para la tabla de cambios. Sobre la ruta y no la
     URL entera: el host es de `map_host`. Gana la primera regla que casa,
     como en un mapa de redirecciones. Una regex que no compila, o un
     reemplazo con un grupo que no existe (`\2` con un grupo; Python lo valida
     antes de buscar), es un **422** que dice cuál. La respuesta lleva
     `mapeo_rutas`: reescritas por regla, emparejadas y su %, y las
     **colisiones** (dos URLs viejas que caen en la misma nueva: solo una entra
     en la comparación, y se dice). Los guardias 67, 70 y 91 no cambian:
     `test_comparacion_ampliada.py` los ejecuta con mapeo.
   - **Al importar un backup se perdían `finish_reason`, las versiones y
     `health`**, así que un censo truncado importado se comparaba como entero
     (lo vi al medir: el censo de Lopesan del 27-jul parado en 8.765 URLs salía
     `concluyente` frente al completo del mismo día). Ahora se conservan.

   Medido re-analizando con el código de hoy censos importados de `crawler_db`
   local (solo lectura; en una BD propia). **Control de un día** —progym 6-oct
   contra 7-oct, 3.540 indexables comunes—: **0 alertas nuevas y 0 cambios** en
   los seis campos; el ruido real es ±1 clic en 6 páginas, PageRank dentro de
   ±9 puntos en todas, y 140 páginas que pierden 10 o más entrantes sin llegar
   a la mitad y 21 que pierden la mitad con menos de 10 —por eso las dos
   condiciones juntas—. Lopesan 27-jul contra 27-jul (el primero truncado): el
   grafo no se compara y lo demás da 0 alertas (2 descriptions cambiadas de
   7.082). **Fallo simulado** sobre el control —las fichas sin `Product`—:
   `pierden_datos_estructurados` 3.076 de 3.539 (86,9%). **Migración
   simulada** —progym con `/x.html` → `/producto/x/` y `/marca` → `/marca/x`,
   dos reglas—: sin mapeo empareja el 10,1% y dispara `desaparecen` (3.186);
   con mapeo **100%** (3.593/3.593, 0 colisiones, 0 alertas, 0 cambios de
   canonical); contra el censo de la víspera recupera el 100% de lo que
   emparejaba sin migración (3.592; el 74,9% del anterior, porque entre esos
   dos censos desaparecieron 1.205 URLs, decisión 89). La comparación con todo
   esto tarda 0,3-0,5 s y la lectura de cada censo 0,6-1 s.

   Queda abierto: el mapeo es del endpoint y de la pestaña, no de una
   programación (la comparación automática del worker no lo aplica), y los
   enlaces ganados y perdidos URL a URL (leerían `links` o el Parquet, 95) no
   están.

<a id="d133"></a>
133. **Una URL de la lista que redirige a una página sana es un aviso, no un
   fallo** (criterio del usuario, 2026-10-18). Hasta ahora cualquier URL de la
   lista que no respondiera 200 era `falla`, y una redirección también. Las dos
   lecturas tienen su razón: quien pone una URL en la lista espera esa
   dirección exacta, pero tras una migración las redirecciones son lo normal y
   marcarlas todas de rojo llena el veredicto de falsas alarmas. El término
   medio:
   - **Se sigue la cadena** dentro del mismo censo (el spider pide cada destino
     como su propia fila, decisión 27, también con `max_depth=0`), hasta
     `MAX_SALTOS` = 10.
   - **Acaba en una página 200, indexable y que no es un muro** → grupo
     `redirige` en `avisos`, con el destino final y los saltos si son más de
     uno. El veredicto es **`aviso`** si no hay nada más: ni pasa ni falla. Un
     CI que comprueba `== "pasa"` no despliega con un aviso, que es el lado
     seguro de equivocarse.
   - **Acaba en un error, en una página no indexable, o da la vuelta** →
     `no_responde_200`, como antes, con el destino y `estado_destino` (el
     código, o el estado de indexabilidad si respondía 200).
   - **El destino no se rastreó** (otro host, o el rastreo no llegó) → duda
     `destino_sin_rastrear`: no se sabe si está bien, así que no se afirma.
   Precedencia: `falla` > `no_concluyente` > `aviso` > `pasa`. La regla
   `redirigidas` de la comparación es `warning`, así que nunca fue motivo de
   `falla` en el veredicto y no cambia.

<a id="d135"></a>
135. **Las reglas de alerta son del cliente, con valores de serie que
   reproducen lo de antes, y una regla de cambio no se evalúa contra lo que no
   es comparable** (Ola 5.2; criterio del usuario: se editan en el panel del
   cliente y arrancan con algo por defecto).
   - **Dónde viven.** En `clients.reglas_alerta`, que es PARCIAL sobre
     `shared.reglas.de_serie()`. Un cliente sin nada guardado tiene las 15
     reglas de la comparación con sus umbrales de la decisión 68, y de ellas
     avisan solo las críticas (decisión 77). A eso se suman dos reglas propias
     de serie; la crítica, que caigan un 10 % las indexables, también avisa. Se guardan solo las
     diferencias, así que una regla de serie nueva llega también a quien ya
     tocó las suyas. Lo que no valida en la BD (editada a mano) se ignora sin
     tumbar las demás.
   - **Reglas de comparación.** Por regla se pueden cambiar `activa`, `avisar`
     y los tres umbrales: el % del sitio, el piso de páginas y el % de una
     plantilla o segmento. Se pasan a `comparar_censos(umbrales=...)`, así que
     los guardias de la comparación (truncado, alcance, muro, versión) siguen
     igual. Medido con el censo sintético de los tests: 25 rotas de 100 saltan
     con lo de serie, 10 no; con `min_paginas=5` saltan las 10.
   - **Reglas propias.** Llevan una medida, un segmento opcional, un modo
     (`valor`, `cambio`, `cambio_pct`), un operador y un umbral. Las medidas
     salen de `urls` e `issues` filtrando por `job_id` (decisión 37), sin
     `links`; las de incidencias cuentan PÁGINAS distintas. El criterio de
     hecho de 5.2, «4xx > 20 en fichas», dispara en el caso (25 fichas en
     404) y no en el control (3 en fichas y 30 en el blog).
   - **Lo que no se evalúa, con su motivo.** Un censo detrás de un muro (103),
     un perfil de la competencia (#36 F2) y, en las de cambio: sin referencia
     comparable, comparación no concluyente (truncado o alcance distinto,
     decisiones 67 y 70), o, si la medida depende del análisis, dos versiones
     de análisis distintas o sin sello (decisión 91). Los códigos de estado
     salen del rastreo y se comparan igual. De 0 a algo no es un porcentaje.
   - **Se evalúan aunque no haya censo anterior**: las de `valor` no lo
     necesitan. Antes el worker salía en cuanto no había referencia. El
     resultado va a `jobs.alertas_cliente`, con su propio `try`, y una regla
     rota no se lleva la comparación ya guardada.
   - **Avisar.** El webhook recibe lo que el cliente marque, salvo que esté
     silenciado hasta una fecha. La interfaz lo sigue mostrando: silenciar es
     no molestar, no dejar de mirar. Los perfiles de la competencia no avisan.
   - **Resumen semanal** (`shared/resumen_semanal.py`). Lo genera el worker en
     la misma pasada que el planificador, el día y la hora del cliente (de
     serie, el lunes a las 8:00 en su zona), con la gracia de 12 h de la
     decisión 75. Va a `resumenes_semanales`, con una restricción única por
     (cliente, semana) para que dos réplicas no manden dos. Contenido:
     - por perfil propio activo, el último rastreo de la semana con sus
       alertas, las incidencias nuevas y resueltas y cuatro cifras con su
       cambio (el de errores solo con la misma versión de análisis);
     - los perfiles que **no** se rastrearon;
     - las alertas de los rastreos sin perfil.
     Sale por el mismo webhook con `tipo: resumen_semanal`, para que n8n lo
     enrute aparte.
   - **Probar antes de fiarse.** `POST /api/clients/{id}/reglas/probar` evalúa
     lo que hay en pantalla contra el último rastreo completado, sin guardar.
   - **En una lista (decisión 128) mandan todas las reglas** con su umbral
     absoluto: apagar una regla ruidosa para los censos completos no puede
     cambiar el veredicto de un QA de despliegue.
   - **Unas reglas ilegibles no paran nada.** Lo guardado se revalida al
     leerlo y lo que no vale se queda con lo de serie. La comparación lleva
     además su propio `try` alrededor de las reglas.
   - **El resumen** solo se genera para clientes con alguna programación
     activa, vaya por un perfil o no. Una programación sin perfil cuenta
     como una serie más: el 18-oct-2026 ningún cliente de producción tenía
     perfiles, y los censos semanales iban por programación. Un perfil **sin
     programación** se lanza a mano —el perfil `lista` de Lopesan, el QA de
     cada despliegue—: sale en el resumen si se lanzó esa semana, y si no, no
     dice nada. Solo de lo programado es un hallazgo que no haya corrido; la
     primera versión ponía «lista: no se ha rastreado esta semana» cada lunes.
     Si el webhook falla, se reintenta mientras dura la gracia, y sus
     cifras no se comparan contra un censo truncado ni detrás de un muro.
   - La pestaña Comparar (`/diff`, bajo demanda) usa los umbrales de serie y
     la alerta guardada en el rastreo los del cliente, así que pueden no
     coincidir. Es a propósito: Comparar sirve para mirar dos censos
     cualesquiera, y la alerta es la del cliente.
   Queda de 5.1: incidentes con estado (abierto/visto/cerrado, «solo el job
   más reciente abre o cierra») y canales por cliente. Y una regla nueva solo
   vale para los rastreos que terminen después: no se re-evalúan los viejos.


<a id="d139"></a>
139. **Una alerta con estado: se avisa al abrirse, se cierra sola y solo la
   cierra quien puede afirmarlo** (Ola 5.1 de `docs/PLAN_MEJOR_CRAWLER.md`, sin
   los canales). Sin estado, el mismo fallo avisaba en cada rastreo mientras
   durase: el canonical de Lopesan a `*.lfr.cloud` habría avisado cada lunes
   durante meses, y un aviso que llega cada semana con lo mismo se deja de leer
   (decisión 77). Tabla `incidentes` y `shared/incidentes.py`:
   - **Un incidente es (serie, clave).** La serie es el perfil (o, si no hay,
     la programación) que repite el rastreo; la clave, la regla que saltó, y en
     las de un campo extraído también su nombre (`extraido_cambia:precio` y
     `extraido_cambia:sku` son dos fallos, decisión 127). Las reglas del cliente
     son `cliente:<id>` (decisión 135). Un solo incidente vivo por (serie,
     clave), con índice único parcial (`estado <> 'cerrado'`), igual en
     Postgres y en SQLite.
   - **Abre, sigue, cierra.** El primer rastreo que dispara la alerta lo abre y
     es el único que avisa; los siguientes suman `veces` y guardan el último
     detalle sin avisar; lo **cierra solo** el primer rastreo que, pudiendo
     afirmarlo, ya no la ve. Si vuelve, es otro incidente y se avisa otra vez.
   - **No ver una alerta no es verla resuelta** (decisiones 67 y 89). Una de la
     comparación solo se cierra si la de este rastreo es comparable y
     concluyente: sin censo anterior, con otro alcance, truncado o detrás de un
     muro, sigue abierta. Una regla del cliente, solo si esa regla se evaluó
     (no en `sin_evaluar` y sin `motivo` global).
   - **Solo el rastreo más reciente de la serie abre o cierra.** Uno que
     termina tarde, o que se re-analiza, con otro posterior ya creado, no toca
     nada ni avisa: si no, cerraría lo que sigue roto o reabriría lo cerrado.
   - **Un rastreo sin serie no abre incidentes** y avisa como siempre: uno
     lanzado a mano no tiene «la semana que viene» con la que cerrarse. Y si la
     actualización falla, el worker avisa como antes: repetir un aviso es mejor
     que callarse uno.
   - **Una persona marca «visto» o silencia; cerrar no.** `PATCH
     /api/clients/{id}/incidentes/{iid}` admite `estado` (abierto o visto) y
     `silenciado_hasta`; cerrar es un 422 y tocar uno cerrado un 409. El
     silencio es de (serie, clave) y no de la fila: un fallo silenciado hasta
     el 31 que se cierra el 10 y vuelve el 17 tampoco avisa.
   - Dónde se ve: la pestaña **Alertas** del cliente (abiertos y cerrados, con
     el rastreo que lo abrió, el último que lo vio y el que lo cerró), el panel
     de cartera («N incidentes abiertos», enlace a `#/clientes/<id>/alertas`) y
     el **resumen semanal**, que dice lo que sigue abierto y desde cuándo: es
     donde se ve que un fallo lleva tres semanas sin arreglarse, ya que el
     aviso solo salió la primera.
   La competencia no abre incidentes (sale antes, como en la 135). **Pendiente**
   de la 5.1: los canales por cliente (Apprise con el destino en el entorno,
   decisión 83), que esperan a que se decida a dónde van los avisos; y el
   criterio de #36, dos semanas de perfil semanal real sin alertas falsas, que
   empieza a contar con este despliegue.

<a id="d149"></a>
149. **Los avisos van a Slack por su API, un canal por cliente** (Ola 5.1 y
   6.4). Decidido el 19-oct-2026: alertas, resumen semanal e informes, a
   Slack. `shared/slack.py` publica con un **bot** (`chat.postMessage`) y no
   con *incoming webhooks*: un webhook de Slack queda atado a un canal al
   crearlo, así que serían N URLs secretas que alguien tiene que ir creando en
   la consola de Slack, una por cliente. Con el bot es **un token para toda la
   agencia** y el canal es un parámetro público de la integración `slack` del
   cliente (`canal`, `canal_nombre` y qué se manda: `alertas`, `resumen`,
   `informe`).
   - **El token es un secreto y vive donde la llave de Search Console**
     (decisión 141): en el entorno o subido a `SECRETOS_DIR`, con prefijo
     `SLACK_`. Al subirlo se comprueba con `auth.test`: uno pegado mal
     fallaría el lunes con el primer resumen, no ahora que hay alguien
     mirando. Del token solo se enseña el espacio de trabajo y el nombre del
     bot, que es lo que hace falta para invitarlo a un canal.
   - **Qué sale y cuándo**: las alertas con el mismo criterio que el webhook
     (críticas o las reglas del cliente marcadas para avisar, y en una serie
     solo lo que abre incidente, decisiones 77, 135 y 139); el resumen
     semanal, con su silencio; y el **informe de cada rastreo programado** al
     terminar, con las cifras y su cambio, las incidencias nuevas y resueltas
     y el enlace al informe — después de las alertas, para que el canal lea
     primero lo urgente. Los rastreos lanzados a mano no mandan informe: un
     canario o una prueba no son algo que el cliente espere leer. El texto del
     informe sale de `_resumen_de_rastreo`, lo mismo que ese rastreo aporta al
     resumen semanal, para no tener dos cifras de lo mismo.
   - **Un fallo de Slack no se pierde en el log.** Los errores se traducen a
     algo que se pueda hacer («El bot no está en el canal: escribe /invite
     @crawler en el canal»), el último envío de cada cliente queda en Redis y
     se ve en la ficha, y «Mandar una prueba» lo dice canal por canal. Es como
     se descubre lo más común: que nadie invitó al bot.
   - Best-effort como el webhook: nunca lanza, y un 429 se reintenta una sola
     vez si Slack pide esperar 5 s o menos, para no retrasar el cierre de un
     rastreo. El webhook genérico sigue: quien enrute por n8n no pierde nada.
   Permisos del bot: `chat:write`, y `channels:read` (y `groups:read` para los
   privados) para elegir el canal de una lista; sin ellos el canal se escribe
   a mano.


<a id="d158"></a>
158. **El tráfico de Google entre dos censos, agrupado por lo que cambió en esas
   páginas, y nunca «por»** (#167; combinación 10 de
   `docs/INVESTIGACION_SEARCH_CONSOLE.md`). La comparación ya decía qué cambió en
   cada página; con Search Console (141) dice además qué pasó con su tráfico
   (`shared/trafico_entre_censos.py`, en `comparacion.trafico`).
   - **Dos ventanas iguales** que acaban en la fecha de cada censo menos 2 días,
     tan largas como los días entre los dos censos en semanas enteras, **de 7 a
     28**. Con 28 fijos, como pedía la issue, dos censos semanales compartían 21
     días de ventana y la caída se diluía. Con menos de 7 días entre medias no
     se compara. Si los datos no llegan al final de la ventana del nuevo, queda
     `pendiente` y el worker la completa tras la siguiente descarga
     (`completar_pendientes`); con un día recortado en el tope de filas, no se
     compara.
   - **Caída o subida** de una página: el 50 % y 20 clics o más. Se agrupan por
     el cambio entre censos (status, redirección o error nuevos, indexabilidad,
     canonical a otra URL, title, H1, contenido, sale del sitemap, pierde
     enlaces o PageRank, desaparece); una página con dos cambios está en los dos
     grupos, y sin ninguno va a «sin cambio técnico en el rastreo: puede ser
     demanda, competencia o la página de resultados».
   - **El tráfico que se mueve no se pierde** (trasvase): si la que cae está
     canonicalizada a otra URL, o es la misma con o sin barra final, y esa otra
     sube. Medido en Lopesan entre el 14 y el 21-oct: las fichas con barra
     final, canonicalizadas a la de sin barra desde antes, bajaron (92 a 4
     clics) mientras subía la buena (38 a 121); sin esto, «8 páginas perdieron
     290 clics sin cambio técnico», cuando era Google haciendo caso al
     canonical. Con ello: 6 páginas que trasvasan 287 clics y 4 que pierden 88
     sin cambio técnico, y la home `/es` sube 109 a la vez que cambia su
     indexabilidad y su canonical. En el sitio, −2 % (42.443 a 41.481 clics en
     semanas que no se pisan).
   - **General y estacional**: si el sitio entero cae un 30 % o más, se dice que
     es general; con datos de hace un año, si el mismo periodo varió lo mismo
     (±15 puntos), que parece estacional. **Rupturas de serie** conocidas
     (num=100 en septiembre de 2025, la corrección de impresiones del
     28-04-2026): si las ventanas las cruzan, se avisa; afectan a impresiones y
     lo que se compara son clics.
   - **Lo que no se puede afirmar** (revisión independiente, 13 hallazgos
     arreglados): una ventana se da por cubierta por **días con filas** de cada
     propiedad activa, no por `gsc_estado.ultimo_dia`, que avanza aunque Google
     devuelva el día vacío (hoy menos 2 casi nunca está cerrado a las 4 UTC: un
     día a 0 era un −14 % falso en 7 días); con una comparación de páginas no
     concluyente (truncado, muro, alcance) no se dice nada del tráfico; una
     página que no estaba en el censo anterior va a «no hay con qué comparar»,
     no a «sin cambio técnico»; un trasvase exige que el destino gane al menos
     la mitad de lo que pierde la otra, y lo que la saca del índice se sigue
     diciendo; el total y «general» cuentan solo los hosts rastreados y las
     propiedades activas; la lista (128) se acota igual al completar un
     pendiente; el cálculo va en un savepoint para que un error SQL no se lleve
     las alertas críticas.
   - **Un solo redactor**: `textos()` escribe las frases («coincide con», nunca
     «por») y las usan la tarjeta de la ficha del rastreo y el aviso de Slack
     del rastreo programado (`resumen_semanal` → `avisos.texto_de_informe`).

<a id="d165"></a>
165. **El tráfico que esperaba a los datos se avisa al completarse, una vez; una
   caída general se dice antes que los grupos; y el contenido se compara por su
   hash** (#167, restos de la 158).
   - **Aviso al completarse.** El informe de Slack del rastreo programado sale
     al acabar el rastreo, cuando los datos de Search Console casi nunca llegan
     aún al final de la ventana: decía «aún no tiene datos…» y
     `completar_pendientes` rellenaba después `jobs.comparacion` sin avisar, así
     que la caída de verdad no llegaba a nadie. Ahora, al completarse, se manda
     por el **mismo camino que ese informe** (`avisos.avisar_de_trafico`:
     rastreos programados, canal del cliente con «informe» activo) y con las
     mismas frases de `textos()`. **Una sola vez**: antes de escribir se relee
     la fila con `FOR UPDATE` y solo sigue si sigue `pendiente` (dos propiedades
     del mismo cliente sincronizando a la vez llegaban las dos), y queda
     `trafico.aviso` = `{estado: por_enviar | enviado | no_enviado, en}`. Sin
     Slack configurado, o en un rastreo lanzado a mano, no sale nada
     (`no_enviado`). **La competencia, nunca**: el worker corta su informe y
     sus alertas (`rol == "competidor"`), y su tráfico no está en el Search
     Console del cliente (con sus hosts, el total da 0: «Clics de Google: 0 y
     0»). `comparar_trafico` devuelve None para un perfil competidor,
     `completar_pendientes` salta los que quedaran pendientes de antes,
     `avisar_de_trafico` recibe el perfil y no avisa, y la ficha del cliente no
     los lista (revisión del PR #230). Un fallo al avisar no deshace la comparación (best-effort
     con el `try` sobre la función entera, 74). No va por `ALERTA_WEBHOOK_URL`:
     el informe tampoco va, y el aviso tiene que llegar a quien leyó el «aún no».
   - **En la ficha del cliente.** `GET /api/clients/{id}/trafico` da el último
     tráfico entre censos de cada perfil (de los 30 rastreos más recientes con
     comparación), con las frases de `textos()` calculadas en el servidor: la
     tarjeta de la ficha no redacta nada. Un pendiente sale con su motivo y
     «esperando datos».
   - **Caída general: primero general, los grupos como detalle.** Con
     `general` (el sitio cae un 30 % o más), la segunda frase ya decía que es
     general, pero después venían las líneas por grupo («40 páginas perdieron
     300 clics; coincide con que cambia el title») como si explicaran la caída,
     que también tienen las páginas que no cambiaron. Se eligió **conservar los
     grupos** detrás de una frase que los presenta como «detalle y no como
     causa», en vez de quitarlos: si además de la caída general un grupo cae
     mucho más (un 404 nuevo), sigue siendo un dato útil, y quitarlo esconde
     justo lo que hay que arreglar. La frase solo sale si detrás va al menos
     una línea de grupo (las subidas sin cambio, de trasvase o sin dato
     anterior no se escriben). Sin `general` no cambia nada.
   - **Contenido por `content_hash`.** Por palabras (30 % y 50 palabras) no se
     veía un texto reescrito con el mismo largo. El hash solo cuenta si los dos
     censos tienen la **misma `crawler_version`, conocida en los dos** (66), y
     la misma config de extracción (`render_js`, `extract_page_content`,
     `strip_promo_blocks`, `custom_boilerplate_selectors`):
     `hash_comparable()`, guardado como `trafico.contenido_por_hash`. El hash
     cambia entero con un carácter, y con otra versión de
     `extract_main_content` (seis cambios entre agosto y octubre) cambian
     TODOS: cada página que cae saldría con «cambia el texto» en vez de «sin
     cambio técnico», atribuyendo al sitio una diferencia nuestra (66, 91).
     Con eso, y si las DOS páginas tienen
     `urls.content_hash` (SHA-256 del contenido principal normalizado, 47):
     hashes iguales = mismo contenido, aunque varíe el recuento; distintos con
     el cambio de palabras de siempre = «cambia mucho el contenido»
     (`contenido`); distintos sin él = «cambia el texto del contenido
     principal» (`texto_contenido`, un grupo aparte para no mezclar una fecha
     cambiada con una página vaciada). Si falta en cualquiera de las dos (censo
     anterior a la columna, página sin contenido principal) o los censos no son
     comparables por hash, la regla por palabras de la 158. Queda: en un sitio
     con fechas relativas, contadores o precios en el contenido principal, dos
     censos con el mismo código también tienen hashes distintos; por eso es un
     grupo aparte («cambia el texto», no «cambia mucho») y nunca dice «por».
   - **Fuera** (pendiente de decisión del usuario): el cambio de plantilla entre
     censos como grupo propio; hoy cada grupo solo lista sus plantillas.
