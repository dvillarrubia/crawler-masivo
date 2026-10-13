# Auditoría de UX de la interfaz

> Hecha el 2026-10-13 recorriendo la app en local con el censo de Lopesan
> (`e77ac4f6`, 5.751 URLs, 38.922 incidencias), más lectura de
> `frontend/index.html` y `frontend/app.js`. La secuencia viva es el milestone
> **M6 · Interfaz** de GitHub; este documento explica **qué se vio y por qué
> ese orden**. Cada hallazgo lleva un id (`UX-nn`) que usan las issues.

## El diagnóstico en una frase

La app está organizada alrededor del **rastreo** —un objeto técnico— y no de lo
que se hace con ella: entrar en un cliente, ver qué está roto, qué ha cambiado
desde el último censo y sacar el informe. Las funciones se han ido añadiendo
como pestañas y botones (9 pestañas y 8 botones en la ficha de un rastreo), y
la misma pregunta se contesta en tres sitios con tres cifras distintas.

## Regla para cada cambio de esta lista

Igual que la Regla 0 de `docs/PRIORIDADES.md`: un cambio de interfaz se cierra
respondiendo **qué decisión toma quien mira la pantalla** con él. «Queda más
bonito» no cierra nada; «el error grave sale arriba en vez de en la página 412»
sí.

Y se cierra **con captura de antes y después**: `docs/ux/capturas.py` recorre
todas las vistas y guarda captura, texto visible y alto de página de cada una.

```bash
docker run --rm -v "$PWD/docs/ux":/s -v /tmp/ux-antes:/out crawler-masivo-crawler \
    python /s/capturas.py http://host.docker.internal:8000 e77ac4f6-60a5-4c8b-834c-6e2af4f98d56 lopesan
```

`frontend/**` **despliega** al mergear (filtro de `deploy.yml`): antes de
mergear, la comprobación de siempre (nada en `running`/`analyzing`, ver
CLAUDE.md «Al operar»).

---

## Hallazgos

### Rotos o engañosos

| id | qué | medido |
|---|---|---|
| UX-01 | El Resumen pinta **todas** las semillas como texto (`index.html`, `job.seeds?.join(', ')`) | con las 3.040 de Lopesan, la página mide **114.465 px** de alto |
| UX-02 | La pestaña Enlaces se queda en «0 enlaces · Cargando...»: el endpoint hace `q.count()` sobre `links` (`api/routers/results.py`, `list_links`) | **2 min 02 s** en local para la primera página. Mismo mal que #52 / decisión 37 |
| UX-03 | No hay rutas: recargar vuelve a la lista, «atrás» no funciona, no se puede enlazar un rastreo, una URL ni un filtro | `view` es solo estado de Alpine; ni `location.hash` ni `pushState` en `app.js` |
| UX-04 | Problemas es una lista plana, sin agrupar por tipo, ordenada error → **info** → aviso, con detalles crudos (`status_code: 404`) | 38.922 filas en **779 páginas** |
| UX-05 | El Informe SEO da **88 «Bueno»** a un censo con 2.367 canonicals a otro dominio marcados como `error` (el fallo de Lopesan, decisión 64) | contradice a Problemas y al Resumen |

### Navegación y estructura

| id | qué |
|---|---|
| UX-06 | La barra superior cambia según la vista: en la ficha de un rastreo no están Clientes ni Programados; en la de un cliente no está Programados ni la vuelta a Trabajos |
| UX-07 | La portada es una lista de 50 rastreos con nombre libre, sin buscador ni filtro por cliente; canarios, pruebas y censos mezclados |
| UX-08 | El cliente no es la entrada: la ficha de Lopesan enseña **1 rastreo** cuando hay ~10 (los de julio no están ligados); Druni no tiene cliente |
| UX-09 | Ficha del rastreo sobrecargada: 4 botones de exportar sueltos, «Eliminar» junto a «Explorador», 9 pestañas |
| UX-10 | La pestaña URLs duplica el Explorador, que es mejor (filtros por columna, columnas elegibles, teclado) |
| UX-11 | «Principales problemas» (Resumen), Problemas e Informe SEO responden lo mismo de tres formas |
| UX-12 | Comparar se abre vacío con un desplegable que mezcla censos con pruebas de 26 URLs, aunque `jobs.comparacion` ya trae la comparación con el anterior |
| UX-13 | Tarjetas de cifras redundantes: «URLs totales» = «Procesadas»; «Fallidas 0» en rojo |

### Detalle y consistencia

| id | qué |
|---|---|
| UX-14 | Formatos: duración `14787.1s`; números `4319` junto a `15.026` (`Intl` es-ES no agrupa 4 cifras: hace falta `useGrouping: 'always'`); fechas «hace 6d» junto a `6/10/2026, 9:00:27`; cron crudo `0 4 * * 3` |
| UX-15 | Iconos que no salen (botón Clientes, pestañas y «Nuevo rastreo» de la ficha de cliente) y `lucide@latest` sin versión fijada |
| UX-16 | Explorador: la lupa sale enorme y descolocada encima del buscador; miga de pan truncada |
| UX-17 | Ficha de URL: «General» es un volcado de 30 campos sin jerarquía; indexabilidad, problemas y enlazado no están arriba |
| UX-18 | Semántico pide API key de Gemini y tres deslizadores (alpha, beta, umbral) antes de decir qué da; términos en inglés (Ring Map, Drift, Scatter UMAP) |
| UX-19 | Nuevo rastreo: 6 pestañas técnicas y 32 concurrentes por defecto; cliente/perfil (que heredan todo) es un campo más |
| UX-20 | Textos sin tilde («Codigos», «Semantico», «Configuracion», «Analisis») junto a otros con ella («Análisis enriquecido») |
| UX-21 | Accesibilidad: 31 pestañas son `div` (sin teclado), 107 ayudas solo al pasar el ratón (`data-tip`), 11 `confirm`/`alert`/`prompt` nativos, 688 `style=""` en línea |
| UX-22 | Móvil: la barra superior desborda a 390 px (hay scroll horizontal) |

---

## Plan por fases

Cada fase es una issue del milestone M6 con su checklist. **Un PR por casilla
o por un grupo pequeño de casillas**, no uno por fase: así cada despliegue es
pequeño y una regresión se aísla sola.

### Fase 0 · Arreglos rápidos — #119

Lo que hoy rompe o miente, sin tocar la estructura.

- [ ] UX-01 Semillas plegadas en el Resumen («3.040 semillas · ver»). Criterio: la ficha de Lopesan mide menos de 3.000 px.
- [ ] UX-02 Enlaces responde en segundos: total estimado o `count` filtrando por `links.job_id` con índice; y el estado «cargando» no puede decir «0 enlaces». Medir antes y después con el mismo job.
- [ ] UX-14 Un único `fmt` para números (`useGrouping: 'always'`), duraciones («4 h 06 min»), fechas (relativa + absoluta en `title`) y cron legible («miércoles 4:00»).
- [ ] UX-15 Fijar versión de lucide y arreglar los iconos que faltan.
- [ ] UX-16 Lupa y miga de pan del Explorador.
- [ ] UX-04 (parcial) Problemas ordenado por gravedad real: error → aviso → info.

### Fase 1 · Navegación — #120

- [ ] UX-03 Rutas en el hash (`#/rastreos/:id/problemas?tipo=...`, `#/clientes/:slug`, `#/rastreos/:id/url/:url_id`) con atrás, recarga y enlaces que se pueden pegar.
- [ ] UX-06 Navegación fija e igual en todas las vistas: Rastreos · Clientes · Programados. Importar backup y el tema, a un menú.
- [ ] UX-09 «Exportar ▾» con los cuatro exports; Backup y Eliminar en «⋯».
- [ ] UX-10 Fusionar la pestaña URLs con el Explorador (el Explorador se queda).
- [ ] UX-09/11 Cinco pestañas: Resumen, Explorador, Problemas, Enlaces, Comparar. Semántico, Enriquecido y Limpieza bajo «Avanzado».
- [ ] UX-13 Tarjetas que digan algo: indexables, no indexables, errores, cambio frente al censo anterior.

### Fase 2 · Problemas como centro — #121

La pantalla que se enseña en una reunión.

- [ ] UX-04 Problemas agrupado por tipo: páginas afectadas, gravedad, qué es, qué decide Google, cómo se arregla, ejemplos; clic → Explorador filtrado. La lista plana queda como detalle.
- [ ] UX-05 La nota del Informe SEO no puede decir «Bueno» con errores graves abiertos (tope por error grave, o quitar la nota). Criterio: Lopesan no sale «Bueno».
- [ ] UX-11 Resumen, Problemas e Informe sacan las cifras de la misma consulta (decisiones 23 y 39: dos cifras que dicen medir lo mismo acaban sin coincidir).
- [ ] UX-12 Comparar abre con la comparación automática contra el censo anterior (`jobs.comparacion`): qué se arregló, qué es nuevo, qué sigue; el desplegable, para elegir otro, sin canarios ni pruebas por defecto.
- [ ] UX-17 Ficha de URL con lo importante arriba: indexabilidad y por qué, problemas, enlazado (in/out, PageRank 0-100), y «todos los campos» plegado.

### Fase 3 · El cliente como eje — #122

Depende de #36 (clientes) y comparte terreno con él.

- [ ] UX-07 Portada = panel de clientes: último censo, tendencia de errores, alertas críticas. La lista de rastreos pasa a ser una vista con buscador y filtro por cliente y estado.
- [ ] UX-07 Marcar cada rastreo como censo, canario o prueba (y filtrar por ello).
- [ ] UX-08 Asignar a un cliente los rastreos que no tienen (Druni, los Lopesan de julio).
- [ ] UX-08 Ficha de cliente con la serie de censos y sus comparaciones encadenadas.
- [ ] UX-19 Nuevo rastreo empieza por cliente/perfil y hereda; nombre + semillas visibles, «Avanzado» plegado, botón «lanzar canario».

### Fase 4 · Pulido — #123

Sin fecha; se va haciendo cuando se toca cada zona.

- [ ] UX-21 Pestañas y botones como `button` con `role="tab"`; ayudas también con foco/clic.
- [ ] UX-21 Diálogos propios en lugar de `confirm`/`alert`/`prompt`.
- [ ] UX-21 Estilos en línea a clases (al tocar cada bloque, no en un PR gigante).
- [ ] UX-20 Tildes y términos en castellano en toda la interfaz.
- [ ] UX-18 Semántico: explicar qué da antes de pedir clave; parámetros en «Avanzado».
- [ ] UX-22 Mínimo usable en móvil: barra superior sin desbordar.

---

## Estado

| fase | issue | estado |
|---|---|---|
| 0 · Arreglos rápidos | #119 | pendiente |
| 1 · Navegación | #120 | pendiente |
| 2 · Problemas como centro | #121 | pendiente |
| 3 · Cliente como eje | #122 | pendiente |
| 4 · Pulido | #123 | pendiente |

Al cerrar una casilla: marcarla aquí y en la issue, con el PR al lado.
