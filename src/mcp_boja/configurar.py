"""Registra el servidor MCP-BOJA en Claude Desktop y/o Claude Code.

Uso:
    mcp-boja-configurar                 # Claude Desktop + Claude Code (si está instalado)
    mcp-boja-configurar --solo-desktop
    mcp-boja-configurar --solo-code
    mcp-boja-configurar --quitar        # elimina el registro
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

NOMBRE = "normativa-andalucia"


def _ruta_desktop() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def _comando() -> str:
    """Ruta absoluta del ejecutable mcp-boja (Claude Desktop no siempre hereda el PATH)."""
    exe = shutil.which("mcp-boja")
    if exe:
        return str(Path(exe).resolve())
    junto = Path(sys.argv[0]).resolve().with_name(
        "mcp-boja.exe" if sys.platform == "win32" else "mcp-boja"
    )
    if junto.exists():
        return str(junto)
    sys.exit("No se encuentra el ejecutable 'mcp-boja'. Instálalo antes con: uv tool install .")


def configurar_desktop(ruta: Path, comando: str, quitar: bool) -> None:
    config: dict = {}
    if ruta.exists():
        texto = ruta.read_text(encoding="utf-8").strip()
        if texto:
            try:
                config = json.loads(texto)
            except json.JSONDecodeError:
                sys.exit(f"{ruta} no es un JSON válido; corrígelo a mano antes de continuar.")
        copia = ruta.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(ruta, copia)
        print(f"  Copia de seguridad: {copia}")
    servidores = config.setdefault("mcpServers", {})
    if quitar:
        servidores.pop(NOMBRE, None)
    else:
        servidores[NOMBRE] = {"command": comando, "args": []}
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  Claude Desktop {'actualizado' if not quitar else 'limpiado'}: {ruta}")
    print("  Reinicia Claude Desktop (ciérralo también desde la bandeja) para aplicar el cambio.")


def configurar_code(comando: str, quitar: bool) -> None:
    claude = shutil.which("claude")
    if not claude:
        print("  Claude Code (comando 'claude') no está en el PATH; se omite.")
        print(f"  Para registrarlo a mano: claude mcp add -s user {NOMBRE} -- \"{comando}\"")
        return
    subprocess.run([claude, "mcp", "remove", "-s", "user", NOMBRE], capture_output=True)
    if quitar:
        print("  Eliminado de Claude Code.")
        return
    r = subprocess.run(
        [claude, "mcp", "add", "-s", "user", NOMBRE, "--", comando],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        print("  Registrado en Claude Code (ámbito de usuario).")
    else:
        print(f"  No se pudo registrar en Claude Code: {r.stderr.strip() or r.stdout.strip()}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--solo-desktop", action="store_true", help="solo Claude Desktop")
    g.add_argument("--solo-code", action="store_true", help="solo Claude Code")
    p.add_argument("--quitar", action="store_true", help="eliminar el registro")
    p.add_argument("--config-desktop", type=Path, help="ruta alternativa del JSON de Claude Desktop")
    a = p.parse_args()

    comando = _comando()
    print(f"Servidor: {comando}")
    if not a.solo_code:
        print("Claude Desktop:")
        configurar_desktop(a.config_desktop or _ruta_desktop(), comando, a.quitar)
    if not a.solo_desktop:
        print("Claude Code:")
        configurar_code(comando, a.quitar)


if __name__ == "__main__":
    main()
