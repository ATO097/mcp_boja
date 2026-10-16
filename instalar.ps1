# Instalador de MCP-BOJA (Windows)
$ErrorActionPreference = "Stop"
$dir = $PSScriptRoot
Write-Host "== Instalando MCP-BOJA (normativa de la Junta de Andalucía) ==" -ForegroundColor Cyan

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "No se encuentra 'uv' (gestor de Python de Astral)."
    $r = Read-Host "¿Instalarlo ahora desde https://astral.sh/uv? (S/N)"
    if ($r -notmatch '^[sSyY]') { Write-Host "Instala uv y vuelve a ejecutar este script."; exit 1 }
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Write-Host "No se pudo instalar uv." -ForegroundColor Red; exit 1 }
}

Write-Host "`n1/3 Instalando el servidor con uv..."
uv tool install --force --reinstall "$dir"
if ($LASTEXITCODE -ne 0) { Write-Host "Falló la instalación." -ForegroundColor Red; exit 1 }

Write-Host "`n2/3 Añadiendo las herramientas de uv al PATH..."
uv tool update-shell | Out-Null
$bin = (uv tool dir --bin).Trim()

Write-Host "`n3/3 Registrando en Claude Desktop / Claude Code..."
& (Join-Path $bin "mcp-boja-configurar.exe") @args

Write-Host "`nListo. Reinicia Claude Desktop para ver la herramienta 'normativa-andalucia'." -ForegroundColor Green
