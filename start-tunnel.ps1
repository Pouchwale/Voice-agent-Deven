# Opens the public HTTPS tunnel Vobiz uses to reach the backend (http://localhost:8100).
# One-time setup already done:  ngrok config add-authtoken <your token>
# backend/.env must have:       PUBLIC_BASE_URL=https://enjoyable-cubbyhole-straw.ngrok-free.dev
# ngrok-policy.yml exposes ONLY /vobiz/* (webhooks + call audio). The dashboard API stays private.
Set-Location $PSScriptRoot
ngrok http 8100 --url https://enjoyable-cubbyhole-straw.ngrok-free.dev --traffic-policy-file ngrok-policy.yml
