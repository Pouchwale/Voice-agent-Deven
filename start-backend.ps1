# Starts the FastAPI backend on http://localhost:8100  (run from the project root)
Set-Location "$PSScriptRoot\backend"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    .\.venv\Scripts\pip install -r requirements.txt
}
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created backend\.env - fill in your API keys." }
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8100 --ws-max-size 1048576
