#!/usr/bin/env bash
# run.sh — Levanta el servicio con un solo comando (lo que pide el Capstone).
set -euo pipefail
cd "$(dirname "$0")"

[ -d venv ] || { echo "Creando venv…"; python3.12 -m venv venv; }
./venv/bin/pip install -q -r requirements.txt

[ -f .env ] || { echo "Falta .env — copiá .env.example y completalo."; exit 1; }

echo "→ http://localhost:8000/salud"
exec ./venv/bin/uvicorn src.api.webhook:app --host 0.0.0.0 --port 8000 --reload
