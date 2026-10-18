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
  'robots_invalid_syntax': {
    que: 'La meta robots está mal escrita (directivas separadas por «/», «|» o «;»).',
    google: 'Ignora lo que no entiende: un «noindex/nofollow» no se aplica.',
    arreglo: 'Separar las directivas por comas: «noindex, nofollow».',
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

  respuesta_no_valida: 'No era la página (WAF, captcha, 429)',
  enlace_no_rastreable: 'Enlace que Google no puede seguir',
  enlace_interno_a_error: 'Enlace interno a un error',
  enlace_interno_a_redireccion: 'Enlace interno a una redirección',
  utm_en_enlace_interno: 'Enlace interno con utm',
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
};
