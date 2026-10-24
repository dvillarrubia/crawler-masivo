# axe-core vendorizado (decision 174)

La sonda de accesibilidad (`seo_crawler/sonda_axe.py`) inyecta axe-core en las
páginas de la muestra. Va aquí, en el repo, para que el rastreo no descargue
nada de un CDN.

- **Versión**: axe-core 4.14.0 (publicada en npm el 2026-10-05).
- **Origen**: `https://registry.npmjs.org/axe-core/-/axe-core-4.14.0.tgz`,
  integridad npm
  `sha512-9WTZxEjsZ7b13TH8JPmbV2z8CHbl80/2hm3XPEG4JgNdQLK81IBRXmSxHfMAOkSqQeRxT/0dwNDz2GOm3zzpcQ==`
  (comprobada al descargar).
- **Ficheros**, sin modificar:
  - `axe.min.js` (sha256 `20c09fe157a8a34a30e241aaa1fcdade657734f08ab379ecfbeb7d45cc46e878`)
  - `es.json` = `locales/es.json` del paquete (sha256
    `c25e489d1883af7c83df95a0acebd599c3b841e36eb0224c61bebc6635f11e2b`): los
    textos de ayuda en castellano; las reglas sin traducir salen en inglés.
  - `LICENSE` (Mozilla Public License 2.0) y `LICENSE-3RD-PARTY.txt` (MIT de
    sus dependencias empaquetadas).
- **Licencia**: MPL-2.0. Se redistribuye sin cambios y con su licencia; el
  código fuente está en <https://github.com/dequelabs/axe-core>.

Para subir de versión: descargar el `.tgz` de npm, comprobar su integridad
contra `npm view axe-core@<v> dist.integrity`, copiar los cuatro ficheros,
actualizar este README (versión y sumas) y pasar `tests/test_accesibilidad.py`
y `tests/test_sonda_axe_spider.py`. Una versión nueva puede añadir reglas o
cambiar impactos: los avisos de dos censos con versiones distintas de axe no
son comparables regla a regla (se guarda la versión en cada captura).
