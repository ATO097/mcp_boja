# MCP-BOJA · Normativa de la Junta de Andalucía

Servidor [MCP](https://modelcontextprotocol.io) para que Claude (Desktop o Code) y otros
clientes MCP consulten la **normativa de la Junta de Andalucía publicada en el BOJA**:
leyes, decretos, decretos-ley, órdenes, resoluciones, etc.

Usa los datos abiertos de la Junta
([dataset «Normativa de la Junta de Andalucía»](https://www.juntadeandalucia.es/datosabiertos/portal/dataset/normativa-de-la-junta-de-andalucia),
licencia CC BY 4.0). No necesita clave ni registro.

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `buscar_normativa` | Busca por palabras del título, rango, organismo, suborganismo, tema, subtema y rango de fechas. Sin distinguir mayúsculas ni tildes, con paginación. |
| `obtener_norma` | Ficha completa de una norma por su `id`. |
| `leer_texto_boja` | Texto íntegro de la disposición (página del BOJA o PDF), paginado, con el enlace al PDF oficial. |
| `listar_valores_filtro` | Rangos, organismos, suborganismos, temas y subtemas existentes. |
| `contar_normas` | Número total de normas del catálogo. |

Ejemplos de preguntas a Claude:

- «¿Qué decretos sobre viviendas turísticas ha aprobado la Junta?»
- «Lista las normas publicadas en el BOJA en octubre de 2026.»
- «Resume la Ley 13/2011 del Turismo de Andalucía.»
- «¿Qué órdenes ha publicado la Consejería de Hacienda este año?»

## Instalación

Requisitos: Windows, macOS o Linux con conexión a internet. El instalador usa
[uv](https://docs.astral.sh/uv/) (gestor de Python) y lo instala si falta. No hace falta
tener Python instalado.

### Windows (desde el ZIP)

1. Descomprime `MCP-BOJA.zip` en cualquier carpeta.
2. Haz doble clic en **`instalar.bat`**.
3. Reinicia Claude Desktop (ciérralo también desde el icono de la bandeja del sistema).

El instalador:

1. instala el servidor con `uv tool install` (queda en `%USERPROFILE%\.local\bin\mcp-boja.exe`),
2. lo añade a `claude_desktop_config.json` (guardando antes una copia `.bak`),
3. lo registra en Claude Code si el comando `claude` está disponible.

Opciones: `instalar.bat --solo-desktop` o `instalar.bat --solo-code`.

### macOS / Linux

```bash
sh instalar.sh
```

### Directamente desde GitHub (sin ZIP)

```bash
uv tool install git+https://github.com/ATO097/MCP-BOJA
mcp-boja-configurar
```

### Configuración manual

Si prefieres no usar el configurador, añade esto a `claude_desktop_config.json`
(Claude Desktop → Configuración → Desarrollador → Editar configuración):

```json
{
  "mcpServers": {
    "normativa-andalucia": {
      "command": "C:\\Users\\TU_USUARIO\\.local\\bin\\mcp-boja.exe"
    }
  }
}
```

En Claude Code:

```bash
claude mcp add -s user normativa-andalucia -- mcp-boja
```

Sin instalar nada de forma permanente (útil para probar):

```json
"normativa-andalucia": {
  "command": "uvx",
  "args": ["--from", "git+https://github.com/ATO097/MCP-BOJA", "mcp-boja"]
}
```

## Actualizar y desinstalar

- **Actualizar:** descomprime la versión nueva y vuelve a ejecutar `instalar.bat`.
- **Desinstalar:** `desinstalar.bat` (quita el registro de Claude y el programa).

## Desarrollo

```bash
uv sync
uv run mcp-boja                    # servidor por stdio
uv run python tests/prueba_cliente.py   # prueba de extremo a extremo contra la API real
uv run mcp dev src/mcp_boja/server.py   # MCP Inspector
```

### Notas sobre la API de la Junta

- `GET /search` distingue mayúsculas y tildes, solo busca la frase exacta en el título
  y responde **400** cuando no hay resultados. Por eso el servidor descarga el catálogo
  completo (`/all`, unos 3 MB, regenerado a diario), lo guarda en memoria 3 horas y busca
  en local. Si falla la descarga, recurre a `/search`.
- Los filtros de `/search` exigen el valor exacto (o `-` para no filtrar).
- El texto de `leer_texto_boja` sale de la web del BOJA, que puede omitir imágenes o
  tablas. La versión auténtica es el PDF firmado (`pdf_oficial`).

## Licencia

Código bajo licencia MIT. Datos: © Junta de Andalucía, licencia
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.es).
