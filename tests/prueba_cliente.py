"""Prueba de extremo a extremo: arranca el servidor por stdio y llama a cada herramienta."""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def mostrar(nombre, res, n=700):
    estado = "ERROR" if res.is_error else "OK"
    txt = res.content[0].text if res.content else json.dumps(res.structured_content)
    print(f"--- {nombre}: {estado}\n{txt[:n]}\n")
    return res


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_boja"])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = await s.list_tools()
        print("Herramientas:", [t.name for t in tools.tools], "\n")
        mostrar("contar_normas", await s.call_tool("contar_normas", {}))
        mostrar("listar rangos", await s.call_tool("listar_valores_filtro", {"tipo": "rangos"}))
        mostrar("listar organismos 'salud'", await s.call_tool("listar_valores_filtro", {"tipo": "organismos", "contiene": "salud"}))
        mostrar("buscar decretos vivienda", await s.call_tool("buscar_normativa", {"texto": "vivienda", "rango": "decreto", "limite": 3}))
        mostrar("turismo + ley (minusculas)", await s.call_tool("buscar_normativa", {"texto": "turismo", "rango": "ley", "limite": 3}))
        mostrar("sin tildes", await s.call_tool("buscar_normativa", {"texto": "vivienda turistica", "limite": 2}))
        mostrar("organismo parcial", await s.call_tool("buscar_normativa", {"organismo": "hacienda", "limite": 2}))
        mostrar("rango de fechas", await s.call_tool("buscar_normativa", {"desde_fecha": "2026-10-01", "hasta_fecha": "2026-10-31", "limite": 3}))
        mostrar("paginacion", await s.call_tool("buscar_normativa", {"rango": "ley", "limite": 2, "saltar": 2}), 400)
        mostrar("tema", await s.call_tool("buscar_normativa", {"tema": "medio ambiente", "limite": 2}), 400)
        mostrar("sin resultados", await s.call_tool("buscar_normativa", {"texto": "xqzw"}), 400)
        mostrar("fecha mal", await s.call_tool("buscar_normativa", {"desde_fecha": "01/10/2026"}), 300)
        mostrar("obtener_norma", await s.call_tool("obtener_norma", {"id": "688549"}))
        mostrar("obtener_norma inexistente", await s.call_tool("obtener_norma", {"id": "999999999"}), 300)
        mostrar("leer_texto_boja", await s.call_tool("leer_texto_boja", {"enlace_o_id": "688549", "max_caracteres": 1500}), 2500)
        mostrar("leer ley antigua", await s.call_tool("buscar_normativa", {"rango": "ley", "ascendente": True, "limite": 2}))


asyncio.run(main())
