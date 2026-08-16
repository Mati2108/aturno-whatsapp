#!/usr/bin/env sh
# Construye el índice si no está y levanta el servicio.
# Va acá y no en el Dockerfile porque los embeddings por API necesitan la
# API key, que existe como variable del servicio pero no durante el build.
set -e
[ -d chroma ] || python -m src.rag.indice
exec uvicorn src.api.webhook:app --host 0.0.0.0 --port "${PORT:-8000}"
