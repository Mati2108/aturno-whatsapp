"""
simular_notificacion.py — El ensayo: alguien pide una persona y el negocio se entera.

QUÉ PRUEBA
----------
La mitad de la escalación que vive en el bot está hecha; la que vive en aturno
—recibir el aviso y mostrarlo como notificación— todavía no. Esto levanta un
receptor de mentira en la máquina, hace que el bot le avise a ÉL, y muestra
exactamente qué le va a llegar a la app cuando exista.

O sea: se puede ver funcionando el contrato entero sin tocar aturno. Y cuando
del otro lado haya alguien escuchando, lo único que cambia es la URL.

    python simular_notificacion.py

No manda mensajes de WhatsApp ni escribe en la agenda: solo escala.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PUERTO = 8877

# Antes de importar nada del proyecto: las variables de entorno le ganan al
# .env en pydantic-settings, y `config()` está cacheada desde la primera vez.
os.environ["ESCALACION_WEBHOOK"] = f"http://127.0.0.1:{PUERTO}/aviso"

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from src.agentes import flujo as F  # noqa: E402
from src.aturno.doble import AturnoDoble  # noqa: E402
from src.fechas import calendario  # noqa: E402

VERDE, GRIS, AMARILLO, NEGRITA, FIN = (
    "\033[32m", "\033[90m", "\033[33m", "\033[1m", "\033[0m")

NEG, TEL = "demo-peluqueria", "+5491130032002"
recibidos: list[dict] = []


class Receptor(BaseHTTPRequestHandler):
    """Hace de aturno: recibe el aviso y lo muestra como lo vería el dueño."""

    def do_POST(self):  # noqa: N802
        largo = int(self.headers.get("Content-Length", 0))
        aviso = json.loads(self.rfile.read(largo) or b"{}")
        recibidos.append(aviso)

        print(f"\n{AMARILLO}{'█'*60}{FIN}")
        print(f"{AMARILLO}█{FIN}  {NEGRITA}🔔 NOTIFICACIÓN EN LA APP DE ATURNO{FIN}")
        print(f"{AMARILLO}{'█'*60}{FIN}")
        quien = aviso.get("nombre") or aviso.get("telefono")
        motivo = ("pidió hablar con alguien" if aviso.get("motivo") == "pedido"
                  else "se trabó con el bot")
        print(f"  {NEGRITA}{quien}{FIN} {motivo}")
        print(f"  {GRIS}negocio:  {aviso.get('business_id')}{FIN}")
        print(f"  {GRIS}teléfono: {aviso.get('telefono')}{FIN}")
        print(f"  {GRIS}estaba en: {aviso.get('paso')}{FIN}")
        print(f"  {GRIS}último mensaje: «{aviso.get('ultimo_mensaje')}»{FIN}")
        print(f"  {GRIS}momento:  {aviso.get('momento')}{FIN}")
        print(f"{AMARILLO}{'█'*60}{FIN}\n")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):  # silencia el log del servidor
        pass


async def main() -> None:
    logging.basicConfig(level=logging.ERROR)

    servidor = HTTPServer(("127.0.0.1", PUERTO), Receptor)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    print(f"\n{GRIS}  (aturno de mentira escuchando en :{PUERTO}){FIN}")

    F.configurar(AturnoDoble())
    grafo = F.construir_flujo(MemorySaver())
    hilo = F.hilo_de(NEG, TEL)

    # Avanza hasta el nombre, pide una persona, escribe mientras espera, y
    # vuelve al bot. Es la secuencia completa de la escalación.
    guion = ["hola", "1", "3", "1", "1",
             "quiero hablar con una persona",
             "hola? hay alguien?",
             "seguir con el bot"]

    for m in guion:
        salida = await grafo.ainvoke({"mensaje": m}, {"configurable": {
            "thread_id": hilo, "business_id": NEG,
            "nombre_negocio": "Peluquería Demo", "telefono": TEL,
            "nombre_cliente": None, "calendario": calendario()}})
        print(f"{VERDE}  vos ▸{FIN} {m}")
        respuesta = salida.get("respuesta") or ""
        if respuesta.strip():
            for l in respuesta.split("\n"):
                print(f"        {l}")
        else:
            print(f"        {GRIS}(el bot se calla — la conversación es del negocio){FIN}")
        print()

    servidor.shutdown()
    print(f"{'─'*60}")
    print(f"  avisos que llegaron a «aturno»: {len(recibidos)}")
    print(f"  {GRIS}Cuando aturno exponga ese endpoint, esto mismo aparece "
          f"como notificación en la app.{FIN}\n")


if __name__ == "__main__":
    asyncio.run(main())
