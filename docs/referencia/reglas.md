# Reglas por area

> Movidas desde «Lo que no se puede romper» de `CLAUDE.md` (2026-10-19). Alli
> quedan solo las que cualquier sesion puede romper sin estar tocando el area;
> aqui, las que muerden al tocar una parte concreta. Lee la seccion de lo que
> vayas a cambiar. Una linea por regla; el porque y la medida, en la decision
> citada (`docs/decisiones/README.md`).

## Rastreo (spider, worker, middlewares)

- **Scrapy corre como subproceso** por job, con los settings por `-s`; nunca en
  el proceso del worker (el reactor de Twisted no reinicia). Decisiones 1-2.
- **El UA y la huella TLS son el mismo navegador**: salen de `shared/perfiles.py`
  (`IMPERSONATE_POR_DEFECTO` -> `UA_POR_DEFECTO`), nunca un `chrome124` literal. 132.
- **Las cabeceras, cookies y Basic del job solo van a los hosts de las semillas**,
  tambien con render y tras una redireccion; sin jar de cookies por defecto. 114.
- **Una sonda no es una URL del censo** (soft 404, enlace externo, recurso,
  og:image): su propio callback, sin `PageItem`, sin gastar `max_urls` ni contar
  para la salud; vive en `jobs.soft404`, `respuestas_sondeadas` o
  `adapter_entities`. Decisiones 105, 123, 124, 154 (la de la og:image,
  encendida de serie: acotada por `og_image_muestra` y por plantilla) y 161
  (la REST API de WordPress: dos a la vez, en el slot del host). La de axe
  (174) va en `capturas_alternativas` con `perfil='axe'`: quien lea esa tabla
  filtra su perfil.
- **Un muro no es la pagina** (`urls.response_class`, `shared/respuestas.py`): ni
  indexable ni incidencias de pagina, y un rastreo bloqueado es `failed` y no alerta. 103.
- **Un tope por carpeta se aplica al encolar** (lo que no cabe no se pide) y su
  regla vive en `shared/topes_carpeta.py`; con un grupo cortado el censo es
  `tope_por_carpeta` (truncado) y en TODO el censo (no solo en el grupo: lo no
  pedido también enlazaba fuera) no se afirman huérfanas ni páginas sin camino. 172.
- **Una URL que escribe una persona la descarga el worker, nunca la API**, por
  `shared/prueba_en_vivo.py`: redirecciones a mano, todas las IP globales y la
  IP fijada con `CURLOPT_RESOLVE` en cada salto. 166.

## Enlaces y PageRank

- **`links` solo guarda aristas del grafo**: lo que Google no puede seguir
  (`javascript:`, `onclick`, `<span href>`) va en `enlaces_no_rastreables`. 106.
- **Un `noindex` o una pagina que no da 200 no reparten enlazado.** 38, 57.
- Lo de leer `links` (filtrar por job, `SUM(repeticiones)`, censos archivados)
  esta en `CLAUDE.md` porque lo rompe cualquier consulta.

## Analisis, incidencias y estado del job

- **Lo que analiza o archiva un censo entero toma el candado del job**
  (`shared/candados.py`): el re-analisis (93) restaura, analiza y re-archiva
  los enlaces del censo dentro del mismo candado (95, 150).
- **Un job solo es `completed` si su dato esta completo**; cero URLs no es un
  sitio limpio. 28, 32.
- **La accesibilidad nunca es `error`** (topa la nota SEO) y lo que axe deja en
  `incomplete` no se afirma; sin paginas evaluadas no hay «sin fallos». 174.
- **Un valor extraido a medida sin fila es «no evaluado», no «vacio»**: «no
  contiene» es `estado='sin_coincidencia'` y nunca incluye bloqueadas ni errores. 104.
- **Un segmento es `urls.segmento`, materializado** (analisis o `POST /segmentos`); la
  primera regla VERDADERA gana y un dato desconocido no casa ni negado. Decision 125.
- **El hreflang de una URL es la union de sus tres fuentes** (`<head>`, cabecera
  `Link`, sitemap); el del sitemap vive en `sitemap_entries.hreflang`, no en la
  tabla `hreflang`, y con el sitemap leido a medias (`estado_hreflang_sitemap`)
  no se afirma que a una pagina sin entrada le falte nada. Decision 167.

## Borrado y almacenamiento

- **Un censo se borra con `borrar_job`** (ficha, cliente y borrado en masa): es la que
  quita tambien el Parquet de sus enlaces archivados. Decision 96.
- Una tabla nueva del censo entra en `shared/almacenamiento.py` y en el borrado
  en masa en el mismo PR: lo exige el test de tablas del censo.
- **Un contenedor efimero se borra con `docker rm -fv`**, no `-f`: el `--rm` no
  cubre el borrado forzado y cada Postgres deja un volumen anonimo de mas de 1 GB
  (53 GB huerfanos en el Mac el 2026-10-21). Lo bloquea el hook de sesiones.
