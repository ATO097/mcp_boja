"""Servidor MCP para la normativa de la Junta de Andalucía publicada en el BOJA.

API pública: https://datos.juntadeandalucia.es/api/v0/regulations/
Dataset: https://www.juntadeandalucia.es/datosabiertos/portal/dataset/normativa-de-la-junta-de-andalucia

La búsqueda de la API distingue mayúsculas y tildes y devuelve 400 cuando no hay
resultados, así que las búsquedas se hacen sobre el catálogo completo (unos 3 MB,
actualizado a diario), descargado y guardado en memoria. Si no se puede descargar,
se recurre a la búsqueda de la API.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
import unicodedata
from typing import Any, Literal
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pypdf import PdfReader

API_BASE = "https://datos.juntadeandalucia.es/api/v0/regulations"
USER_AGENT = "mcp-boja/0.1"
TIMEOUT = httpx.Timeout(60.0, connect=15.0)
CACHE_TTL = 3 * 3600  # el catálogo se regenera una vez al día
MAX_PDF_BYTES = 30 * 1024 * 1024

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("pypdf").setLevel(logging.ERROR)
log = logging.getLogger("mcp_boja")

# Tipo de filtro -> (parámetro de la API, endpoint de option-values)
FILTROS = {
    "temas": ("themes", "themes_names"),
    "subtemas": ("subtopic", "subtopics_names"),
    "organismos": ("organism", "organisms_names"),
    "suborganismos": ("suborganism", "suborganisms_names"),
    "rangos": ("class_regulations", "class_regulations_names"),
}
ORDEN_API = {
    "fecha_publicacion": "publication_date",
    "fecha_actualizacion": "update_date",
    "titulo": "title",
    "rango": "class_regulations",
    "organismo": "organism",
    "id": "id",
}
Orden = Literal["fecha_publicacion", "fecha_actualizacion", "titulo", "rango", "organismo", "id"]

mcp = MCPServer(
    "normativa-andalucia",
    title="Normativa de la Junta de Andalucía (BOJA)",
    version="0.1.0",
    instructions=(
        "Consulta la normativa aprobada por la Junta de Andalucía y publicada en el BOJA "
        "(leyes, decretos, órdenes, resoluciones...). Usa buscar_normativa para localizar "
        "normas (palabras del título, rango, organismo, tema, fechas), obtener_norma para "
        "la ficha de una norma por su id y leer_texto_boja para leer su texto íntegro. "
        "listar_valores_filtro muestra los rangos, organismos y temas existentes. "
        "Cita siempre el enlace al BOJA y recuerda que la versión auténtica es el PDF oficial."
    ),
)


class ErrorBOJA(ToolError):
    """Error mostrado al modelo con un mensaje claro."""


# --------------------------------------------------------------------------- utilidades


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )


async def _api(path: str, params: dict[str, Any] | None = None) -> Any:
    async with _http() as c:
        try:
            r = await c.get(f"{API_BASE}/{path}", params=params)
        except httpx.HTTPError as e:
            raise ErrorBOJA(f"No se pudo conectar con la API de la Junta: {e}") from e
    if r.status_code == 422:
        raise ErrorBOJA(f"Parámetros rechazados por la API: {r.text[:500]}")
    if r.status_code >= 400:
        raise ErrorBOJA(f"La API respondió {r.status_code}: {r.text[:300]}")
    return r.json()


def _norm(s: str | None) -> str:
    """Minúsculas, sin tildes y con espacios simples, para comparar textos."""
    s = unicodedata.normalize("NFKD", (s or "").casefold())
    return re.sub(r"\s+", " ", "".join(ch for ch in s if not unicodedata.combining(ch))).strip()


def _lista(items: Any, clave: str) -> list[str]:
    if not isinstance(items, list):
        return []
    vistos: list[str] = []
    for i in items:
        v = i.get(clave) if isinstance(i, dict) else None
        if v and v not in vistos:
            vistos.append(v)
    return vistos


def _fecha_iso(f: str | None) -> str | None:
    """La API devuelve dd/mm/aaaa y el catálogo aaaa-mm-dd; se unifica a aaaa-mm-dd."""
    if not f:
        return None
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", f)
    return f"{m[3]}-{m[2]}-{m[1]}" if m else f[:10]


def _temas(r: dict[str, Any]) -> tuple[list[str], list[str]]:
    temas: list[str] = []
    subtemas: list[str] = []
    for t in r.get("themes") or []:
        if not isinstance(t, dict):
            continue
        for p in _lista(t.get("parent"), "themes"):
            if p not in temas:
                temas.append(p)
        if t.get("subtopic") and t["subtopic"] not in subtemas:
            subtemas.append(t["subtopic"])
    return temas, subtemas


def _ficha(r: dict[str, Any]) -> dict[str, Any]:
    """Aplana un registro (de la API o del catálogo) a un formato compacto."""
    docs = _lista(r.get("document"), "doc_link")
    temas, subtemas = _temas(r)
    ficha = {
        "id": r.get("id"),
        "titulo": (r.get("title") or "").strip(),
        "rango": ", ".join(_lista(r.get("range"), "class_regulations")),
        "fecha_publicacion": _fecha_iso(r.get("publication_date")),
        "organismo": ", ".join(_lista(r.get("organism"), "organism")),
        "suborganismo": ", ".join(_lista(r.get("suborganism"), "suborganism")),
        "ambito": r.get("scope"),
        "temas": temas,
        "subtemas": subtemas,
        "enlace": docs[0] if docs else None,
        "otros_enlaces": docs[1:],
        "fecha_actualizacion": _fecha_iso(r.get("update_date")),
    }
    return {k: v for k, v in ficha.items() if v not in (None, "", [])}


# --------------------------------------------------------------------------- caché


class _Catalogo:
    def __init__(self) -> None:
        self.fichas: list[dict[str, Any]] = []
        self.por_id: dict[str, dict[str, Any]] = {}
        self.cargado = 0.0
        self.lock = asyncio.Lock()

    async def obtener(self) -> list[dict[str, Any]]:
        async with self.lock:
            if self.fichas and time.time() - self.cargado < CACHE_TTL:
                return self.fichas
            try:
                datos = await _api("all", {"format": "json"})
                if not isinstance(datos, list) or not datos:
                    raise ErrorBOJA("El catálogo descargado está vacío o tiene otro formato.")
            except Exception:
                if self.fichas:  # mejor datos de hace unas horas que nada
                    log.warning("No se pudo refrescar el catálogo; se usa la copia anterior")
                    return self.fichas
                raise
            fichas = []
            for r in datos:
                f = _ficha(r)
                f["_titulo"] = _norm(f.get("titulo"))
                f["_rango"] = [_norm(x) for x in _lista(r.get("range"), "class_regulations")]
                f["_org"] = _norm(f.get("organismo"))
                f["_suborg"] = _norm(f.get("suborganismo"))
                f["_temas"] = _norm(" | ".join(f.get("temas", [])))
                f["_subtemas"] = _norm(" | ".join(f.get("subtemas", [])))
                fichas.append(f)
            self.fichas = fichas
            self.por_id = {f["id"]: f for f in fichas}
            self.cargado = time.time()
            return fichas


_catalogo = _Catalogo()
_valores_cache: dict[str, tuple[float, list[str]]] = {}


async def _valores(tipo: str) -> list[str]:
    hit = _valores_cache.get(tipo)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    data = await _api(f"option-values/{FILTROS[tipo][1]}")
    vals = [v for v in data.get("result", []) if v and v != "-"]
    _valores_cache[tipo] = (time.time(), vals)
    return vals


def _publica(f: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in f.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- búsqueda


def _clave(f: dict[str, Any], orden: str) -> Any:
    if orden == "id":
        return int(f.get("id") or 0)
    campo = {
        "fecha_publicacion": "fecha_publicacion",
        "fecha_actualizacion": "fecha_actualizacion",
        "titulo": "_titulo",
        "rango": "rango",
        "organismo": "_org",
    }[orden]
    return _norm(str(f.get(campo) or "")) if campo in ("rango",) else str(f.get(campo) or "")


def _raiz(palabra: str) -> str:
    """Raíz aproximada para ignorar género y número: turística/turísticos -> turistic."""
    if len(palabra) < 5 or any(c.isdigit() for c in palabra):
        return palabra
    for fin in ("es", "s"):
        if palabra.endswith(fin) and len(palabra) - len(fin) >= 3:
            palabra = palabra[: -len(fin)]
            break
    return palabra[:-1] if palabra[-1] in "aeo" and len(palabra) > 4 else palabra


def _buscar_local(
    fichas: list[dict[str, Any]],
    texto: str,
    rango: str,
    organismo: str,
    suborganismo: str,
    tema: str,
    subtema: str,
    desde_fecha: str,
    hasta_fecha: str,
) -> list[dict[str, Any]]:
    # "3/2024" no debe coincidir con "13/2024": las cifras no pueden ir precedidas de otra cifra
    patrones = [
        re.compile((r"(?<!\d)" if p[0].isdigit() else "") + re.escape(_raiz(p)))
        for p in re.findall(r"[\w/.-]+", _norm(texto))
    ]
    rq = _norm(rango)
    # "ley" debe dar solo leyes, no "Ley Orgánica"; si no hay rango exacto, se busca parcial
    rango_exacto = rq and any(rq in f["_rango"] for f in fichas)
    filtros = [
        ("_org", _norm(organismo)),
        ("_suborg", _norm(suborganismo)),
        ("_temas", _norm(tema)),
        ("_subtemas", _norm(subtema)),
    ]
    res = []
    for f in fichas:
        if patrones and not all(p.search(f["_titulo"]) for p in patrones):
            continue
        if rq:
            if rango_exacto and rq not in f["_rango"]:
                continue
            if not rango_exacto and not any(rq in x for x in f["_rango"]):
                continue
        if any(q and q not in f[campo] for campo, q in filtros):
            continue
        fp = f.get("fecha_publicacion") or ""
        if desde_fecha and (not fp or fp < desde_fecha):
            continue
        if hasta_fecha and (not fp or fp > hasta_fecha):
            continue
        res.append(f)
    return res


async def _buscar_api(
    texto: str, rango: str, organismo: str, suborganismo: str, tema: str, subtema: str,
    orden: str, ascendente: bool, limite: int,
) -> dict[str, Any]:
    """Respaldo: búsqueda directa en la API (sensible a mayúsculas y tildes)."""

    async def exacto(tipo: str, valor: str) -> str:
        if not valor.strip():
            return "-"
        q = _norm(valor)
        vals = await _valores(tipo)
        cand = [v for v in vals if _norm(v) == q] or [v for v in vals if q in _norm(v)]
        if not cand:
            raise ErrorBOJA(f"'{valor}' no coincide con ningún valor de {tipo}.")
        return cand[0]

    params = {
        "title": texto.strip() or "-",
        "class_regulations": await exacto("rangos", rango),
        "organism": await exacto("organismos", organismo),
        "suborganism": await exacto("suborganismos", suborganismo),
        "themes": await exacto("temas", tema),
        "subtopic": await exacto("subtemas", subtema),
        "order_by": ORDEN_API[orden],
        "mode": "ASC" if ascendente else "DESC",
        "size": limite,
        "format": "json",
    }
    try:
        data = await _api("search", params)
    except ErrorBOJA as e:
        if "400" in str(e):  # la API responde 400 cuando no hay resultados
            data = {"total_hits": 0, "results": []}
        else:
            raise
    resultados = [_ficha(r) for r in data.get("results", [])][:limite]
    return {
        "total_encontradas": data.get("total_hits", len(resultados)),
        "mostradas": len(resultados),
        "aviso": "Catálogo no disponible: búsqueda directa en la API, que distingue "
        "mayúsculas y tildes en el título.",
        "resultados": resultados,
    }


# --------------------------------------------------------------------------- herramientas


@mcp.tool()
async def buscar_normativa(
    texto: str = "",
    rango: str = "",
    organismo: str = "",
    suborganismo: str = "",
    tema: str = "",
    subtema: str = "",
    desde_fecha: str = "",
    hasta_fecha: str = "",
    orden: Orden = "fecha_publicacion",
    ascendente: bool = False,
    limite: int = 20,
    saltar: int = 0,
) -> dict[str, Any]:
    """Busca normas de la Junta de Andalucía publicadas en el BOJA.

    Ningún filtro distingue mayúsculas ni tildes, y todos admiten parte del nombre.
    Sin filtros devuelve las normas más recientes.

    Args:
        texto: palabras que deben aparecer todas en el título, en cualquier orden y sin
            importar género o número (p. ej. "vivienda turistica").
        rango: tipo de norma: "Ley", "Decreto", "Decreto-ley", "Orden", "Resolución",
            "Acuerdo", "Corrección de errores"...
        organismo: consejería u organismo (p. ej. "Hacienda", "Servicio Andaluz de Salud").
        suborganismo: órgano dependiente (p. ej. "Dirección General de Presupuestos").
        tema: tema (p. ej. "Medio ambiente").
        subtema: subtema (p. ej. "Becas").
        desde_fecha: fecha de publicación mínima, formato AAAA-MM-DD.
        hasta_fecha: fecha de publicación máxima, formato AAAA-MM-DD.
        orden: campo de ordenación.
        ascendente: True para orden ascendente (por defecto, lo más reciente primero).
        limite: número de resultados a devolver (1-100).
        saltar: resultados a omitir, para paginar (usa el valor siguiente_saltar).
    """
    limite = max(1, min(int(limite), 100))
    saltar = max(0, int(saltar))
    for nombre, f in (("desde_fecha", desde_fecha), ("hasta_fecha", hasta_fecha)):
        if f and not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", f):
            raise ErrorBOJA(f"{nombre} debe tener formato AAAA-MM-DD (o AAAA, AAAA-MM).")
    # un año o mes suelto cubre el periodo completo
    hasta = hasta_fecha + ("-12-31" if len(hasta_fecha) == 4 else "-31" if len(hasta_fecha) == 7 else "")

    try:
        fichas = await _catalogo.obtener()
    except Exception as e:
        log.warning("Catálogo no disponible (%s); se usa la API de búsqueda", e)
        if desde_fecha or hasta_fecha or saltar:
            raise ErrorBOJA(
                "El catálogo no está disponible ahora y la API no permite filtrar por "
                "fechas ni paginar. Inténtalo de nuevo en unos minutos."
            ) from e
        return await _buscar_api(
            texto, rango, organismo, suborganismo, tema, subtema, orden, ascendente, limite
        )

    res = _buscar_local(
        fichas, texto, rango, organismo, suborganismo, tema, subtema, desde_fecha, hasta
    )
    res.sort(key=lambda f: _clave(f, orden), reverse=not ascendente)
    frase = _norm(texto)
    if frase:  # relevancia: primero los títulos que empiezan por la frase o la contienen
        res.sort(key=lambda f: 0 if f["_titulo"].startswith(frase) else 1 if frase in f["_titulo"] else 2)
    pagina = res[saltar : saltar + limite]
    resp: dict[str, Any] = {
        "total_encontradas": len(res),
        "mostradas": len(pagina),
        "resultados": [_publica(f) for f in pagina],
    }
    if saltar + limite < len(res):
        resp["siguiente_saltar"] = saltar + limite
    if not res:
        resp["sugerencia"] = (
            "Sin resultados. Prueba con menos palabras o filtros, o consulta los valores "
            "válidos con listar_valores_filtro."
        )
    return resp


@mcp.tool()
async def obtener_norma(id: str) -> dict[str, Any]:
    """Devuelve la ficha completa de una norma a partir de su identificador (campo "id")."""
    id = str(id).strip()
    if not id.isdigit():
        raise ErrorBOJA("El id debe ser numérico (el campo 'id' de buscar_normativa).")
    try:
        data = await _api(id)
        res = data.get("results") or []
        if res:
            return _ficha(res[0])
    except ErrorBOJA as e:
        if "400" not in str(e):
            # API caída: se intenta con el catálogo
            if id in _catalogo.por_id:
                return _publica(_catalogo.por_id[id])
            raise
    raise ErrorBOJA(f"No existe ninguna norma con id {id}.")


@mcp.tool()
async def listar_valores_filtro(
    tipo: Literal["temas", "subtemas", "organismos", "suborganismos", "rangos"],
    contiene: str = "",
) -> dict[str, Any]:
    """Lista los valores existentes de un filtro de búsqueda.

    Args:
        tipo: filtro a consultar.
        contiene: texto opcional para filtrar la lista (sin distinguir mayúsculas ni tildes).
    """
    vals = await _valores(tipo)
    if contiene.strip():
        q = _norm(contiene)
        vals = [v for v in vals if q in _norm(v)]
    return {"tipo": tipo, "total": len(vals), "valores": vals}


@mcp.tool()
async def contar_normas() -> dict[str, Any]:
    """Número total de normas disponibles en el catálogo."""
    data = await _api("count")
    return {"total": data.get("count", {}).get("result")}


def _texto_disposicion(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    cuerpo = soup.select_one("#cuerpo") or soup.find("main") or soup.body or soup
    for tag in cuerpo.select("script, style, .boja_nav, .alerta, nav"):
        tag.decompose()

    pdf = None
    a = cuerpo.select_one("a.item_pdf_disposicion") or cuerpo.find(
        "a", href=re.compile(r"\.pdf($|\?)", re.I)
    )
    if a and a.get("href"):
        pdf = urljoin(url, a["href"])

    cab = cuerpo.select_one(".cabecera_detalle_disposicion")
    titulo = None
    if cab:
        p = cab.select_one(".item p") or cab.find("p")
        titulo = p.get_text(" ", strip=True) if p else None
    h1 = cuerpo.find("h1")
    boletin = re.sub(r"\s+", " ", h1.get_text(" ", strip=True)) if h1 else None

    lineas: list[str] = []
    for el in cuerpo.find_all(["h2", "h3", "h4", "h5", "p", "li", "tr"]):
        if el.find_parent(["p", "li", "tr"]):
            continue
        if el.name == "tr":
            celdas = [c.get_text(" ", strip=True) for c in el.find_all(["td", "th"])]
            t = " | ".join(c for c in celdas if c)
        else:
            t = el.get_text(" ", strip=True)
        if t and t != "Descargar PDF":
            lineas.append(t)
    texto = "\n\n".join(lineas)
    return {"boletin": boletin, "titulo": titulo, "pdf_oficial": pdf, "texto": texto}


@mcp.tool()
async def leer_texto_boja(
    enlace_o_id: str, desde: int = 0, max_caracteres: int = 20000
) -> dict[str, Any]:
    """Lee el texto íntegro de una disposición publicada en el BOJA.

    El texto procede de la web del BOJA y puede omitir imágenes o tablas; la versión
    oficial y auténtica es el PDF firmado (campo pdf_oficial).

    Args:
        enlace_o_id: id de la norma (de buscar_normativa) o URL del BOJA.
        desde: posición del carácter desde el que leer (para paginar textos largos).
        max_caracteres: tamaño máximo del fragmento devuelto (1000-100000).
    """
    ref = enlace_o_id.strip()
    if ref.isdigit():
        url = (await obtener_norma(ref)).get("enlace")
        if not url:
            raise ErrorBOJA(f"La norma {ref} no tiene enlace al documento.")
    elif ref.startswith(("http://", "https://")):
        url = ref
    else:
        raise ErrorBOJA("Indica el id numérico de la norma o una URL completa.")
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]

    async with _http() as c:
        try:
            r = await c.get(url)
        except httpx.HTTPError as e:
            raise ErrorBOJA(f"No se pudo descargar {url}: {e}") from e
    if r.status_code >= 400:
        raise ErrorBOJA(f"{url} respondió {r.status_code}")
    tipo = r.headers.get("content-type", "")
    if "pdf" in tipo or str(r.url).lower().endswith(".pdf"):
        if len(r.content) > MAX_PDF_BYTES:
            return {"url": str(r.url), "aviso": "PDF demasiado grande para extraer su texto."}
        try:
            lector = PdfReader(io.BytesIO(r.content))
            texto = "\n\n".join((pag.extract_text() or "").strip() for pag in lector.pages)
        except Exception as e:
            raise ErrorBOJA(f"No se pudo leer el PDF {r.url}: {e}") from e
        datos = {"pdf_oficial": str(r.url), "paginas": len(lector.pages)}
    elif "html" in tipo:
        datos = _texto_disposicion(r.text, str(r.url))
        texto = datos.pop("texto")
    else:
        return {
            "url": str(r.url),
            "aviso": f"Formato no soportado ({tipo}); ábrelo directamente.",
        }
    if len(texto.strip()) < 500:
        datos["aviso"] = (
            "Se ha obtenido poco texto: el enlace puede ser una ficha o un buscador "
            "y no la disposición. Ábrelo en el navegador o usa pdf_oficial si existe."
        )

    max_caracteres = max(1000, min(int(max_caracteres), 100000))
    desde = max(0, int(desde))
    fragmento = texto[desde : desde + max_caracteres]
    fin = desde + len(fragmento)
    resp = {"url": str(r.url), **{k: v for k, v in datos.items() if v}}
    resp.update(
        longitud_total=len(texto),
        desde=desde,
        hasta=fin,
        texto=fragmento,
    )
    if fin < len(texto):
        resp["siguiente_desde"] = fin
    return resp


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
