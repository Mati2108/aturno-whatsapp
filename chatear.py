"""
chatear.py — Hablar con el bot desde la terminal, sin WhatsApp y sin gastar cupo.

PARA QUÉ EXISTE
---------------
Todo lo que hace este producto pasa antes de Twilio: entender el mensaje,
decidir el paso, buscar en aturno, redactar con plantillas. Twilio solo lo
entrega. Así que se puede probar el sistema ENTERO sin mandar un mensaje.

Eso resuelve tres problemas de una:

  1. Probar sin gastar el cupo de 50 mensajes diarios de la cuenta trial.
  2. Iterar rápido: acá el ciclo es un Enter, no abrir el celular.
  3. Verificar la integración con aturno de punta a punta antes de exponerla.

Lo único que NO prueba es el último salto —que Twilio entregue el texto— y
que la firma del webhook valide. Para eso hay que mandar un mensaje real.

CÓMO SE USA
-----------
    python chatear.py                      # contra el doble en memoria
    python chatear.py --negocio TU-SLUG    # contra el aturno REAL

Con `--negocio` los turnos que confirmes quedan en la agenda de verdad. Lo
demás (ver servicios, días y horarios) solo lee.

    /estado     en qué paso está y qué eligió
    /reset      empezar de cero
    /salir
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from langgraph.checkpoint.memory import MemorySaver

from src.agentes import flujo as F
from src.aturno.base import ClienteAturno
from src.aturno.doble import AturnoDoble
from src.config import config
from src.fechas import calendario

TELEFONO = "+5491130032002"

VERDE, GRIS, AMARILLO, ROJO, FIN = "\033[32m", "\033[90m", "\033[33m", "\033[31m", "\033[0m"


def _cliente(slug: str | None) -> tuple[ClienteAturno, str, str]:
    """Devuelve (cliente, business_id, nombre) según se pida real o doble."""
    if slug:
        from src.aturno.api import AturnoAPI

        return AturnoAPI(config().aturno_api_url), slug, slug
    return AturnoDoble(), "demo-peluqueria", "Peluquería Demo"


async def main() -> None:
    p = argparse.ArgumentParser(description="Chat con el bot, sin WhatsApp.")
    p.add_argument("--negocio", help="slug de aturno; sin esto usa el doble en memoria")
    p.add_argument("--detalle", action="store_true", help="mostrar logs internos")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.detalle else logging.WARNING,
        format=f"{GRIS}%(message)s{FIN}",
    )

    cliente, negocio, nombre_negocio = _cliente(args.negocio)
    if args.negocio:
        # El nombre sale de aturno y no del slug: es lo que la persona ve en el
        # saludo, y "aturno" no es como se llama el negocio.
        try:
            doc = await cliente._negocio(args.negocio)  # noqa: SLF001
            nombre_negocio = doc.get("name") or args.negocio
        except Exception:  # noqa: BLE001 — sin nombre se sigue con el slug
            pass
    F.configurar(cliente)
    # MemorySaver y no Postgres: esto es una sesión de prueba, no tiene que
    # dejar rastro ni exigir una base levantada para poder probar el bot.
    grafo = F.construir_flujo(MemorySaver())

    modo = (f"{AMARILLO}aturno REAL ({args.negocio}){FIN}" if args.negocio
            else f"{GRIS}doble en memoria{FIN}")
    print(f"\n  {VERDE}●{FIN} {nombre_negocio}   ·   {modo}")
    if args.negocio:
        print(f"  {AMARILLO}Los turnos que confirmes van a la agenda de verdad.{FIN}")
        print(f"  {GRIS}El backend de aturno puede tardar ~30s la primera vez (arranca en frío).{FIN}")
    print(f"  {GRIS}/estado · /reset · /salir{FIN}\n")

    sesion = 0
    while True:
        hilo = F.hilo_de(negocio, f"{TELEFONO}-{sesion}")
        try:
            texto = input(f"{VERDE}vos ▸{FIN} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not texto:
            continue
        if texto in ("/salir", "/exit", "/q"):
            break
        if texto == "/reset":
            sesion += 1
            print(f"{GRIS}  (conversación nueva){FIN}\n")
            continue
        if texto == "/estado":
            estado = await grafo.aget_state({"configurable": {"thread_id": hilo}})
            v = estado.values or {}
            print(f"{GRIS}  paso: {v.get('estado')}")
            for k in ("servicio_id", "profesional_id", "fecha", "hora", "nombre"):
                if v.get(k):
                    print(f"  {k}: {v[k]}")
            print(f"  opciones mostradas: {len(v.get('opciones') or [])}{FIN}\n")
            continue

        cfg = {"configurable": {
            "thread_id": hilo,
            "business_id": negocio,
            "nombre_negocio": nombre_negocio,
            "telefono": TELEFONO,
            "nombre_cliente": None,
            "calendario": calendario(),
        }}
        try:
            salida = await grafo.ainvoke({"mensaje": texto}, cfg)
            respuesta = salida["respuesta"]
        except Exception as e:  # noqa: BLE001 — acá el error ES el resultado útil
            print(f"{ROJO}  ✗ {type(e).__name__}: {e}{FIN}\n")
            continue

        # Sangrado para que se lea como un mensaje y no como salida de consola.
        print(f"\n{GRIS}  bot ▾{FIN}")
        for linea in respuesta.split("\n"):
            print(f"  {linea}")
        print()

    if hasattr(cliente, "cerrar"):
        await cliente.cerrar()
    print(f"{GRIS}  listo.{FIN}\n")


if __name__ == "__main__":
    asyncio.run(main())
