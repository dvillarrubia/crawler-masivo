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
     nunca desde aquí.** La referencia es por definición un censo superado,
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
     El segmento es el del censo nuevo (las reglas de hoy) y, si falta, el del
     anterior. El caso que lo justifica, en test: 40 fichas repartidas en 40
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
