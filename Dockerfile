# Imagen del servicio. slim y no la completa: la diferencia son ~700 MB que en
# un plan chico de hosting importan, y no usamos nada del toolchain extra.
FROM python:3.12-slim

WORKDIR /app

# Las dependencias en su propia capa: cambiar código no reinstala todo.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY datos/ ./datos/

# El índice se construye al arrancar, no en el build: con embeddings por API
# hace falta la GEMINI_API_KEY, que existe como variable de entorno del
# servicio pero no durante el build.
COPY arranque.sh .
RUN chmod +x arranque.sh

EXPOSE 8000

# Sin --reload: recarga el proceso ante cambios de archivo y en producción
# solo agrega consumo.
CMD ["./arranque.sh"]
