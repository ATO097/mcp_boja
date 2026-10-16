# Desinstalador de MCP-BOJA (Windows)
$bin = (uv tool dir --bin).Trim()
$cfg = Join-Path $bin "mcp-boja-configurar.exe"
if (Test-Path $cfg) { & $cfg --quitar }
uv tool uninstall mcp-boja
Write-Host "MCP-BOJA desinstalado. Reinicia Claude Desktop." -ForegroundColor Green
