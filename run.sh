#!/usr/bin/env bash
# run.sh — Levanta todo con un comando, sin Docker.
# Para la versión con contenedores:  docker compose up
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo "Falta .env — copiá .env.example y completalo."; exit 1; }

[ -d venv ] || { echo "→ Creando entorno…"; python3.12 -m venv venv; }
./venv/bin/pip install -q -r requirements.txt

# El índice del RAG se arma una sola vez; si ya está, no se recalcula.
[ -d chroma ] || { echo "→ Construyendo el índice del RAG…"; ./venv/bin/python -m src.rag.indice; }

echo "→ Salud:   http://localhost:8000/salud"
echo "→ Trazas:  http://localhost:6006  (si corriste 'phoenix serve')"
exec ./venv/bin/uvicorn src.api.webhook:app --host 0.0.0.0 --port 8000
