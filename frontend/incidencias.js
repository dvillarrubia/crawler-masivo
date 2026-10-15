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
  'redirect_chain': {
    que: 'Para llegar a la URL final hay dos saltos o más (A → B → C).',
    google: 'Sigue la cadena, pero cada salto gasta rastreo y Google pide evitarlas.',
    arreglo: 'Que la primera URL redirija directamente a la final, y actualizar los enlaces internos.',
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
  'canonical_broken': {
    que: 'El canonical apunta a una URL que no responde 200.',
    google: 'Descarta el canonical y elige por su cuenta.',
    arreglo: 'Apuntar el canonical a la URL buena y que esa URL responda 200.',
  },
  'canonical_multiple': {
    que: 'La página declara más de un canonical.',
    google: 'Si no coinciden, los ignora todos y elige por su cuenta.',
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
  title_multiple: 'Varios títulos',
  description_multiple: 'Varias meta descriptions',
  hreflang_in_body: 'Hreflang fuera del head',
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
  lang: 'idioma',
  href: 'destino',
  paginas_afectadas: 'páginas',
  similarity: 'similitud',
  outlinks: 'enlaces',
  schema_type: 'tipo',
  errors: 'errores',
  warnings: 'avisos',
};
