#!/usr/bin/env sh
# Levanta el servicio. El índice de búsqueda se intenta construir, pero NO
# puede impedir que el bot arranque.
#
# POR QUÉ ESTO NO USA `set -e`
# Lo usaba, y tumbó el servicio en producción. El índice se construye con
# embeddings por API; el día que se agotó la cuota diaria de Gemini, ese
# comando salió con error, `set -e` cortó el script y el contenedor murió con
# "Exited with status 1". O sea: nadie pudo sacar un turno porque el bot no
# podía contestar preguntas — un componente secundario matando al principal.
#
# El orden correcto de importancia es al revés. Sacar turnos es lo que el
# negocio vende; responder preguntas es lo que suma. Si la búsqueda no está,
# el bot dice "ese dato no lo tengo cargado" y sigue reservando.

# El índice solo se construye si falta. En Render el disco es efímero, así que
# en la práctica se arma en cada despliegue; la condición está para no
# rehacerlo al reiniciar un contenedor que ya lo tenía.
if [ ! -d chroma ]; then
  echo "Construyendo el índice de búsqueda…"
  if python -m src.rag.indice; then
    echo "Índice listo."
  else
    echo "AVISO: no se pudo construir el índice (¿cuota de embeddings?)."
    echo "       El bot arranca igual: va a poder sacar turnos, pero a las"
    echo "       preguntas de información va a contestar que no tiene el dato."
  fi
fi

exec uvicorn src.api.webhook:app --host 0.0.0.0 --port "${PORT:-8000}"
