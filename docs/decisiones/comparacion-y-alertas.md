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
