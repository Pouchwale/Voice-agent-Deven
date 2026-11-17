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
    # A build can fail on a passing hiccup (files still locked right after login): try up to 3 times.
    for ($try = 1; $try -le 3; $try++) {
        npm run build
        if ($LASTEXITCODE -eq 0) { break }
        if ($try -lt 3) { Write-Host "Build failed (try $try of 3) - retrying in 15 seconds..." -ForegroundColor Yellow; Start-Sleep -Seconds 15 }
    }
    if ($LASTEXITCODE -eq 0) { npm run start } else { Write-Host "Build failed 3 times - see the errors above." -ForegroundColor Red }
}
