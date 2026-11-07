# Starts the dashboard on http://localhost:3100  (run from the project root)
#   .\start-frontend.ps1        fast production mode (builds first, ~1 min)
#   .\start-frontend.ps1 -Dev   development mode (hot reload while editing code)
param([switch]$Dev)
Set-Location "$PSScriptRoot\frontend"
if (-not (Test-Path "node_modules")) { npm install }
if (-not (Test-Path ".env.local")) { Copy-Item ".env.local.example" ".env.local" -ErrorAction SilentlyContinue }
if ($Dev) {
    npm run dev
} else {
    npm run build
    if ($LASTEXITCODE -eq 0) { npm run start } else { Write-Host "Build failed - see the errors above." -ForegroundColor Red }
}
