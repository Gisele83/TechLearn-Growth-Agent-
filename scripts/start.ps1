# Démarrage local sous Windows PowerShell.
# Ouvre l'API et l'interface dans deux fenêtres distinctes.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path ".venv")) {
    Write-Error "Environnement Python absent. Lancer d'abord : python -m venv .venv ; .venv\Scripts\pip install -e 'apps\api[dev]'"
    exit 1
}

if (-not (Test-Path "apps\web\node_modules")) {
    Write-Error "Dépendances web absentes. Lancer d'abord : cd apps\web ; npm install"
    exit 1
}

$env:PYTHONPATH = Join-Path $PWD "apps\api\src"
if (-not $env:GA_ENVIRONMENT) { $env:GA_ENVIRONMENT = "local" }

Write-Host "API       -> http://localhost:8000  (documentation : /docs)"
Write-Host "Interface -> http://localhost:5173"
Write-Host ""

Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "`$env:PYTHONPATH='$($env:PYTHONPATH)'; `$env:GA_ENVIRONMENT='$($env:GA_ENVIRONMENT)'; .venv\Scripts\uvicorn.exe growth_agent.main:app --reload --port 8000"
)

Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location apps\web; npm run dev"
)

Write-Host "Deux fenêtres ont été ouvertes. Les fermer pour arrêter les services."
