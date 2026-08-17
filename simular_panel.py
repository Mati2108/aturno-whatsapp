"""
simular_panel.py — El circuito completo: el cliente escribe, el dueño contesta.

QUÉ PRUEBA
----------
Los dos caminos nuevos entre el bot y el panel de aturno, en las dos
direcciones y contra el contrato de verdad:

    bot → panel     cada mensaje, para que la conversación se vea
    panel → bot     lo que escribe el dueño, para que salga por WhatsApp

El "aturno" de acá es de mentira, pero habla el mismo protocolo que el real:
mismo endpoint, mismo secreto en la cabecera, mismo cuerpo. Lo que cambia
cuando se conecte el de verdad es la URL y nada más.

QUÉ NO PRUEBA
-------------
Que Firestore guarde bien y que la pantalla del panel se vea linda. Eso pide
las credenciales de Firebase del backend de aturno, que este servicio no tiene
ni quiere tener. La lógica pura de ese lado está probada aparte, en
`aturno/backend/src/conversaciones.test.js` (26 aserciones).

    python simular_panel.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PUERTO = 8878
SECRETO = "secreto-de-prueba-no-usar-en-produccion"

# Antes de importar el proyecto: las variables de entorno le ganan al .env, y
# `config()` queda cacheada desde la primera llamada.
os.environ["PANEL_URL"] = f"http://127.0.0.1:{PUERTO}"
os.environ["PANEL_SECRETO"] = SECRETO

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from src.agentes import flujo as F  # noqa: E402
from src.agentes.estados import Estado  # noqa: E402
from src.aturno.doble import AturnoDoble  # noqa: E402
from src.fechas import calendario  # noqa: E402

VERDE, GRIS, AMARILLO, AZUL, NEGRITA, FIN = (
    "\033[32m", "\033[90m", "\033[33m", "\033[36m", "\033[1m", "\033[0m")

NEG, TEL = "demo-peluqueria", "+5491130032002"

# Lo que "aturno" fue guardando. Es el equivalente en memoria de lo que en el
# real va a Firestore: los mensajes y el resumen de la conversación.
panel: dict = {"mensajes": [], "resumen": {}}


class ATurnoDeMentira(BaseHTTPRequestHandler):
    """Implementa POST /api/whatsapp/bot/evento igual que el backend real."""

    def do_POST(self):  # noqa: N802
        if self.headers.get("x-bot-secret") != SECRETO:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"No encontrado"}')
            return

        cuerpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        panel["mensajes"].append(cuerpo)

        previo = panel["resumen"]
        del_cliente = cuerpo.get("autor") == "cliente"
        panel["resumen"] = {
            "telefono": cuerpo.get("telefono"),
            "ultimoTexto": (cuerpo.get("texto") or "")[:160],
            "ultimoAutor": cuerpo.get("autor"),
            "necesitaHumano": (
                True if cuerpo.get("necesita_humano")
                else (False if cuerpo.get("autor") == "negocio"
                      else previo.get("necesitaHumano", False))
            ),
            "sinLeer": previo.get("sinLeer", 0) + (1 if del_cliente else 0),
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):
        pass


def mostrar_panel() -> None:
    """Cómo se ve la conversación del lado del negocio."""
    r = panel["resumen"]
    print(f"\n{AZUL}┌{'─' * 58}┐{FIN}")
    print(f"{AZUL}│{FIN} {NEGRITA}PANEL DE ATURNO · Conversaciones{FIN}")
    print(f"{AZUL}├{'─' * 58}┤{FIN}")
    marca = f"{AMARILLO} ● pide una persona{FIN}" if r.get("necesitaHumano") else ""
    sin_leer = f"  ({r['sinLeer']} sin leer)" if r.get("sinLeer") else ""
    print(f"{AZUL}│{FIN} {r.get('telefono')}{sin_leer}{marca}")
    print(f"{AZUL}│{FIN} {GRIS}último: «{(r.get('ultimoTexto') or '')[:44]}»{FIN}")
    print(f"{AZUL}├{'─' * 58}┤{FIN}")
    for m in panel["mensajes"]:
        quien = {"cliente": "Cliente ", "bot": "Asistente", "negocio": "Vos     "}[m["autor"]]
        color = {"cliente": "", "bot": GRIS, "negocio": VERDE}[m["autor"]]
        primera = (m["texto"] or "").split("\n")[0][:44]
        print(f"{AZUL}│{FIN} {color}{quien}{FIN} │ {primera}")
    print(f"{AZUL}└{'─' * 58}┘{FIN}\n")


async def main() -> None:
    logging.basicConfig(level=logging.ERROR)

    servidor = HTTPServer(("127.0.0.1", PUERTO), ATurnoDeMentira)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    print(f"\n{GRIS}  (aturno de mentira en :{PUERTO}, hablando el protocolo real){FIN}")

    F.configurar(AturnoDoble())
    grafo = F.construir_flujo(MemorySaver())
    hilo = F.hilo_de(NEG, TEL)
    cfg = {"configurable": {
        "thread_id": hilo, "business_id": NEG, "nombre_negocio": "Peluquería Demo",
        "telefono": TEL, "nombre_cliente": None, "calendario": calendario()}}

    from src.api.conversaciones import avisar_a_aturno, evento

    async def escribe_el_cliente(texto: str) -> None:
        """Lo que hace el webhook con cada mensaje que llega."""
        salida = await grafo.ainvoke({"mensaje": texto}, cfg)
        estado = salida.get("estado")
        await avisar_a_aturno(evento(
            NEG, TEL, texto, de_quien="cliente",
            necesita_humano=estado == Estado.EN_MANOS_HUMANAS.value, paso=estado))
        print(f"{VERDE}  cliente ▸{FIN} {texto}")
        respuesta = salida.get("respuesta") or ""
        if respuesta.strip():
            await avisar_a_aturno(evento(NEG, TEL, respuesta, de_quien="bot", paso=estado))
            print(f"        {respuesta.splitlines()[0][:56]}")
        else:
            print(f"        {GRIS}(el bot se calla){FIN}")

    print(f"\n{NEGRITA}{'═' * 62}\n  1. LA PERSONA HABLA CON EL BOT\n{'═' * 62}{FIN}")
    for m in ["hola", "1", "3", "1", "1"]:
        await escribe_el_cliente(m)

    print(f"\n{NEGRITA}{'═' * 62}\n  2. PIDE UNA PERSONA\n{'═' * 62}{FIN}")
    await escribe_el_cliente("quiero hablar con alguien del local")
    mostrar_panel()

    print(f"{NEGRITA}{'═' * 62}\n  3. ESCRIBE OTRA VEZ Y EL BOT NO CONTESTA\n{'═' * 62}{FIN}")
    await escribe_el_cliente("es para preguntar si atienden obra social")

    print(f"\n{NEGRITA}{'═' * 62}\n  4. EL DUEÑO CONTESTA DESDE EL PANEL\n{'═' * 62}{FIN}")
    # Es lo que hace POST /panel/responder: manda el texto y deja la
    # conversación en manos de la persona.
    texto_del_dueno = "Hola! Sí, trabajamos con OSDE y Swiss Medical. Te espero el jueves."
    estado_actual = await grafo.aget_state(cfg)
    await grafo.aupdate_state(cfg, {
        "estado": Estado.EN_MANOS_HUMANAS.value,
        "estado_previo": (estado_actual.values or {}).get("estado_previo"),
    })
    await avisar_a_aturno(evento(NEG, TEL, texto_del_dueno, de_quien="negocio"))
    print(f"{VERDE}  vos ▸{FIN} {texto_del_dueno}")
    print(f"        {GRIS}(sale por WhatsApp desde el número del asistente){FIN}")
    mostrar_panel()

    print(f"{NEGRITA}{'═' * 62}\n  5. EL BOT RETOMA CUANDO SE LO PIDEN\n{'═' * 62}{FIN}")
    await escribe_el_cliente("seguir con el bot")

    servidor.shutdown()
    ok = (len(panel["mensajes"]) >= 8
          and panel["resumen"].get("necesitaHumano") is False)
    print(f"\n{'─' * 62}")
    print(f"  mensajes que vio el panel: {len(panel['mensajes'])}")
    print(f"  {VERDE if ok else AMARILLO}{'✓ el circuito completo funciona' if ok else '! revisar'}{FIN}")
    print(f"  {GRIS}Con el aturno real cambia la URL. Nada más.{FIN}\n")


if __name__ == "__main__":
    asyncio.run(main())
