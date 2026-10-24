"""Topes de URLs por subdominio o carpeta (Ola 8, decision 172).

Un presupuesto aparte de `max_urls`: en un multisite (blogs.uoc.edu, con un
blog por carpeta) un `max_urls` global se lo come el primer blog que el BFS
encuentra, y los demas quedan sin ver. Con `topes_por_carpeta` cada parte tiene
su cupo y el resto del sitio sigue rastreandose.

Vive en `shared/` porque lo usan tres sitios con la MISMA regla (decisiones 23,
39 y 64: dos copias acaban discrepando): `JobConfig` para validar, el spider
para admitir o no cada URL, y el analizador para saber que URL cae en una parte
cortada y no afirmar ausencias ahi (decisiones 67 y 70).

Sintaxis de las reglas (`{"regla": tope}`):

- ``"*"``: cada carpeta de primer nivel de cada host tiene su tope
  (``/economia/``, ``/blog/``...). Las paginas sueltas de la raiz (``/``,
  ``/contacto``) no estan en ninguna carpeta y no las cuenta.
- ``"/blog/"``: el prefijo de ruta, en cada host por separado.
- ``"blogs.uoc.edu"`` o ``"blogs.uoc.edu/*"``: el host entero.
- ``"blogs.uoc.edu/blog/"``: el prefijo, solo en ese host.
- ``"blogs.uoc.edu/*/"``: cada carpeta de primer nivel de ese host.

Una URL tiene como mucho DOS cupos: el de su host (si hay regla de host) y el
de su ruta, que es la regla de ruta mas concreta que casa: un prefijo antes que
una carpeta comodin; entre prefijos, el mas largo; a igual longitud, el que
nombra el host; entre comodines, el del host antes que ``"*"``. Asi
``{"/blog/": 500, "*": 300}`` da 500 al blog y 300 a cada otra carpeta, y
``{"blogs.uoc.edu/*": 2000, "*": 300}`` acota cada blog a 300 y el host a 2000.

Sin dependencias de Scrapy ni de Pydantic: la API, el worker y el analizador lo
importan igual.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

# Cuantas reglas como mucho: la lista se evalua por URL descubierta.
MAX_REGLAS = 200
TOPE_MAX = 5_000_000
# Grupos cortados que se guardan con su detalle en `jobs.topes_carpeta`. Con
# `"*"` en un sitio de miles de carpetas la lista no cabe; pasado esto, la
# regla entera cuenta como cortada para el analisis (no se sabe cual no lo
# esta, y lo que no se sabe no se afirma).
MAX_GRUPOS_GUARDADOS = 2000
# URLs distintas que se recuerdan como dejadas sin rastrear. Pasado esto la
# cifra sigue subiendo, pero puede contar dos veces la misma: es un minimo y
# se dice (`recuento_aproximado`).
MAX_RECORDADAS = 2_000_000

# Motivo de cierre de un rastreo que agoto la frontera con algun tope cortado.
# Entra en `shared.comparacion.MOTIVOS_TRUNCADO`: el censo no esta entero.
MOTIVO = "tope_por_carpeta"

_SEGURO_EN_RUTA = "/%:@!$&'()*+,;=-._~"


@dataclass(frozen=True)
class Regla:
    clave: str          # la regla tal y como se escribio (normalizada)
    tope: int
    tipo: str           # "host" | "prefijo" | "carpeta"
    host: str | None    # None = cualquier host
    prefijo: str = ""   # solo "prefijo"


def _sin_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def _host_normal(host: str) -> str:
    host = (host or "").strip().lower().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return _sin_www(host)


def interpretar(clave: str, tope) -> Regla:
    """Una regla a partir de su clave. ValueError con el motivo si no vale."""
    if isinstance(tope, bool) or not isinstance(tope, int):
        raise ValueError(f"{clave!r}: el tope tiene que ser un entero")
    if tope < 1 or tope > TOPE_MAX:
        raise ValueError(f"{clave!r}: el tope va de 1 a {TOPE_MAX}")
    texto = (clave or "").strip()
    if not texto:
        raise ValueError("regla vacia")
    if "://" in texto:
        texto = texto.split("://", 1)[1]
    if texto == "*":
        return Regla("*", tope, "carpeta", None)
    if texto.startswith("/"):
        host, ruta = None, texto
    else:
        host, _, resto = texto.partition("/")
        ruta = "/" + resto if resto else ""
        host = _host_normal(host)
        # Sin punto, casi siempre es una carpeta escrita sin la barra
        # (`blog/`): se dice, en vez de tomarla por un host que no existe.
        if not host or "*" in host or " " in host or ("." not in host and host != "localhost"):
            raise ValueError(f"{clave!r}: no es un host (p. ej. blogs.uoc.edu/*)")
    nombre = (host or "") + ruta
    if host is not None and ruta in ("", "/", "/*"):
        return Regla(host + "/*", tope, "host", host)
    if ruta == "/*/":
        return Regla(nombre, tope, "carpeta", host)
    if ruta.endswith("*"):
        ruta = ruta[:-1]
    if "*" in ruta:
        raise ValueError(f"{clave!r}: el comodin solo vale como regla entera (*), "
                         "como host/* o como host/*/")
    if ruta in ("", "/"):
        raise ValueError(f"{clave!r}: un prefijo de ruta necesita una carpeta (p. ej. /blog/)")
    ruta = quote(ruta, safe=_SEGURO_EN_RUTA)
    return Regla((host or "") + ruta, tope, "prefijo", host, ruta)


def compilar(reglas: dict | None) -> list[Regla]:
    """Las reglas validadas. ValueError con todos los motivos juntos."""
    if not reglas:
        return []
    if not isinstance(reglas, dict):
        raise ValueError("topes_por_carpeta es un objeto {regla: tope}")
    if len(reglas) > MAX_REGLAS:
        raise ValueError(f"como mucho {MAX_REGLAS} topes por carpeta")
    salida: list[Regla] = []
    errores: list[str] = []
    vistas: dict[str, str] = {}
    for clave, tope in reglas.items():
        if tope is None:      # `null` de la herencia que no llego a consumirse
            continue
        try:
            r = interpretar(str(clave), tope)
        except ValueError as exc:
            errores.append(str(exc))
            continue
        if r.clave in vistas:
            errores.append(f"{clave!r} y {vistas[r.clave]!r} son la misma regla")
            continue
        vistas[r.clave] = str(clave)
        salida.append(r)
    if errores:
        raise ValueError("; ".join(errores))
    return salida


def _host_y_ruta(url: str) -> tuple[str, str]:
    try:
        p = urlsplit(url)
        return _host_normal(p.hostname or ""), p.path or "/"
    except ValueError:
        return "", "/"


def _casa_prefijo(ruta: str, prefijo: str) -> bool:
    # `/blog` es la portada de `/blog/`: entra en su cupo.
    return ruta.startswith(prefijo) or (prefijo.endswith("/") and ruta == prefijo[:-1])


def grupos_de(url: str, reglas: list[Regla]) -> list[tuple[Regla, str]]:
    """Los cupos de *url*: `[(regla, grupo)]`, el de ruta primero y el de host
    despues, cada uno solo si alguna regla casa. `grupo` es el nombre del cupo
    concreto (``blogs.uoc.edu/economia/``): una regla comodin tiene uno por
    carpeta y por host."""
    if not reglas:
        return []
    host, ruta = _host_y_ruta(url)
    if not host:
        return []
    de_ruta: Regla | None = None
    mejor = (-1, -1)
    for r in reglas:
        if r.tipo != "prefijo" or (r.host is not None and r.host != host):
            continue
        if _casa_prefijo(ruta, r.prefijo):
            orden = (len(r.prefijo), 1 if r.host else 0)
            if orden > mejor:
                mejor, de_ruta = orden, r
    grupo_ruta = None
    if de_ruta is not None:
        grupo_ruta = host + de_ruta.prefijo
    else:
        partes = ruta.split("/")
        # "/economia/x" -> ["", "economia", "x"]; "/contacto" no es carpeta.
        if len(partes) >= 3 and partes[1]:
            for r in sorted((r for r in reglas if r.tipo == "carpeta"
                             and (r.host is None or r.host == host)),
                            key=lambda r: 0 if r.host else 1):
                de_ruta = r
                grupo_ruta = f"{host}/{partes[1]}/"
                break
    salida: list[tuple[Regla, str]] = []
    if de_ruta is not None:
        salida.append((de_ruta, grupo_ruta))
    for r in reglas:
        if r.tipo == "host" and r.host == host:
            salida.append((r, host + "/*"))
            break
    return salida


@dataclass
class Presupuesto:
    """Lo que el spider lleva de cada cupo durante el rastreo."""

    reglas: list[Regla]
    admitidas: dict[str, int] = field(default_factory=dict)       # grupo -> URLs
    sin_rastrear: dict[str, int] = field(default_factory=dict)    # grupo -> URLs
    regla_de: dict[str, Regla] = field(default_factory=dict)      # grupo -> regla
    _admitidas_claves: set[int] = field(default_factory=set)
    _dejadas_claves: set[int] = field(default_factory=set)
    recuento_aproximado: bool = False

    def contar(self, url: str, clave: int) -> None:
        """Una URL que ya esta en el censo (semilla, o rastreada en una
        ejecucion anterior del job): gasta cupo, sin preguntar."""
        if clave in self._admitidas_claves:
            return
        grupos = grupos_de(url, self.reglas)
        if not grupos:
            return
        self._admitidas_claves.add(clave)
        for r, g in grupos:
            self.regla_de[g] = r
            self.admitidas[g] = self.admitidas.get(g, 0) + 1

    def admitir(self, url: str, clave: int) -> bool:
        """True si *url* cabe (y gasta cupo); False si un tope la deja fuera,
        y entonces cuenta una vez como dejada sin rastrear en el primer cupo
        lleno (el de ruta antes que el de host)."""
        if clave in self._admitidas_claves:
            return True     # ya pedida: el dupefilter decide, sin gastar dos veces
        grupos = grupos_de(url, self.reglas)
        if not grupos:
            return True
        for r, g in grupos:
            if self.admitidas.get(g, 0) >= r.tope:
                self.regla_de[g] = r
                if clave not in self._dejadas_claves:
                    if len(self._dejadas_claves) < MAX_RECORDADAS:
                        self._dejadas_claves.add(clave)
                    else:
                        # Sin memoria para mas: se cuenta igual y la cifra
                        # puede repetir alguna URL (`recuento_aproximado`).
                        self.recuento_aproximado = True
                    self.sin_rastrear[g] = self.sin_rastrear.get(g, 0) + 1
                return False
        self._admitidas_claves.add(clave)
        for r, g in grupos:
            self.regla_de[g] = r
            self.admitidas[g] = self.admitidas.get(g, 0) + 1
        return True

    @property
    def cortado(self) -> bool:
        return any(self.sin_rastrear.values())

    def resumen(self) -> dict:
        """Lo que va a `jobs.topes_carpeta`. `sin_rastrear` es lo descubierto
        y no pedido por el tope: lo que nunca se llego a descubrir (enlazado
        solo desde paginas no rastreadas) no esta, asi que es un minimo."""
        por_regla: dict[str, dict] = {}
        for r in self.reglas:
            por_regla[r.clave] = {"regla": r.clave, "tope": r.tope, "grupos": 0,
                                  "grupos_cortados": 0, "rastreadas": 0,
                                  "sin_rastrear": 0, "lista_completa": True}
        cortados = []
        for g, r in self.regla_de.items():
            fila = por_regla[r.clave]
            fila["grupos"] += 1
            fila["rastreadas"] += self.admitidas.get(g, 0)
            n = self.sin_rastrear.get(g, 0)
            if n:
                fila["grupos_cortados"] += 1
                fila["sin_rastrear"] += n
                cortados.append({"regla": r.clave, "grupo": g, "tope": r.tope,
                                 "rastreadas": self.admitidas.get(g, 0), "sin_rastrear": n})
        cortados.sort(key=lambda c: (-c["sin_rastrear"], c["grupo"]))
        if len(cortados) > MAX_GRUPOS_GUARDADOS:
            for c in cortados[MAX_GRUPOS_GUARDADOS:]:
                por_regla[c["regla"]]["lista_completa"] = False
            cortados = cortados[:MAX_GRUPOS_GUARDADOS]
        return {
            "version": 1,
            "reglas": list(por_regla.values()),
            "cortados": cortados,
            "sin_rastrear": sum(self.sin_rastrear.values()),
            "sin_rastrear_minimo": True,
            "recuento_aproximado": self.recuento_aproximado,
        }


class PartesCortadas:
    """Para el analisis: si una URL cae en un cupo que se quedo corto.

    Se reconstruye desde `jobs.topes_carpeta` y `jobs.config`: el grupo de cada
    URL sale de `grupos_de` con las mismas reglas que uso el spider. Si la
    lista de grupos cortados de una regla se recorto al guardarla, todo lo que
    casa con esa regla cuenta como cortado.
    """

    def __init__(self, resumen: dict | None, config: dict | None):
        self.grupos: set[str] = set()
        self.reglas_enteras: set[str] = set()
        self.reglas: list[Regla] = []
        if not isinstance(resumen, dict) or not resumen.get("sin_rastrear"):
            return
        try:
            self.reglas = compilar((config or {}).get("topes_por_carpeta"))
        except ValueError:
            self.reglas = []
        for c in resumen.get("cortados") or []:
            if isinstance(c, dict) and c.get("grupo"):
                self.grupos.add(c["grupo"])
        for f in resumen.get("reglas") or []:
            if isinstance(f, dict) and f.get("grupos_cortados") and not f.get("lista_completa", True):
                self.reglas_enteras.add(f.get("regla"))
        if not self.reglas:
            # Sin las reglas no se sabe que URL cae donde: todo cuenta como
            # cortado (lo que no se sabe no se afirma).
            self.reglas_enteras.add("*todo*")

    def __bool__(self) -> bool:
        return bool(self.grupos or self.reglas_enteras)

    def contiene(self, url: str) -> bool:
        if not self:
            return False
        if "*todo*" in self.reglas_enteras:
            return True
        for r, g in grupos_de(url, self.reglas):
            if g in self.grupos or r.clave in self.reglas_enteras:
                return True
        return False


def describir(resumen: dict | None, max_grupos: int = 5) -> str | None:
    """Una frase para el informe y la ficha, o None si ningun tope corto."""
    if not isinstance(resumen, dict) or not resumen.get("sin_rastrear"):
        return None
    cortados = resumen.get("cortados") or []
    n = sum(int(f.get("grupos_cortados") or 0) for f in resumen.get("reglas") or [])
    ejemplos = ", ".join(c["grupo"] for c in cortados[:max_grupos] if isinstance(c, dict))
    total = f"{int(resumen['sin_rastrear']):,}".replace(",", ".")
    return (f"Los topes por carpeta dejaron sin rastrear al menos {total} URLs "
            f"en {n} {'parte' if n == 1 else 'partes'} del sitio ({ejemplos}"
            f"{', …' if n > max_grupos else ''})")
