# Decisiones: herramientas de control, tests y versionado de censos

> Movidas tal cual desde `CLAUDE.md` (2026-10-14). Indice de todas las
> decisiones en [`README.md`](README.md). La numeracion es historica y se
> conserva: los commits, issues y el resto de decisiones citan por numero.

<a id="d55"></a>
55. **Los scripts de control mentían, y uno de ellos escribe en la base de
   datos** — `fix_h1_en_contenido.py` es el único script que **modifica**
   `page_content`, así que un fallo suyo no da un dato raro: corrompe el
   contenido guardado. Tenía tres: devolvía `0` donde el llamador hace `len(...)`,
   de modo que con `--todos` **un solo job sin h1 abortaba la pasada entera**;
   `_falta_titular` exigía una línea idéntica, así que «Hola mundo.» frente al h1
   «Hola mundo» —o el titular partido por un `<br>`— se daba por ausente y se
   anteponía **duplicado**; y al revertir se quitaba la primera línea de
   `content_text_original` aunque no se hubiera parcheado, **borrando un titular
   legítimo** (ahora el diario anota qué columnas se tocaron). 6 de los 8 tests
   nuevos fallan contra el código anterior.
   `check_js_templates.py`, que es quien decide `grafo_fiable` y por tanto si el
   PageRank es de fiar: los hosts no incluían la variante sin `www`, así que en
   un rastreo de `www.x.com` un enlace a `x.com` salía **externo** y no contaba;
   no se limpiaban los banners antes de comparar, así que el enlace que inyecta
   OneTrust o Cookiebot contaba como enlace escondido tras JavaScript y
   **cualquier sitio con gestor de consentimiento salía con el grafo no fiable**;
   el umbral era «mayor que cero», ahora pide el 10% del grafo de la plantilla o
   el caso que importa (cero enlaces en crudo y alguno al renderizar); `/tv/`
   pasaba por home y `/es/productos/galletas-cookies/` por página legal; y las
   reglas de producto estaban escritas para un cliente de hoteles, así que en
   cualquier otro sitio todo caía en «otras · N niveles». El repuesto ahora es la
   forma de la ruta (`shared/plantillas.py`, compartida con
   `check_content_quality.py`).
   `check_content_quality.py` comparaba **cifras incomparables**: filas de
   `links` con repeticiones contra un conjunto de URLs sin repetir, de modo que
   lo guardado salía siempre mayor y la alarma «se han perdido enlaces» no
   saltaba nunca — que es justo para lo que existe el script.
   `export_markdown.py` comparaba cada ruta con todas las demás (O(n²): 2.500
   millones de comparaciones con 50.000 URLs, 0,45 s medidos ya con 2.789) y
   exportaba 404 y noindex como si fueran contenido publicado.

<a id="d62"></a>
62. **El comprobador de contenido no puede dar la alarma cuando el bloqueado
   es él** (#30) — `check_content_quality.py` es la herramienta con la que se
   caza la pérdida de contenido después de un rastreo, y descargaba la página
   con `r.text` **sin mirar el código**. Un 403, o un muro de WAF que responde
   **200** con 5-6 kB de HTML y cero palabras de la página, se comparaba como si
   fuera la página: el informe decía «aquí se ha perdido todo el contenido»
   sobre un extractor que funciona. Lo tengo reproducido con progym, que desde
   mi IP contesta `Just a moment...` al mismo camino (curl_cffi + chrome124) que
   usa el script. Ahora un código distinto de 200, o un `<title>` de los nueve
   muros conocidos, es `NoEsLaPagina` y la muestra se descarta.
   Y lo que se descarta **se cuenta y se imprime**: antes, una plantilla cuyas
   muestras fallaban todas desaparecía de la tabla, y una tabla corta se lee
   como «aquí no hay problemas». Si no se pudo comprobar ni una, lo dice con
   todas las letras en vez de imprimir una tabla vacía. El filtro casa el
   título por subcadena pero solo contra muros reales: «Checklist de seguridad
   web» es un artículo y pasa, y una página sin `<title>` no se tira por eso.

<a id="d66"></a>
66. **Cada rastreo queda sellado con la versión que lo hizo** — comparar dos
   censos del mismo sitio solo significa algo si se sabe si entre medias cambió
   **el sitio** o cambiamos **nosotros**, y con lo que se guardaba no había
   forma de contestarlo. Me pasó dos veces el mismo día:
   - el grafo de Druni bajó de 37,5 a 32,1 millones de aristas entre dos
     medidas, y parecía efecto de una optimización mía; era que
     `analyze_indexability` había materializado **9.771 páginas noindex** que
     antes repartían autoridad. Costó repetir una medición de 30 minutos
     descartarlo;
   - el censo de CST dio **88.838 «errores» de datos estructurados** que eran
     basura guardada por el extractor de junio, no un sitio roto.
   Ahora `jobs.crawler_version` guarda el SHA del commit: el `ARG` del
   Dockerfile lo recibe al construir (`--build-arg`, que el despliegue rellena
   con `git rev-parse --short HEAD`), queda en el entorno de la imagen y el
   worker lo escribe al marcar el job como `running`. En local, sin argumento,
   queda **`dev`** y no una cadena vacía que parezca una versión. Comprobado de
   extremo a extremo: imagen construida con un valor de prueba → el código lo
   reporta.
   Es la decisión 13 llevada a su conclusión («antes de atribuir una diferencia
   a un cambio del crawler, repetirla con la configuración vieja»): para eso
   hay que saber cuál era la configuración vieja **y cuál era el código viejo**.
   Y hay un caso que el sello no cubre y conviene recordar: **un re-análisis
   arregla lo que decide el análisis, no lo que el extractor guardó mal**, así
   que una comparación debe distinguir «re-analizado» de «re-rastreado».

<a id="d69"></a>
69. **Una dependencia que falta no puede reaparecer como otro criterio de SEO**
   — `_norm` tenía un `except` que caía a `url.strip().rstrip("/")` y la imagen
   de la API **no llevaba `w3lib`**: el endpoint de comparación emparejaba las
   URLs con el criterio CONTRARIO al documentado en los dos casos que importan
   —`?a=1&b=2` dejaba de casar con `?b=2&a=1`, y `/a/` sí casaba con `/a`— sin
   que nada lo dijera. Lo mismo en `dominio_registrable`, cuyo `except` volvía
   a las dos últimas etiquetas, que es exactamente la regla que la decisión 49
   descartó; y **ninguna de las tres imágenes declaraba `tldextract`**: el
   crawler lo heredaba de Scrapy, `analysis/` no lo tenía y la API tampoco. Los
   dos respaldos fuera, las dos dependencias declaradas donde se usan. CI
   tampoco lo habría cazado, porque `test_comparacion.py` solo corría en el
   trabajo del crawler, que sí lleva w3lib: ahora corre también en el de la
   API, que es donde vive el endpoint.

<a id="d72"></a>
72. **Con qué se rastreó y con qué se ANALIZÓ son dos preguntas** — la
   decisión 66 selló el rastreo y dejó abierto el otro lado, que es el que más
   muerde: **un re-análisis cambia las cifras de un censo sin que cambie el
   sitio ni el rastreo**. Medido en penguin, un censo de julio con **959.633
   incidencias** entregadas, re-analizado con el código de octubre. Comparar
   dos censos analizados con código distinto sin decirlo atribuye al cliente un
   cambio que es nuestro. `jobs.analisis_version` lo sella al terminar
   `run_all`, y la comparación avisa cuando difieren.
   La constante se muda a `shared/version.py` porque ahora la escriben los dos
   —el worker al marcar `running`, el analizador al cerrar su pasada— y dos
   copias de «qué versión soy» pueden decir cosas distintas, que es justo lo
   que estas columnas existen para evitar. Solo se avisa de la diferencia
   cuando se CONOCEN las dos: el desconocido ya lo cubre el aviso del rastreo.

<a id="d74"></a>
74. **Un test que lee el fuente comprueba que escribiste algo, no que
   funcione** — me pasó DOS VECES la misma tarde, con el mismo final: el
   analizador escribía `analisis_version` con `Job` sin importar
   (`NameError`), y el worker montaba su sesión con `SessionLocal` sin
   importar (`NameError`). Los dos métodos importan sus dependencias **dentro**
   de la función, como el resto de esos ficheros, y a los dos se me olvidó. Los
   dos tenían test. Los dos pasaban: uno comprobaba que la cadena
   `analisis_version` aparecía en el fuente del método, el otro el ORDEN de las
   llamadas con `inspect`. El segundo llegó a producción y lo cazó el primer
   rastreo que lo ejecutó.
   La regla no es «nunca mires el fuente»: los tres `inspect.getsource` que
   quedan comprueban **forma** que no se puede observar ejecutando —que el SQL
   de `analyze_links` no una con `urls` (decisión 37, y en SQLite no hay plan
   que medir), que no haya vuelto el respaldo silencioso de
   `dominio_registrable` (69), que la comparación vaya después del estado
   final (71)—. La regla es que **mirar el fuente no puede sustituir a
   ejecutar**: esas tres van ahora acompañadas de un test que llama a la
   función de verdad contra SQLite y lee lo que quedó escrito.
   Y el `try` tiene que cubrir la función ENTERA. El fallo de producción
   ocurrió en la línea que montaba la sesión, que estaba fuera, así que el
   «best-effort» no cubría precisamente la línea que falló.

<a id="d82"></a>
82. **Las herramientas de control descargan como el rastreo** (#30) —
   `check_js_templates.py` bajaba el crudo con `urllib`, sin huella TLS: un WAF
   que deja pasar al rastreo (curl_cffi, decisión 11) le servía un desafío, el
   render sí llegaba a la página y TODOS sus enlaces salían «solo con
   JavaScript» — `grafo_fiable=false` por un bloqueo, no por el sitio. La
   descarga y el criterio de la decisión 62 (código distinto de 200 o título de
   muro → `NoEsLaPagina`) se mudan a `shared/descarga_cruda.py`, que usan los
   dos scripts. Lo descartado se cuenta (`sin_comprobar`, `motivos`) y una
   plantilla sin ninguna muestra válida sale con `muestras: 0` en vez de
   desaparecer; el worker (`_resumen_js_check`) no la cuenta como concluyente,
   así que si el comprobador se come el muro en todas, el veredicto es `None`
   («no se pudo comprobar»), nunca «fiable».
