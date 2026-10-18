/* ===== Catálogo de incidencias (UX-04, docs/AUDITORIA_UX.md) =====
 *
 * Qué es cada tipo, qué decide Google con él y cómo se arregla. Es lo que
 * enseña la vista agrupada de Problemas: «Canonical externo · 2.367» no le
 * dice nada a quien no escribió el analizador, y es justo lo que se enseña en
 * una reunión.
 *
 * Regla 0 de docs/PRIORIDADES.md: cada entrada contesta qué decide Google.
 * Si un tipo nuevo no tiene entrada, la vista lo pinta igual con su nombre;
 * añadirla aquí es parte de crear el tipo.
 */

const ISSUE_INFO = {
  // ----- Respuesta del servidor -----
  '4xx_error': {
    que: 'La URL responde con un error de cliente (404, 410…).',
    google: 'Sale del índice y los enlaces que apuntan a ella se pierden.',
    arreglo: 'Si el contenido existe en otra URL, redirigir con 301; si no, quitar los enlaces internos que la enlazan.',
  },
  '5xx_error': {
    que: 'La URL responde con un error del servidor (500, 503…).',
    google: 'Si se repite, Google rastrea menos el sitio y acaba sacando la página del índice.',
    arreglo: 'Revisar con el equipo técnico: logs del servidor en las URLs afectadas.',
  },
  'connection_error': {
    que: 'No se pudo conectar: timeout, DNS o conexión rechazada.',
    google: 'Para Google es como un 5xx: menos rastreo y, si dura, fuera del índice.',
    arreglo: 'Comprobar si es un bloqueo al rastreador (WAF) o una caída real del servidor.',
  },
  'respuesta_no_valida': {
    que: 'Lo que respondió el servidor no era la página: un desafío de un WAF (Cloudflare, Akamai, Imperva, F5, AWS), un captcha, un límite de peticiones (429) o un cuerpo que no se puede leer. Va una fila por host y tipo, con cuántas páginas.',
    google: 'No se sabe: no hemos visto la página. Si el muro también se le pone a Googlebot, Google no puede rastrearla y acaba sacándola del índice; si solo nos lo pone a nosotros, esas páginas se quedan sin auditar.',
    arreglo: 'Comprobar con Search Console (inspección de URL) que Google sí ve la página. Para auditarla: lista blanca de la IP del rastreador en el WAF, menos concurrencia o render de JavaScript, y reanudar el rastreo (las páginas de muro se repiten al reanudar).',
  },
  'redirect_chain': {
    que: 'Para llegar a la URL final hay dos saltos o más (A → B → C). Con más de 10 es un error: Google deja de seguirla.',
    google: 'Sigue hasta 10 saltos, y cada uno gasta rastreo; pasados 10 no llega al destino y Search Console da un error de redirección.',
    arreglo: 'Que la primera URL redirija directamente a la final, y actualizar los enlaces internos.',
  },
  'redireccion_a_error': {
    que: 'La redirección, o la cadena de redirecciones, lleva a otra ruta que da un error (4xx o 5xx). Las que solo cambian http/https, www o la barra final no salen aquí: ahí el error es la página, que ya avisa en su URL.',
    google: 'Llega a un error: la URL vieja pierde lo que tenía y la nueva no existe.',
    arreglo: 'Redirigir directamente a una página que responda 200, o quitar los enlaces que llevan a la URL vieja.',
  },
  'redireccion_a_noindex': {
    que: 'La redirección, o la cadena, acaba en una página que pide no ser indexada (noindex). Si diez o más redirecciones llegan a la misma, sale una sola vez, en ella: suele ser una regla deliberada (todo /mi-cuenta/ a /login). Las que solo cambian http/https, www o la barra final no salen.',
    google: 'Sigue la redirección y encuentra un noindex: lo que tenía la URL vieja no pasa a ninguna página del índice.',
    arreglo: 'Redirigir a la página indexable que sustituye a la vieja; si el destino es correcto, decidir si debe seguir en noindex.',
  },
  'meta_refresh_redireccion': {
    que: 'La página redirige con <meta http-equiv="refresh"> en vez de con una redirección del servidor. Con retardo, el usuario la ve unos segundos antes de saltar. Una refresh sin URL (recargar) o dentro de <noscript> no cuenta. Con render JS, una refresh inmediata puede aparecer como redirección por JavaScript, y el retardo de un salto visto por el navegador no se conoce.',
    google: 'La inmediata la trata como permanente; la de varios segundos, como temporal, y puede quedarse con la URL de origen. Recomienda la redirección del servidor.',
    arreglo: 'Sustituirla por una redirección 301 en el servidor.',
  },
  'redirect_loop': {
    que: 'Las redirecciones vuelven a una URL ya visitada y no terminan nunca.',
    google: 'La página no se puede indexar.',
    arreglo: 'Corregir la regla de redirección que crea el bucle.',
  },

  // ----- Indexabilidad y canonical -----
  'noindex_page': {
    que: 'La página pide no ser indexada (meta robots o cabecera X-Robots-Tag).',
    google: 'No la muestra en resultados y, con el tiempo, deja de seguir sus enlaces.',
    arreglo: 'Nada si es intencionado (búsquedas, filtros, privadas). Si es una página que debe posicionar, quitar el noindex.',
  },
  'noindex_y_canonical_a_otra': {
    que: 'La página está en noindex y a la vez declara un canonical a otra URL: dos señales que piden cosas distintas. Va agrupado por plantilla. Si el destino también es noindex no sale (no hay contradicción útil).',
    google: 'Suele obedecer el noindex, y la consolidación en la canónica no se produce. Es habitual y casi siempre deliberado en facetas y filtros.',
    arreglo: 'Quedarse con una de las dos: noindex si la página no debe estar, o canonical (sin noindex) si es una copia de la canónica. Gana claridad, no índice.',
  },
  'canonical_html_y_cabecera_distintos': {
    que: 'El canonical del HTML y el de la cabecera HTTP Link apuntan a URLs distintas (la barra final cuenta). Si pasa en diez páginas o más de un host, sale una vez por host.',
    google: 'Recibe dos canonicals que se contradicen y puede ignorar los dos y elegir por su cuenta.',
    arreglo: 'Que los dos digan lo mismo, o quitar uno (suele ser el CDN o un plugin el que añade la cabecera).',
  },
  'robots_invalid_syntax': {
    que: 'La meta robots está mal escrita (directivas separadas por «/», «|» o «;»).',
    google: 'Ignora lo que no entiende: un «noindex/nofollow» no se aplica.',
    arreglo: 'Separar las directivas por comas: «noindex, nofollow».',
  },
  'robots_txt_inaccesible': {
    que: 'El robots.txt del host respondió con un error del servidor (5xx), con un 429 o no respondió (timeout, DNS, conexión). Va una fila por host, con el código, los intentos y si al acabar el rastreo se volvió a pedir y respondió (intermitente). Un 404 no sale aquí: es «sin robots.txt».',
    google: 'Mientras dure, Google da el sitio entero por bloqueado y deja de rastrearlo; los errores de red cuentan igual que un 5xx. Si sigue así más de 30 días usa la última copia que guardó, y sin copia rastrea sin restricciones. Lo que ya está indexado no sale por esto, pero no se rastrea nada nuevo. Un 429 a nuestra IP puede ser solo un límite para el rastreador: Googlebot puede no recibirlo.',
    arreglo: 'Que /robots.txt responda siempre 200 (o 404 si no hay reglas), también con el servidor cargado y desde las IPs de Google: revisar el WAF, la caché y el origen. Comprobarlo en el informe de robots.txt de Search Console. El rastreo no se para por esto: las páginas del host se auditan igual.',
  },
  'robots_difiere_para_googlebot': {
    que: 'El robots.txt de este host no dice lo mismo para Googlebot que para nuestro rastreador: tiene un grupo propio para Googlebot, o uno (como «User-agent: Mozilla») que casa con nuestro user-agent. «Solo Googlebot» son URLs que rastreamos y Google no puede pedir; «solo el rastreador», URLs que no pedimos y Google sí puede.',
    google: 'Aplica el grupo que nombra a Googlebot y, si no hay, el de «*». Una página que Googlebot no puede pedir no se rastrea (puede indexarse sin contenido si tiene enlaces); por eso sale «Blocked by robots.txt» y no indexable aunque nosotros sí la viéramos. El robots.txt evaluado es el que nos sirvió a nosotros: si el sitio sirve otro a Googlebot, no se puede ver desde aquí.',
    arreglo: '«Solo Googlebot»: si esas páginas deben posicionar, quitar la regla del grupo de Googlebot. «Solo el rastreador»: no se auditaron; relanzar con el preset de user-agent de Googlebot para verlas como las ve Google.',
  },
  'bot_busqueda_ia_bloqueado': {
    que: 'El robots.txt de este host cierra el sitio entero (la home y todas las URLs de la muestra por plantilla) a un bot de BÚSQUEDA de IA: OAI-SearchBot (ChatGPT search), Claude-SearchBot o PerplexityBot. Va una fila por host con los bots y la línea que los bloquea. Los bots de entrenamiento (GPTBot, ClaudeBot, Google-Extended, Applebot-Extended, CCBot) no avisan: bloquearlos es una decisión legítima y no saca de ninguna búsqueda. La tabla completa por bot está en la ficha del rastreo.',
    google: 'A Google no le afecta: Googlebot tiene su propio grupo y Google-Extended no cambia nada en la Búsqueda. Lo que pierde el sitio es la otra búsqueda: según OpenAI, un sitio que bloquea OAI-SearchBot no sale en las respuestas de ChatGPT search (solo como enlace de navegación), y bloquear GPTBot NO saca de ahí; lo mismo dicen Anthropic de Claude-SearchBot y Perplexity de PerplexityBot. ChatGPT-User y Claude-User son descargas que pide una persona y no deciden si el sitio aparece.',
    arreglo: 'Si el cliente quiere aparecer en esas búsquedas, abrir el grupo de ese bot (o quitarlo de una lista «User-agent» compartida con los de entrenamiento: es el error típico, bloquear GPTBot y OAI-SearchBot en el mismo bloque). Si quiere no entrenar modelos, basta con bloquear los de entrenamiento. Es informativo: puede ser deliberado.',
  },
  'canonical_missing': {
    que: 'La página no declara canonical.',
    google: 'Elige ella misma la URL canónica entre las variantes que encuentre.',
    arreglo: 'Añadir un canonical a sí misma en las plantillas indexables.',
  },
  'canonical_cross_domain': {
    que: 'El canonical apunta a otro dominio (o a otro subdominio, si es «info»).',
    google: 'Tiende a indexar la URL del otro dominio en lugar de esta. Si el otro dominio no es el tuyo, la página desaparece de tus resultados.',
    arreglo: 'Revisar la plantilla que genera el canonical: casi siempre es un dominio de pruebas o de origen del CMS que se ha colado.',
  },
  'canonical_a_error': {
    que: 'El canonical apunta a una URL que da error (4xx o 5xx), directamente o al final de sus redirecciones.',
    google: 'Descarta el canonical. Si la página es una variante, el contenido se queda sin ninguna URL que indexar.',
    arreglo: 'Apuntar el canonical a la URL buena, que responda 200; si la buena es la propia página, que sea autocanónica.',
  },
  'canonical_a_redireccion': {
    que: 'El canonical apunta a una URL que redirige.',
    google: 'Suele quedarse con la URL final, pero recibe dos señales que no coinciden y puede elegir otra.',
    arreglo: 'Poner en el canonical la URL final de la redirección.',
  },
  'canonical_a_no_indexable': {
    que: 'Una página indexable declara canonical a una página que está en noindex.',
    google: 'Recibe dos señales que se contradicen: «la buena es aquella» y aquella pide no estar en el índice. Ninguna de las dos URLs queda indexada.',
    arreglo: 'Si la canónica debe posicionar, quitarle el noindex; si el noindex es deliberado, nada urgente, pero mejor que la variante apunte a una página indexable o sea autocanónica.',
  },
  'canonical_encadenado': {
    que: 'El canonical apunta a una página que a su vez declara otro canonical (A → B → C).',
    google: 'Puede seguir la cadena o ignorarla y elegir por su cuenta. Si es «error», la cadena acaba en una URL que da error.',
    arreglo: 'Que todas las variantes apunten directamente a la canónica final.',
  },
  'canonical_sin_enlazar': {
    que: 'Una página canónica a la que no llega ningún enlace interno: los enlaces van a sus variantes.',
    google: 'Usa los enlaces internos como señal de cuál es la canónica: con todos apuntando a la variante, puede quedarse con ella.',
    arreglo: 'Cambiar los enlaces internos para que apunten a la canónica. Solo cuentan los enlaces de fuera del grupo: los de la canónica a sus variantes (el selector de color o talla) no.',
  },
  'canonical_broken': {
    que: 'El canonical apunta a una URL que no responde 200.',
    google: 'Descarta el canonical y elige por su cuenta.',
    arreglo: 'Ya no se emite: desde la decisión 101 es canonical_a_error o canonical_a_redireccion. Sale solo en censos analizados antes.',
  },
  'canonical_multiple': {
    que: 'La página declara en el <head> varios canonicals con destinos distintos. El mismo destino escrito dos veces no sale aquí.',
    google: 'Los ignora todos, también el de la cabecera Link, y elige por su cuenta: la página cuenta como indexable, no como canonicalizada.',
    arreglo: 'Dejar uno solo; suele ser un plugin y la plantilla escribiendo los dos.',
  },
  'canonical_in_body': {
    que: 'El canonical está en el <body>, no en el <head>.',
    google: 'Solo lo lee en el <head>: este no cuenta.',
    arreglo: 'Mover la etiqueta al <head> de la plantilla.',
  },
  'canonical_a_http': {
    que: 'Una página https declara un canonical en http.',
    google: 'Puede preferir la versión http, que normalmente redirige: una señal contradictoria.',
    arreglo: 'Cambiar el canonical a https.',
  },

  // ----- HTML que Google trunca o ignora (decisión 102) -----
  'html_supera_2mb': {
    que: 'El HTML de la página pasa de 2 MB descomprimido. Solo se cuenta en páginas indexables.',
    google: 'Googlebot deja de descargar a los 2 MB e indexa lo que lleve: el contenido y los enlaces de detrás no existen para él.',
    arreglo: 'Sacar del HTML lo que no es contenido (SVG en línea, JSON o CSS incrustados, filtros con miles de opciones) o paginar el listado.',
  },
  'head_cortado': {
    que: 'Un elemento que no va en el <head> (una imagen, un div, un texto suelto) lo cierra antes de tiempo, y metadatos que venían detrás quedan en el <body>. Una fila por cada combinación de elemento y metadatos, con las páginas afectadas.',
    google: 'Documenta que, al encontrar un elemento no válido, da el <head> por terminado y deja de leer lo que venga en él: lo de detrás (robots, canonical, título…) puede no aplicarse. Es error si lo que queda detrás es el robots, el googlebot o el canonical, porque decide si la página entra en el índice. Ojo: la indexabilidad de este informe sigue contando ese robots como aplicado, porque no está comprobado qué hace Google con él en el body; la página puede estar en el índice aunque aquí salga como noindex, o al revés.',
    arreglo: 'Mover ese elemento al <body> o subir los metadatos por encima de él en la plantilla. Suele ser un pixel, un iframe o un bloque de un plugin. Solo se mide en rastreos sin render: en el DOM montado el corte ya no se ve.',
  },
  'meta_fuera_del_head': {
    que: 'El título, la meta description o la meta robots están en el <body>, sin que nada haya cortado el <head>. Una fila por combinación, con las páginas afectadas.',
    google: 'Documenta que van en el <head> y no dice qué hace con ellos fuera. Hay frameworks que los sirven en el body a propósito: Next.js 15.2 o posterior (streaming metadata) los sube al <head> con JavaScript y documenta que Googlebot los lee. Por eso es informativo, no un fallo.',
    arreglo: 'Si el sitio no es de un framework que los suba con JavaScript, moverlos al <head> de la plantilla. Si lo es, comprobar con la inspección de URL de Search Console que Google ve el título y el robots que tocan. Solo se mide en rastreos sin render.',
  },

  // ----- Lo que el HTML declara de sí mismo (decisión 108) -----
  'viewport_ausente': {
    que: 'Páginas indexables sin <meta name="viewport">. Una fila por plantilla, con las páginas afectadas.',
    google: 'No decide si se indexa: la indexación mobile-first indexa la versión que ve el rastreador de smartphone, con o sin viewport. Lo que cambia es cómo se ve: sin viewport, el móvil pinta la página a 980 px de ancho y la encoge, y el usuario tiene que hacer zoom. Si el rastreo fue sin render y el sitio lo pone con JavaScript, aquí sale como ausente: compruébalo en el navegador.',
    arreglo: 'Añadir <meta name="viewport" content="width=device-width, initial-scale=1"> en el <head> de la plantilla.',
  },
  'viewport_incorrecto': {
    que: 'El viewport existe pero no adapta la página al móvil: un ancho fijo (width=1024), sin width=device-width ni initial-scale, o sin content. Una fila por plantilla y motivo.',
    google: 'Igual que sin viewport: no afecta a la indexación, sino a cómo se ve en el móvil (la página se pinta al ancho fijo y se encoge). user-scalable=no y maximum-scale no cuentan aquí: impiden hacer zoom, que es accesibilidad, no adaptación.',
    arreglo: 'Usar content="width=device-width, initial-scale=1" y que la maqueta sea adaptable. Si hay dos viewports, dejar uno.',
  },
  'html_lang_ausente': {
    que: 'Páginas indexables sin atributo lang en <html>, o con el atributo vacío. Una fila por plantilla.',
    google: 'Google no usa el lang para decidir el idioma de la página: lo saca del contenido visible. Sirve a los lectores de pantalla, a la traducción del navegador y a la accesibilidad (WCAG 3.1.1). Es informativo, no un problema de posicionamiento.',
    arreglo: 'Poner <html lang="es"> (o el idioma de cada versión) en la plantilla.',
  },
  'html_lang_no_coincide_hreflang': {
    que: 'El idioma del <html lang> no es el del código con que la página se anota a sí misma en su hreflang (se compara solo el idioma: es y es-ES coinciden; es-MX y es-ES también). Una fila por plantilla y pareja de idiomas.',
    google: 'Google se guía por el hreflang y por el contenido, no por el lang. Que no coincidan dice que uno de los dos está mal, pero no cuál: puede ser un lang="es" fijo en todo un sitio multidioma, o un hreflang mal asignado. Hasta comparar con el idioma real del contenido no se puede afirmar más.',
    arreglo: 'Mirar las páginas de ejemplo: si el contenido está en el idioma del hreflang, corregir el lang de la plantilla; si está en el del lang, revisar el hreflang.',
  },
  'texto_mal_codificado': {
    que: 'El título o el texto de la página llevan caracteres sustituidos (�): los bytes no casan con la codificación con que se leen. Una fila por plantilla, con lo que declaran la cabecera, el meta y el BOM.',
    google: 'Lee la página con la misma codificación que el navegador y, si los bytes no casan, indexa y enseña en el resultado los caracteres rotos: el título y el snippet salen con símbolos de interrogación.',
    arreglo: 'Servir el HTML en la codificación que declara la cabecera Content-Type (lo normal: guardar las plantillas y la base de datos en UTF-8 y declarar charset=utf-8). Solo se mide en rastreos sin render.',
  },
  'charset_incoherente': {
    que: 'La cabecera Content-Type, el <meta charset> y el BOM declaran codificaciones distintas (latin1, iso-8859-1 y windows-1252 cuentan como la misma). El texto sale bien. Una fila por host y combinación.',
    google: 'Manda el BOM y, sin él, la cabecera, como en el navegador: hoy no se rompe nada, pero basta con que el servidor deje de mandar la cabecera (una caché, un CDN, una copia estática) para que se lea con la otra y salga roto.',
    arreglo: 'Declarar la misma codificación en la cabecera y en el <meta charset> de la plantilla. Solo se mide en rastreos sin render.',
  },
  'charset_ausente': {
    que: 'Ni la cabecera Content-Type, ni un <meta charset> en los primeros 1.024 bytes, ni un BOM dicen la codificación, y el HTML lleva caracteres no ASCII. Una fila por host.',
    google: 'Tiene que adivinarla, como el navegador. Suele acertar, pero si no, los acentos salen rotos en el índice y en el snippet. Un meta escrito más abajo de los 1.024 bytes el navegador no lo ve.',
    arreglo: 'Mandar charset=utf-8 en la cabecera Content-Type o poner <meta charset="utf-8"> como primer elemento del <head>.',
  },
  'canonical_relativo': {
    que: 'El canonical está escrito como ruta relativa (/x, x o ../x) o relativa al protocolo (//host/x), no como URL absoluta. Una fila por plantilla y forma.',
    google: 'Admite las rutas relativas y recomienda las absolutas. Lo que rompe es el contexto: con un <base href> a otro sitio, o con la página servida desde un dominio espejo o de staging, la ruta relativa apunta al host equivocado. Es informativo.',
    arreglo: 'Escribir el canonical como URL absoluta, con esquema y host de producción.',
  },
  'canonical_con_fragmento': {
    que: 'El canonical lleva un fragmento (#…). Una fila por plantilla; aparte las que, quitado el fragmento, apuntan a otra página.',
    google: 'Quita el fragmento: un canonical a /p#top es un canonical a /p. Si eso es la propia página, no pasa nada (informativo). Si es otra —lo típico de un enrutado con #/, donde /#/producto/1 queda en la portada—, la página le pide a Google que la consolide en esa otra, y es un aviso.',
    arreglo: 'Quitar el fragmento del canonical. Si el sitio enruta con #, pasar a rutas reales (History API): Google no indexa lo que va detrás del #.',
  },
  'og_ausente': {
    que: 'Páginas indexables sin og:title ni og:image. Una fila por plantilla.',
    google: 'No lo usa para posicionar. Lo usan las redes sociales y las aplicaciones de mensajería al compartir el enlace: sin ellos, la vista previa sale sin imagen o con lo primero que encuentren.',
    arreglo: 'Añadir og:title, og:description y og:image (URL absoluta, imagen grande) en la plantilla.',
  },
  'og_image_relativa': {
    que: 'La og:image está escrita como ruta relativa, no como URL absoluta. Una fila por plantilla y forma.',
    google: 'No afecta al posicionamiento. El protocolo Open Graph pide una URL absoluta, y hay redes que no resuelven la relativa: la vista previa sale sin imagen.',
    arreglo: 'Escribir la og:image como URL absoluta, con esquema y host.',
  },
  'redireccion_por_javascript': {
    que: 'La página redirige con JavaScript (location = "…", location.replace("…")) en cuanto carga, sin condición: una página puente. Se detecta en el HTML servido, sin render, y solo con un destino literal a nivel superior del script; lo que va dentro de una función, de un if o de un temporizador no se afirma.',
    google: 'Google ejecuta el JavaScript al renderizar y suele seguir la redirección, pero más tarde y con menos garantías que una del servidor, y quien no ejecuta JavaScript se queda en la página puente. El destino no se ha seguido en este rastreo: si aparece su código, es porque se llegó a él por otro camino.',
    arreglo: 'Sustituirla por una redirección 301 en el servidor y enlazar directamente el destino.',
  },

  // ----- Títulos, descripciones y encabezados -----
  'title_missing': {
    que: 'La página no tiene <title>.',
    google: 'Se inventa uno a partir del contenido, normalmente peor.',
    arreglo: 'Escribir un título único que diga de qué va la página.',
  },
  'title_too_short': {
    que: 'El título es muy corto.',
    google: 'Es una de las señales más claras del tema de la página: corto, desaprovecha espacio.',
    arreglo: 'Ampliarlo con el término principal y un diferencial.',
  },
  'title_too_long': {
    que: 'El título pasa del ancho que Google enseña (~580 px).',
    google: 'Lo corta con «…» o lo reescribe.',
    arreglo: 'Acortarlo dejando lo importante al principio.',
  },
  'title_generico': {
    que: 'El título es el de relleno del CMS («Sin título», «Untitled document», «Nueva página»…), solo o con la marca del sitio. Sale una vez por valor, con las páginas que lo llevan.',
    google: 'No sabe de qué va la página por el título, y suele reescribirlo.',
    arreglo: 'Escribir el título de cada página.',
  },
  'description_igual_title': {
    que: 'La meta description repite el título, palabra por palabra. Va agrupado por plantilla: suele ser el valor por defecto del CMS o del plugin.',
    google: 'No aporta nada que no diga ya el título, y la suele sustituir por un fragmento de la página.',
    arreglo: 'Escribir una description que resuma la página, o configurar el plugin para que no copie el título.',
  },
  'title_duplicate': {
    que: 'Varias páginas indexables comparten el mismo título.',
    google: 'Le cuesta distinguirlas y puede mostrar la que no es.',
    arreglo: 'Un título distinto por página; si son la misma página, consolidar con canonical o redirección.',
  },
  'title_multiple': {
    que: 'La página tiene más de un <title>.',
    google: 'Usa uno de ellos, no siempre el que se quiere.',
    arreglo: 'Dejar uno solo: suelen ser dos plugins escribiendo la cabecera.',
  },
  'description_missing': {
    que: 'La página no tiene meta description.',
    google: 'Usa un fragmento del texto como descripción del resultado.',
    arreglo: 'Escribir una descripción que invite al clic.',
  },
  'description_too_short': {
    que: 'La meta description es muy corta.',
    google: 'Es probable que la complete o la sustituya por texto de la página.',
    arreglo: 'Ampliarla hasta unas 140-155 letras.',
  },
  'description_too_long': {
    que: 'La meta description pasa del ancho que Google enseña.',
    google: 'La corta con «…».',
    arreglo: 'Acortarla dejando lo importante al principio.',
  },
  'description_duplicate': {
    que: 'Varias páginas indexables comparten la misma meta description.',
    google: 'Tiende a ignorarla y a generar otra.',
    arreglo: 'Una descripción distinta por página, o generarla por plantilla con datos de cada página.',
  },
  'description_multiple': {
    que: 'La página tiene más de una meta description.',
    google: 'Usa una de ellas, no siempre la buena.',
    arreglo: 'Dejar una sola.',
  },
  'h1_missing': {
    que: 'La página no tiene un <h1> visible.',
    google: 'No es obligatorio, pero el titular visible ayuda a entender el tema.',
    arreglo: 'Añadir un <h1> con el titular de la página.',
  },
  'h1_vacio': {
    que: 'La página tiene <h1>, pero vacío: sin texto, o con un logo sin alt dentro. Un logo sin alt dentro del <h1> deja la página sin titular.',
    google: 'Es como no tenerlo: no hay titular que leer.',
    arreglo: 'Poner el titular como texto, o el nombre en el alt de la imagen.',
  },
  'h1_multiple': {
    que: 'La página tiene varios <h1> visibles.',
    google: 'No lo penaliza, pero diluye cuál es el titular.',
    arreglo: 'Dejar un <h1> y pasar el resto a <h2>.',
  },
  'h1_duplicate': {
    que: 'Varias páginas indexables comparten el mismo <h1>.',
    google: 'Señal de que pueden competir entre sí.',
    arreglo: 'Diferenciar los titulares o consolidar las páginas.',
  },

  // ----- Contenido -----
  'duplicate_content': {
    que: 'Varias páginas indexables tienen exactamente el mismo contenido.',
    google: 'Indexa una y descarta el resto, a menudo no la que se quiere.',
    arreglo: 'Elegir la buena y canonicalizar o redirigir las demás.',
  },
  'near_duplicate_content': {
    que: 'Páginas indexables que comparten el 90 % del texto o más.',
    google: 'Puede tratarlas como duplicadas y quedarse solo con una.',
    arreglo: 'Diferenciar el contenido o consolidar en una sola página.',
  },
  'soft_404': {
    que: 'La página responde 200 pero es una página de error: sirve lo mismo que el sitio da a una URL que no existe, su titular dice «Página no encontrada», o es una búsqueda interna sin resultados.',
    google: 'La trata como un 404 («Soft 404» en Search Console) y la saca del índice; si está en el sitemap, como «URL enviada que parece un soft 404». Los enlaces internos que llegan a ella son enlaces rotos disfrazados.',
    arreglo: 'Si el contenido ya no existe, responder 404 o 410 (o 301 a su sustituto) y quitarla del sitemap; si debería existir, arreglar lo que la deja vacía.',
  },
  'soft_404_candidato': {
    que: 'Indicio de página de error sin certeza: un titular que solo contiene la frase («cómo arreglar el error 404»), un listado vacío, un producto descatalogado o una huella de error que casa con media plantilla.',
    google: 'Puede tratarla como soft 404 si no ofrece nada más. No está afirmado: es la lista para revisar (y para la fase con modelo de #11).',
    arreglo: 'Revisar a mano. Si es un error, como «Soft 404»; si es una página útil, nada.',
  },
  'low_word_count': {
    que: 'La página indexable tiene poco contenido propio (sin contar menú ni pie).',
    google: 'Contenido escaso: difícil que posicione y, en cantidad, rebaja la calidad del sitio.',
    arreglo: 'Ampliar el contenido útil, o no indexarla si no aporta.',
  },
  'low_text_ratio': {
    que: 'Poco texto en relación con el HTML.',
    google: 'No es una señal de Google; el aviso ya no se emite en rastreos nuevos.',
    arreglo: 'Nada, salvo que también salga «Pocas palabras».',
  },
  'very_low_text_ratio': {
    que: 'Muy poco texto en relación con el HTML.',
    google: 'No es una señal de Google; el aviso ya no se emite en rastreos nuevos.',
    arreglo: 'Nada, salvo que también salga «Pocas palabras».',
  },

  // ----- Hreflang -----
  'hreflang_missing_return': {
    que: 'La página A apunta a B con hreflang, pero B no apunta de vuelta a A.',
    google: 'Ignora las anotaciones sin retorno.',
    arreglo: 'Que cada versión liste a todas las demás, incluida ella misma.',
  },
  'hreflang_invalid_lang': {
    que: 'El código de idioma o país no es válido (p. ej. «en-UK» en vez de «en-GB»).',
    google: 'Ignora la anotación: ese idioma se queda sin hreflang.',
    arreglo: 'Usar ISO 639-1 para el idioma e ISO 3166-1 para el país.',
  },
  'hreflang_broken_target': {
    que: 'El hreflang apunta a una URL que no responde 200.',
    google: 'Ignora esa alternativa.',
    arreglo: 'Apuntar a la URL buena de cada idioma.',
  },
  'hreflang_in_body': {
    que: 'Las anotaciones hreflang están en el <body>.',
    google: 'Solo las lee en el <head>, la cabecera HTTP o el sitemap.',
    arreglo: 'Mover las etiquetas al <head>.',
  },
  'hreflang_sin_autorreferencia': {
    que: 'La página anota sus otras versiones pero no se anota a sí misma con su idioma (a veces se anota con otra URL: la misma ruta con un parámetro, aunque esa variante se canonicalice a ella).',
    google: 'Su documentación pide que cada versión se liste a sí misma; sin ella la página no declara en qué idioma está y el clúster queda cojo.',
    arreglo: 'Añadir a la plantilla la etiqueta de la propia página, con su URL canónica exacta.',
  },
  'hreflang_sin_x_default': {
    que: 'Ningún clúster hreflang de este host declara x-default (el aviso es uno por host, con cuántos clústeres afecta).',
    google: 'Sin x-default, a quien no casa con ningún idioma le enseña la versión que él elija. No es un error: es la opción de no decidirlo.',
    arreglo: 'Si hay una versión para «el resto del mundo» (o un selector de idioma), anotarla como x-default en todas las páginas del clúster.',
  },
  'hreflang_idioma_repetido': {
    que: 'Dentro del mismo clúster, un código de idioma-región apunta a dos o más URLs distintas.',
    google: 'No sabe cuál de las dos es la de ese idioma y puede ignorar las dos.',
    arreglo: 'Dejar una sola URL por código; si son dos mercados, darles códigos distintos (es-ES, es-MX).',
  },
  'hreflang_destino_no_indexable': {
    que: 'Esta página anota como versión de un idioma una URL que está canonicalizada a otra, en noindex o bloqueada por robots.txt (una incidencia por anotación; la URL anotada va en el detalle).',
    google: 'Descarta esa alternativa: ese mercado se queda sin su versión en los resultados, igual que con un destino roto.',
    arreglo: 'Si la canonicalización es un error, quitarla; si no, apuntar el hreflang a la URL canónica de ese idioma.',
  },
  'hreflang_retorno_incoherente': {
    que: 'A anota B con un código (en-US), B devuelve el enlace a A pero se declara a sí misma con otro (en-GB).',
    google: 'Ve dos códigos para la misma URL y no garantiza cuál usa: la versión puede salir en el país equivocado.',
    arreglo: 'Usar el mismo código para cada URL en todas las páginas del clúster.',
  },

  // ----- Datos estructurados -----
  'structured_data_error': {
    que: 'Un bloque de datos estructurados está roto o le falta algo obligatorio.',
    google: 'No muestra resultados enriquecidos con ese bloque (estrellas, precio, migas…).',
    arreglo: 'Corregir la plantilla que genera el bloque; un arreglo vale para todas las páginas afectadas.',
  },
  'structured_data_warning': {
    que: 'Al bloque de datos estructurados le falta una propiedad recomendada.',
    google: 'Lo acepta, pero el resultado enriquecido puede ser más pobre.',
    arreglo: 'Completar la propiedad en la plantilla si el dato existe.',
  },
  'structured_data_info': {
    que: 'El bloque es de un tipo cuyo resultado enriquecido Google ha retirado (FAQPage desde mayo de 2026, HowTo desde septiembre de 2023).',
    google: 'Lo lee, pero ya no lo muestra como resultado enriquecido: no da preguntas desplegables ni pasos en la búsqueda.',
    arreglo: 'Nada urgente: el marcado no es incorrecto. No merece la pena completarlo ni crearlo por la búsqueda de Google; quitarlo solo si estorba.',
  },

  // ----- Enlazado y sitemap -----
  'orphan_page': {
    que: 'Ninguna página del sitio enlaza a esta.',
    google: 'Le cuesta descubrirla y no le llega autoridad interna.',
    arreglo: 'Enlazarla desde páginas relacionadas, o retirarla si sobra.',
  },
  'sitemap_orphan': {
    que: 'Está en el sitemap, pero ninguna página del sitio la enlaza.',
    google: 'La conoce por el sitemap, pero el propio sitio no la respalda con enlaces.',
    arreglo: 'Enlazarla desde su sección, o quitarla del sitemap si no debe indexarse.',
  },
  'not_in_sitemap': {
    que: 'Página indexable que no está en el sitemap.',
    google: 'Puede descubrirla por enlaces, pero el sitemap le ayuda a priorizar.',
    arreglo: 'Incluir en el sitemap todas las páginas indexables.',
  },
  'sitemap_no_indexable': {
    que: 'Está en el sitemap, pero no es indexable: redirige, da error, lleva noindex, se canonicaliza a otra URL o robots.txt la bloquea. El motivo va en el detalle.',
    google: 'El sitemap es para las URLs que quieres ver en los resultados, y es una de las señales que usa para elegir la canónica. Una entrada que redirige, falla o lleva noindex pide indexar algo que la página niega; con un canonical a otra URL, son dos señales de canónica que no coinciden.',
    arreglo: 'Decidir cuál de las dos está mal: si la página no debe indexarse, quitarla del sitemap (suele ser una regla del generador, no URL a URL); si debe, quitar el noindex o el canonical. Un salto se cambia por la URL final.',
  },
  'solo_enlazada_por_redireccion': {
    que: 'Solo se llega a la página a través de una redirección: los enlaces internos apuntan a la URL vieja.',
    google: 'Acaba dejando de rastrear la URL vieja, y con ella el camino a esta.',
    arreglo: 'Cambiar los enlaces internos para que apunten a la URL final.',
  },
  'not_reachable_from_home': {
    que: 'Página indexable sin ningún camino de enlaces desde la home.',
    google: 'Solo la encuentra por el sitemap o por enlaces externos.',
    arreglo: 'Enlazarla desde la navegación o desde una sección alcanzable.',
  },
  'high_outlink_count': {
    que: 'Más de 100 enlaces en el contenido de la página (sin contar el menú).',
    google: 'Reparte la autoridad entre muchos destinos.',
    arreglo: 'Revisar si todos esos enlaces aportan.',
  },
  'enlace_no_rastreable': {
    que: 'Enlaces que una persona puede pulsar y Google no puede seguir: href="javascript:…", <a onclick> sin href, <a routerLink>, <span href>, una tarjeta con onclick o una ruta en el fragmento (#/…). Si el mismo componente está en 10 páginas o más se cuenta una vez («plantilla»); si no, por página.',
    google: 'Solo sigue un <a> con un href que sea una URL. Por estos enlaces no descubre el destino ni le pasa autoridad. Si es «aviso», detrás hay una URL interna; si es «info», el código no dice a dónde va (un desplegable con javascript:void(0)).',
    arreglo: 'Convertirlos en <a href="/destino"> (el onclick puede quedarse). En una SPA, rutas con la History API en vez de #/. El de plantilla se arregla una vez, en el componente.',
  },
  'enlace_interno_a_error': {
    que: 'La página enlaza a URLs internas que responden 4xx o 5xx. Los enlaces rotos que salen en 10 páginas o más no se repiten aquí: los avisa la propia URL rota.',
    google: 'Rastrea un error por cada enlace, y la autoridad que pasaba por ahí se pierde.',
    arreglo: 'Cambiar o quitar los enlaces de esta página; si el destino debería existir, arreglarlo o redirigirlo.',
  },
  'enlace_sin_ancla': {
    que: 'Enlace interno sin ningún nombre: sin texto, sin aria-label ni title, y sin alt si es una imagen. Si el mismo enlace (posición y destino) está en diez páginas o más, sale una vez, como plantilla. En el caso de una imagen, el arreglo es el mismo que el de «imagen sin alt».',
    google: 'El enlace cuenta, pero el texto ancla es lo que le dice de qué va el destino, y aquí no hay ninguno.',
    arreglo: 'Poner texto al enlace, o un alt descriptivo a la imagen (o aria-label si es un icono).',
  },
  'enlace_interno_a_redireccion': {
    que: 'Enlaces internos a una URL que redirige. «Plantilla»: la URL que redirige está enlazada desde 10 páginas o más (el aviso va en ella); «página»: los de esta página.',
    google: 'Sigue la redirección y pasa la señal, pero gasta un rastreo más por enlace y prefiere que se enlace la URL final.',
    arreglo: 'Apuntar los enlaces a la URL final. Los de plantilla se cambian una vez, en el menú o el pie.',
  },
  'utm_en_enlace_interno': {
    que: 'Enlaces internos con parámetros utm_*. Se avisa una vez por URL con utm, con cuántas páginas la enlazan.',
    google: 'Es otra URL que rastrear; si se puede indexar (aviso) compite con la limpia. Además, en Analytics reinicia la sesión y atribuye la visita a una campaña falsa.',
    arreglo: 'Quitar los utm de los enlaces internos. Para medir clics internos, usar eventos, no parámetros de campaña.',
  },

  // ----- Paginación (decisión 122) -----
  'paginacion_canonical_a_la_primera': {
    que: 'Las páginas 2, 3… de un listado declaran como canonical la página 1 de su serie. Es de plantilla: una fila por forma de listado, con cuántas series y páginas.',
    google: 'Pide que cada página de la serie sea su propia canónica. Con el canonical a la 1 puede dejar de indexar las 2+ y dejar de seguir lo que solo enlazan ellas (productos o artículos antiguos). rel=next/prev no influye: Google no lo usa desde 2019.',
    arreglo: 'Que cada página paginada sea autocanónica (la 2 a la 2). Si existe una «ver todo» que carga rápido, el canonical a ella es otra opción válida, y no sale aquí.',
  },
  'paginacion_noindex': {
    que: 'Las páginas 2, 3… de un listado llevan noindex. Es de plantilla: una fila por forma de listado, con cuántas páginas indexables solo cuelgan de ella. Es aviso (y no información) cuando deja alguna colgando.',
    google: 'Es una práctica corriente y no es un error en sí: la página 1 sigue indexada. Pero con el tiempo trata sus enlaces como nofollow, así que lo que solo se enlaza desde ahí deja de estar enlazado.',
    arreglo: 'Nada si todo lo que listan se enlaza también desde otro sitio. Si deja páginas colgando, quitar el noindex de la paginación (un cambio de plantilla) en vez de enlazarlas una a una.',
  },
  'solo_enlazada_desde_paginacion_excluida': {
    que: 'Página indexable a la que solo llegan enlaces seguibles desde páginas 2+ de un listado que están en noindex o canonicalizadas a la página 1. Sustituye a «Página huérfana», «Huérfana del sitemap» y «Sin camino desde la home» en esa página. Es información cuando la serie ya lleva el aviso (se arregla allí) y aviso cuando no.',
    google: 'Va dejando de rastrear las páginas 2+ excluidas y no cuenta sus enlaces: para Google esta página acaba sin enlaces internos, aunque en el sitio se pueda llegar a ella. Un enlace nofollow tampoco es un camino.',
    arreglo: 'Si la serie lleva el aviso, arreglar la paginación (ver «Paginación en noindex»). Si no, enlazarla desde una página que cuente: la 1 del listado, la navegación, un artículo relacionado.',
  },
  'solo_enlazada_desde_paginacion_profunda': {
    que: 'Páginas indexables a las que solo enlazan las páginas 2, 3… de un listado (indexables). Es de plantilla: una fila por forma de listado.',
    google: 'Las encuentra, pero por la cola del listado: cada elemento nuevo las empuja una página más lejos, y lo que queda muy hondo se rastrea menos.',
    arreglo: 'Enlazar lo importante desde fuera de la paginación (categorías, relacionados, la navegación), o mostrar más elementos por página.',
  },
  'paginacion_sin_enlace': {
    que: 'La página anuncia la siguiente (rel=next, o un botón «cargar más» que la carga con JavaScript) y no tiene NINGÚN <a href> a ella. Si enlaza la página siguiente con otra URL, no sale aquí sino en «rel=next/prev incorrecto». No dice que la siguiente sea inalcanzable: puede estar enlazada desde otra página.',
    google: 'Solo sigue los <a href>. No pulsa botones ni usa rel=next para descubrir, así que las páginas 2+ (y lo que solo enlazan ellas) pueden no descubrirse nunca.',
    arreglo: 'Poner enlaces <a href="…/page/2/"> a las páginas siguientes en el HTML, aunque el «cargar más» siga funcionando con JavaScript encima.',
  },
  'paginacion_siguiente_rota': {
    que: 'rel=next o rel=prev apunta a una URL rastreada que responde 4xx o 5xx (el 429 no cuenta: es el sitio frenando al rastreador).',
    google: 'No usa rel=next/prev, así que no afecta al índice; pero suele delatar que la plantilla calcula mal la última página, y el mismo error puede estar en los <a>.',
    arreglo: 'Corregir la plantilla para que la última página no declare siguiente y los enlaces de paginación apunten a páginas que existen.',
  },
  'paginacion_rel_incorrecto': {
    que: 'rel=next o rel=prev apunta a una redirección, fuera de la serie (otro host u otra ruta), o a una URL distinta de la que enlaza la paginación (el rel dice /page/2/ y el enlace es ?e-page-1f=2). Es de plantilla, con el reparto de motivos.',
    google: 'No usa rel=next/prev desde 2019, así que no afecta al índice. Es un fallo de plantilla, y otros buscadores sí los leen.',
    arreglo: 'Que el rel apunte a la misma URL que el enlace de la página siguiente, o quitarlo.',
  },
  'enlace_externo_roto': {
    que: 'El sitio enlaza una URL de otro sitio que responde 404, 410 u otro 4xx (o que redirige a uno), comprobado con GET. Un aviso por URL rota, con cuántas páginas la enlazan. Solo con «Comprobar enlaces externos» activado; un 403, un 429 o un 5xx no se avisan (es lo que responde un WAF o una caída puntual).',
    google: 'No penaliza el sitio por un enlace roto a otro, pero es una mala experiencia, y un sitio con muchos enlaces muertos da señal de descuido y deja de pasar el valor de la cita.',
    arreglo: 'Cambiar el enlace por la URL nueva del recurso o quitarlo. Si sale en muchas páginas, está en la plantilla: se arregla una vez.',
  },
  'dominio_externo_muerto': {
    que: 'El sitio enlaza a un dominio que no existe (el DNS no lo encuentra, comprobado con el resolver) o que no acepta conexiones (dos intentos). Una incidencia por dominio, con sus URLs enlazadas y las páginas desde las que se enlaza.',
    google: 'Un dominio caducado lo puede comprar cualquiera: el sitio acabaría enlazando a lo que publique el nuevo dueño. Para quien pulsa, el enlace no lleva a ningún sitio.',
    arreglo: 'Quitar o sustituir todos los enlaces a ese dominio. Un dominio aparcado que responde 200 con una página de parking no se detecta: revisar los enlaces a dominios desconocidos de vez en cuando.',
  },
  'broken_resource': {
    que: 'Una imagen, hoja de estilos o script que cargan las páginas responde 404/410 u otro 4xx (o su dominio no existe). Un aviso por recurso, con cuántas páginas lo cargan. Solo con «Comprobar recursos embebidos» (o «de otros hosts») activado.',
    google: 'Una imagen rota no se indexa y deja un hueco; un CSS o un JS roto puede romper el render que ve Google.',
    arreglo: 'Restaurar el fichero o cambiar la referencia en la plantilla o el contenido. Si lo cargan muchas páginas, está en la plantilla.',
  },

  // ----- Imágenes -----
  'image_missing_alt': {
    que: 'Imagen sin texto alternativo. Se cuenta una vez por imagen, no por página.',
    google: 'No sabe qué muestra la imagen; si enlaza, el alt es su ancla.',
    arreglo: 'Añadir un alt descriptivo. Un logo o un icono de plantilla se arregla una vez.',
  },

  // ----- URLs -----
  'url_too_long': {
    que: 'La URL tiene más de 115 caracteres.',
    google: 'No la penaliza, pero las URLs largas se comparten y se leen peor.',
    arreglo: 'Acortar el slug si se puede, con redirección 301.',
  },
  'url_non_ascii': {
    que: 'La URL lleva caracteres no ASCII (acentos, ñ…).',
    google: 'Funcionan, pero se codifican y se comparten mal.',
    arreglo: 'Usar slugs sin acentos en las URLs nuevas.',
  },
  'url_uppercase': {
    que: 'La URL tiene mayúsculas.',
    google: 'Distingue mayúsculas: «/A» y «/a» son URLs distintas y pueden duplicarse.',
    arreglo: 'URLs en minúsculas y redirección de las variantes.',
  },
  'url_underscores': {
    que: 'La URL separa palabras con guion bajo.',
    google: 'Recomienda guiones para separar palabras.',
    arreglo: 'Usar guiones en las URLs nuevas.',
  },
  'url_multiple_slashes': {
    que: 'La URL tiene barras dobles (//).',
    google: 'Puede tratarla como una URL distinta de la buena.',
    arreglo: 'Corregir el enlace que la genera.',
  },
  'url_has_parameters': {
    que: 'URL con parámetros (?…).',
    google: 'Cada combinación de parámetros es una URL más que rastrear.',
    arreglo: 'Canonicalizar a la versión sin parámetros o no enlazarlas.',
  },
  'url_non_seo_friendly': {
    que: 'URL que no describe su contenido (ids, códigos).',
    google: 'Una URL legible ayuda poco, pero ayuda al usuario a elegir el resultado.',
    arreglo: 'Usar slugs descriptivos en las URLs nuevas.',
  },
  'url_cms_faceted': {
    que: 'URL de filtro o de funcionamiento interno del CMS.',
    google: 'Gasta rastreo en URLs que no deberían posicionar.',
    arreglo: 'Bloquearlas en robots.txt o no enlazarlas.',
  },

  // ----- Seguridad -----
  // ----- Servidor: tiempo, compresión y certificado (decisiones 109 y 110) -----
  'respuesta_lenta': {
    que: 'El servidor tarda en empezar a responder (TTFB: el tiempo hasta el primer byte, sin DNS, conexión ni TLS) en una plantilla entera. Va una fila por host y plantilla con la mediana y el p90; solo plantillas con 20 páginas o más. La cifra depende de la concurrencia del rastreo: el detalle la dice.',
    google: 'Google rastrea menos un servidor lento (Search Console, «Estadísticas de rastreo», tiempo medio de respuesta). No es una señal de posicionamiento por sí misma, y no es Core Web Vitals: CrUX mide a usuarios reales.',
    arreglo: 'Mirar con el equipo técnico qué hace esa plantilla en el servidor (consultas, caché de página, CDN). Comparar con las plantillas rápidas del mismo host.',
  },
  'html_sin_comprimir': {
    que: 'El HTML se sirve sin Content-Encoding (ni gzip, ni Brotli, ni zstd) aunque el rastreador lo pide como un navegador. Una fila por host; las páginas de menos de 1 KB no cuentan. Si el host mezcla, «por plantilla» dice dónde mirar.',
    google: 'Los rastreadores de Google admiten gzip, deflate y Brotli. Sin compresión cada página tarda más en bajar y se rastrean menos con el mismo presupuesto. No afecta al posicionamiento.',
    arreglo: 'Activar gzip o Brotli para text/html en el servidor o el CDN.',
  },
  'tls_certificado_invalido': {
    que: 'El certificado del host no vale: caducado, de otro nombre, autofirmado o con la cadena incompleta. Una fila por host, con el motivo y las páginas que fallaron por eso.',
    google: 'El navegador no abre la página sin un aviso de seguridad y Googlebot no puede rastrearla por https: si dura, sale del índice.',
    arreglo: 'Renovar o reinstalar el certificado (con la cadena intermedia completa) y revisar la renovación automática.',
  },
  'tls_caduca_pronto': {
    que: 'El certificado caduca en 7 días o menos, contados desde el día del rastreo (no se recalcula al re-analizar). Con certificados de 45 a 90 días y renovación a 30 días, un sitio sano no baja de ahí: si sale, la renovación automática probablemente ha fallado.',
    google: 'Nada todavía. El día que caduque, lo de «certificado inválido».',
    arreglo: 'Comprobar la renovación automática (certbot, el panel del CDN) antes de la fecha.',
  },
  'tls_version_antigua': {
    que: 'El servidor solo negocia TLS 1.0 o 1.1. Lo negociado es lo máximo que admite: que además acepte versiones viejas no se mide.',
    google: 'Chrome y los demás navegadores no conectan con TLS 1.0 ni 1.1 desde 2020: la página no se abre.',
    arreglo: 'Habilitar TLS 1.2 y 1.3 en el servidor o el balanceador.',
  },

  'http_url': {
    que: 'La página se sirve por http, sin cifrar.',
    google: 'HTTPS es señal de posicionamiento y el navegador avisa de «no seguro».',
    arreglo: 'Redirigir todo a https.',
  },
  'mixed_content': {
    que: 'Página https que carga recursos por http.',
    google: 'El navegador bloquea o avisa: la página puede verse rota.',
    arreglo: 'Cargar todos los recursos por https.',
  },
  'missing_hsts': {
    que: 'El servidor no envía la cabecera HSTS. Se cuenta una vez por host.',
    google: 'No afecta al posicionamiento; es seguridad.',
    arreglo: 'Configurar Strict-Transport-Security en el servidor.',
  },
  'missing_csp': {
    que: 'El servidor no envía Content-Security-Policy. Una vez por host.',
    google: 'No afecta al posicionamiento; es seguridad.',
    arreglo: 'Definir una política CSP con el equipo técnico.',
  },
  'missing_x_content_type_options': {
    que: 'Falta la cabecera X-Content-Type-Options. Una vez por host.',
    google: 'No afecta al posicionamiento; es seguridad.',
    arreglo: 'Enviar «X-Content-Type-Options: nosniff».',
  },
  'missing_x_frame_options': {
    que: 'Falta X-Frame-Options (y la CSP no lleva frame-ancestors). Una vez por host.',
    google: 'No afecta al posicionamiento; es seguridad.',
    arreglo: 'Enviar X-Frame-Options o frame-ancestors en la CSP.',
  },
  // Paridad movil/escritorio (decision 119): una fila por plantilla y campo
  'paridad_movil_indexacion': {
    que: 'Con el perfil móvil la página lleva otro canonical, un noindex o un nofollow que la versión rastreada no tiene (o al revés). Va por plantilla: «páginas» de las muestreadas de esa plantilla.',
    google: 'Indexa la versión que recibe su rastreador de smartphone (indexación mobile-first). Un noindex o un canonical a otra URL solo en móvil saca la página del índice o la consolida en otra aunque la de escritorio esté bien.',
    arreglo: 'Servir las mismas directivas a móvil y escritorio: revisar la plantilla móvil o la regla del servidor/CDN que decide por User-Agent. Si es servido dinámico, añadir «Vary: User-Agent».',
  },
  'paridad_movil_contenido': {
    que: 'Con el perfil móvil cambian el title, el H1 o la description, o faltan contenido principal o enlaces internos (por encima del umbral). Lo que también cambia entre dos peticiones de escritorio se descarta como volátil.',
    google: 'Indexa y enlaza lo que ve en móvil: el texto o los enlaces que solo están en escritorio no cuentan, y un menú que solo se monta al pulsar no reparte enlazado.',
    arreglo: 'Que la versión móvil lleve el mismo contenido principal, titulares y enlaces internos (en el HTML, no solo al interactuar).',
  },
  'paridad_movil_respuesta': {
    que: 'Con el perfil móvil la URL responde otro código o redirige a otra URL (por ejemplo a un m-dot). Si es un m-dot con la anotación completa (rel=alternate media en escritorio y canonical a escritorio en móvil) sale como informativa.',
    google: 'Con URLs separadas necesita la anotación en los dos sentidos para entender que son la misma página; sin ella puede indexar las dos o quedarse con la que no es. Otro código en móvil es lo que indexa.',
    arreglo: 'Si el sitio usa URLs móviles separadas, anotar las dos versiones; si no, servir la misma URL y el mismo código a los dos.',
  },
  'unsafe_crossorigin': {
    que: 'Enlace con target="_blank" sin rel="noopener".',
    google: 'Nada: desde 2021 los navegadores lo aplican solos. Ya no se emite.',
    arreglo: 'Nada.',
  },
};

// Nombres de los tipos que el analizador emite y no tenían etiqueta.
const ISSUE_LABEL_EXTRA = {
  canonical_multiple: 'Varios canonicals',
  canonical_in_body: 'Canonical fuera del head',
  canonical_a_http: 'Canonical a http',
  canonical_a_error: 'Canonical a un error',
  canonical_a_redireccion: 'Canonical a una redirección',
  canonical_a_no_indexable: 'Canonical a una noindex',
  canonical_encadenado: 'Canonical encadenado',
  canonical_sin_enlazar: 'Canónica sin enlaces internos',
  redireccion_a_error: 'Redirección a un error',
  title_multiple: 'Varios títulos',
  description_multiple: 'Varias meta descriptions',
  hreflang_in_body: 'Hreflang fuera del head',
  hreflang_sin_autorreferencia: 'Hreflang sin autorreferencia',
  hreflang_sin_x_default: 'Hreflang sin x-default',
  hreflang_idioma_repetido: 'Hreflang idioma repetido',
  hreflang_destino_no_indexable: 'Hreflang a URL no indexable',
  hreflang_retorno_incoherente: 'Hreflang retorno incoherente',
  html_supera_2mb: 'HTML de más de 2 MB',
  head_cortado: 'Head cortado antes de tiempo',
  meta_fuera_del_head: 'Metadatos fuera del head',
  viewport_ausente: 'Sin meta viewport',
  viewport_incorrecto: 'Viewport que no adapta al móvil',
  html_lang_ausente: 'Sin <html lang>',
  html_lang_no_coincide_hreflang: 'html lang distinto del hreflang',
  texto_mal_codificado: 'Texto mal codificado',
  charset_incoherente: 'Charset incoherente',
  charset_ausente: 'Charset sin declarar',
  canonical_relativo: 'Canonical relativo',
  canonical_con_fragmento: 'Canonical con fragmento',
  og_ausente: 'Sin Open Graph',
  og_image_relativa: 'og:image relativa',
  redireccion_por_javascript: 'Redirección por JavaScript',

  respuesta_no_valida: 'No era la página (WAF, captcha, 429)',
  robots_txt_inaccesible: 'robots.txt con error o sin respuesta',
  robots_difiere_para_googlebot: 'robots.txt distinto para Googlebot',
  bot_busqueda_ia_bloqueado: 'Buscador de IA bloqueado en robots.txt',
  enlace_no_rastreable: 'Enlace que Google no puede seguir',
  enlace_interno_a_error: 'Enlace interno a un error',
  enlace_interno_a_redireccion: 'Enlace interno a una redirección',
  utm_en_enlace_interno: 'Enlace interno con utm',
  respuesta_lenta: 'Servidor lento (TTFB por plantilla)',
  html_sin_comprimir: 'HTML sin comprimir',
  tls_certificado_invalido: 'Certificado TLS no válido',
  tls_caduca_pronto: 'Certificado TLS a punto de caducar',
  tls_version_antigua: 'TLS antiguo (1.0/1.1)',
  enlace_sin_ancla: 'Enlace interno sin texto ancla',
  // Decisiones 120 y 121
  redireccion_a_noindex: 'Redirección a una noindex',
  meta_refresh_redireccion: 'Redirección por meta refresh',
  noindex_y_canonical_a_otra: 'Noindex con canonical a otra',
  canonical_html_y_cabecera_distintos: 'Canonical del HTML ≠ cabecera',
  h1_vacio: 'H1 vacío',
  title_generico: 'Título de relleno',
  description_igual_title: 'Description igual al título',
  paginacion_canonical_a_la_primera: 'Paginación con canonical a la página 1',
  paginacion_noindex: 'Paginación en noindex',
  solo_enlazada_desde_paginacion_excluida: 'Solo enlazada desde paginación excluida',
  paginacion_sin_enlace: 'Paginación sin <a> a la siguiente',
  paginacion_siguiente_rota: 'rel=next/prev a un error',
  paginacion_rel_incorrecto: 'rel=next/prev incorrecto',
  solo_enlazada_desde_paginacion_profunda: 'Solo enlazada desde páginas 2+',
  paridad_movil_indexacion: 'Móvil: otro canonical o noindex',
  paridad_movil_contenido: 'Móvil: otro contenido',
  paridad_movil_respuesta: 'Móvil: otra respuesta',
  enlace_externo_roto: 'Enlace externo roto',
  dominio_externo_muerto: 'Dominio externo muerto',
  broken_resource: 'Recurso roto',
};

// Los tipos de `enlaces_no_rastreables.crawlability` (decision 106).
const TIPO_NO_RASTREABLE = {
  javascript_href: 'href="javascript:"',
  onclick_sin_href: '<a onclick> sin href',
  a_sin_href: '<a> sin href (routerLink…)',
  href_fuera_de_a: 'href fuera de un <a>',
  onclick_en_otra_etiqueta: 'onclick en otra etiqueta',
  fragmento_como_ruta: 'ruta en el fragmento (#/)',
};

// Cómo se leen los campos de `issues.details`. Lo que no está aquí se
// enseña con su nombre: mejor un nombre técnico que esconder el dato.
const DETALLE_LABEL = {
  status_code: 'código',
  length: 'caracteres',
  min: 'mínimo',
  max: 'máximo',
  pixels: 'píxeles',
  max_pixels: 'máx. píxeles',
  word_count: 'palabras',
  threshold: 'umbral',
  duplicate_count: 'páginas en el grupo',
  canonical: 'canonical',
  canonical_href: 'canonical',
  target: 'destino',
  hops: 'saltos',
  target_status: 'código del destino',
  final_status: 'código final',
  destino_final: 'destino final',
  variantes: 'variantes',
  variantes_enlazadas: 'variantes enlazadas',
  enlaces_a_variantes: 'enlaces a las variantes',
  lang: 'idioma',
  href: 'destino',
  paginas_afectadas: 'páginas',
  cluster: 'clúster',
  alternativas: 'alternativas',
  variante_propia: 'se anota como',
  clusters_afectados: 'clústeres sin x-default',
  clusters_del_host: 'clústeres del host',
  cuantas_urls: 'URLs con ese código',
  lang_del_destino: 'código del destino',
  motivo: 'motivo',

  ejemplos: 'ejemplos',
  similarity: 'similitud',
  outlinks: 'enlaces',
  schema_type: 'tipo',
  errors: 'errores',
  warnings: 'avisos',
  bytes: 'bytes',
  limite_bytes: 'límite',
  bytes_sin_leer: 'bytes sin leer',
  pct_sin_leer: '% sin leer',
  cortado_por: 'lo corta',
  fuera_del_head: 'quedan fuera',
  directiva_en_duda: 'robots o canonical detrás del corte',
  // Lo que el HTML declara de sí mismo (decisión 108)
  plantilla: 'plantilla',
  rastreo_con_render: 'rastreo con render',
  con_varios_viewports: 'con varios viewports',
  atributo_vacio: 'con lang vacío',
  html_lang: 'html lang',
  hreflang: 'se anota en hreflang como',
  charset_cabecera: 'charset de la cabecera',
  charset_meta: 'charset del meta',
  charset_bom: 'BOM',
  manda: 'manda',
  forma: 'forma',
  apunta_a_si_misma: 'apunta a la propia página',
  palabras: 'palabras',
  destino_status: 'código del destino',
  destino_no_comprobado: 'destino no comprobado',
  // Avisos de enlaces (decision 106)
  paginas_que_enlazan: 'páginas que la enlazan',
  enlaces: 'enlaces',
  destinos: 'destinos',
  con_destino_interno: 'con destino interno',
  destinos_fuera_del_censo: 'destinos fuera del censo',
  destinos_internos_distintos: 'destinos internos distintos',
  apariciones: 'apariciones',
  destino: 'destino',
  href_ejemplo: 'código',
  destino_ejemplo: 'destino',
  etiqueta: 'etiqueta',
  posicion: 'posición',
  ancla: 'ancla',
  // Avisos del servidor (decisiones 109 y 110)
  paginas: 'páginas',
  mediana_ms: 'mediana (ms)',
  p90_ms: 'p90 (ms)',
  umbral_ms: 'umbral (ms)',
  concurrencia_por_dominio: 'concurrencia por dominio',
  paginas_del_host: 'páginas del host',
  bytes_medianos: 'bytes (mediana)',
  por_plantilla: 'por plantilla',
  not_after: 'caduca',
  dias_restantes: 'días que quedaban',
  umbral_dias: 'umbral (días)',
  version: 'versión TLS',
  emisor: 'emisor',
  fecha_sonda: 'medido el',
  paginas_con_error_tls: 'páginas que fallaron por TLS',
  // robots.txt (decisiones 112 y 113)
  intentos: 'intentos',
  intermitente: 'intermitente: a la segunda respondió',
  limitado: '429: puede ser un límite solo para el rastreador',
  evaluadas: 'URLs evaluadas',
  solo_googlebot: 'solo Googlebot bloqueado',
  solo_rastreador: 'solo el rastreador bloqueado',
  ejemplos_solo_googlebot: 'ejemplos solo Googlebot',
  ejemplos_solo_rastreador: 'ejemplos solo el rastreador',
  // Decisiones 120 y 121
  fuente: 'de dónde sale',
  canonical_html: 'canonical del HTML',
  canonical_cabecera: 'canonical de la cabecera',
  destino_indexable: 'destino indexable',
  paginas_ejemplo: 'páginas de ejemplo',
  redirecciones_que_llegan: 'redirecciones que llegan',
  final: 'URL final',
  retardo_s: 'retardo (s)',
  h1_vacios: 'h1 vacíos',
  alcance: 'alcance',
  // Paginación (decision 122)
  series: 'series',
  motivos: 'por qué no cuentan',
  ejemplos_origen: 'enlazada desde',
  en_sitemap: 'en el sitemap',
  con_parametros: 'URL con parámetros',
  siguiente: 'siguiente',
  rel: 'rel',
  indexables_que_solo_cuelgan_de_ella: 'indexables que solo cuelgan de ella',
  ejemplos_indexables: 'ejemplos de esas indexables',
  solo_enlazadas_por_la_serie: 'páginas 2+ que solo enlaza la propia serie',
  con_boton_js: 'con botón por JavaScript',
  otros_entrantes: 'otros entrantes (nofollow o que no son camino)',
  // Paridad movil (decision 119)
  campo: 'campo',
  perfil: 'perfil',
  paginas_muestreadas: 'muestreadas de la plantilla',
  sin_vary_user_agent: 'sin «Vary: User-Agent»',
  // Sondas de respuesta (decisiones 123 y 124)
  veredicto: 'veredicto',
  final_url: 'acaba en',
  urls_distintas: 'URLs distintas',
  misma_casa: 'del mismo dominio',
  recurso: 'recurso',
  resource_type: 'tipo',
  interno: 'del sitio',
  host: 'host',
  comprobadas: 'comprobadas',
  distintas: 'distintas',
  error: 'error',
};
