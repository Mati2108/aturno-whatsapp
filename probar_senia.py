"""
probar_senia.py — ¿El servicio con seña se cobra de verdad?

QUÉ RESPONDE
------------
Una pregunta sola: cuando el negocio marca un servicio como "requiere seña",
¿el turno que entra por WhatsApp queda esperando el pago —con su link— en vez de
confirmarse gratis? Es el agujero que documentaba PENDIENTES.md: por WhatsApp se
salteaba el depósito que la web sí cobra.

No alcanza con que el bot mande un link. Se verifica del otro lado, en aturno:
que la reserva exista, que su estado sea `pending_deposit` y no `pending`, y que
tenga puesto el vencimiento que aparta el horario.

QUÉ NO HACE
-----------
NO paga. El link queda impreso para que lo abra una persona si quiere completar
la prueba de punta a punta. Tampoco cancela nada: una reserva esperando la seña
suelta el horario sola cuando vence su retención, que es exactamente para lo que
esa retención existe.

    python probar_senia.py                 # sin red, contra el doble
    python probar_senia.py --real          # contra el aturno REAL

Con `--real` crea una reserva de verdad en la agenda del negocio. Se pide para
dentro de unos días —no para hoy— para que al negocio no le aparezca como algo
inminente mientras se prueba.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date, timedelta

import httpx
from langgraph.checkpoint.memory import MemorySaver

from src.agentes import flujo as F
from src.agentes.estados import Estado
from src.aturno.doble import AturnoDoble
from src.config import config
from src.fechas import calendario

VERDE, ROJO, AMARILLO, GRIS, NEGRITA, FIN = (
    "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[1m", "\033[0m")

TELEFONO = "+5491130032002"
NOMBRE = "Prueba Seña"

ok = True


def chequear(nombre: str, cond: bool, detalle: str = "") -> None:
    global ok
    ok = ok and bool(cond)
    color = VERDE if cond else ROJO
    print(f"  {color}{'✓' if cond else '✗'}{FIN} {nombre}"
          + (f"{GRIS}  ({detalle}){FIN}" if detalle else ""))


async def conversar(g, cfg: dict, servicio: str, mensajes_maximos: int = 12) -> dict:
    """Habla hasta llegar al resumen: primero el servicio, después «1» siempre.

    El servicio se elige por NOMBRE y no por número porque el número depende de
    en qué posición de la lista haya quedado, y lo que se está probando es
    justamente el que pide seña. La resolución por nombre la hace el propio
    flujo, sin pasar por el modelo.

    De ahí en adelante se contesta «1» a ciegas, y no con un guion fijo, porque
    el negocio real puede tener equipo o no tenerlo y el flujo saltea los pasos
    que no aplican. Un guion escrito de antemano se rompe cuando el negocio
    cambia su configuración, que no es lo que se está probando.
    """
    salida = await g.ainvoke({"mensaje": "hola"}, cfg)
    print(f"\n{GRIS}── el bot dice ──{FIN}\n{salida['respuesta'][:600]}")

    salida = await g.ainvoke({"mensaje": servicio}, cfg)
    print(f"\n{GRIS}── yo: «{servicio}» · el bot dice ──{FIN}\n{salida['respuesta'][:600]}")

    for _ in range(mensajes_maximos):
        if salida.get("estado") == Estado.ESPERANDO_CONFIRMACION.value:
            return salida
        salida = await g.ainvoke({"mensaje": "1"}, cfg)
        print(f"\n{GRIS}── yo: «1» · el bot dice ──{FIN}\n{salida['respuesta'][:600]}")
    return salida


def _minutos_de_retencion(reserva: dict) -> int | None:
    """Cuánto dura de verdad la retención de esta reserva, según la reserva.

    Se calcula de los dos sellos que puso aturno y no de ninguna constante:
    es el número contra el que hay que comparar lo que el bot le prometió a la
    persona. Que no coincidan es un turno que se libera antes de lo que se dijo.
    """
    from datetime import datetime as _dt
    try:
        creada = _dt.fromisoformat((reserva["createdAt"]).replace("Z", "+00:00"))
        vence = _dt.fromisoformat((reserva["holdExpiresAt"]).replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    return round((vence - creada).total_seconds() / 60)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true",
                        help="contra el aturno de verdad, creando una reserva real")
    parser.add_argument("--negocio", default="aturno", help="slug del negocio")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    cfg_app = config()

    if args.real:
        from src.aturno.api import AturnoAPI
        cliente = AturnoAPI(cfg_app.aturno_api_url)
        negocio = args.negocio
        print(f"{AMARILLO}{NEGRITA}CONTRA ATURNO REAL{FIN} · {cfg_app.aturno_api_url} "
              f"· negocio «{negocio}»")
        print(f"{GRIS}Crea una reserva de verdad. No paga y no cancela: la "
              f"retención vence sola.{FIN}")
    else:
        cliente = AturnoDoble()
        negocio = "demo-peluqueria"
        print(f"{NEGRITA}CONTRA EL DOBLE EN MEMORIA{FIN} · negocio «{negocio}»")

    F.configurar(cliente)
    g = F.construir_flujo(MemorySaver())

    # Qué servicios piden seña. Si ninguno la pide, no hay nada que probar y
    # decirlo es más útil que un verde vacío.
    servicios = await cliente.listar_servicios(negocio)
    con_senia = [s for s in servicios if s.requiere_senia]
    print(f"\n{NEGRITA}SERVICIOS{FIN}")
    for s in servicios:
        marca = f"{VERDE}seña ${s.senia}{FIN}" if s.requiere_senia else f"{GRIS}sin seña{FIN}"
        print(f"  {s.nombre:26} ${s.precio:>10,.0f}  {marca}")
    if not con_senia:
        print(f"\n{ROJO}Ningún servicio pide seña: no hay nada que probar.{FIN}")
        print(f"{GRIS}Se prende con «requiere seña» en el servicio, desde el panel.{FIN}")
        return 1

    cfg = {"configurable": {
        "thread_id": f"senia-{date.today().isoformat()}",
        "business_id": negocio,
        "nombre_negocio": "Aturno" if args.real else "Peluquería Demo",
        "telefono": TELEFONO,
        # Con el nombre puesto se saltea ese paso, que es el único que depende
        # del clasificador. Acá se prueba el cobro, no la comprensión.
        "nombre_cliente": NOMBRE,
        "calendario": calendario(date.today() + timedelta(days=1), 8),
    }}

    print(f"\n{NEGRITA}LA CONVERSACIÓN{FIN}")
    salida = await conversar(g, cfg, con_senia[0].nombre)

    print(f"\n{NEGRITA}EL RESUMEN, ANTES DE CONFIRMAR{FIN}")
    chequear("llegó al resumen",
             salida.get("estado") == Estado.ESPERANDO_CONFIRMACION.value,
             f"estado={salida.get('estado')}")
    chequear("avisa que hay una seña ANTES de que la persona diga que sí",
             "Seña:" in (salida.get("respuesta") or ""))

    salida = await g.ainvoke({"mensaje": "sí"}, cfg)
    print(f"\n{GRIS}── yo: «sí» · el bot dice ──{FIN}\n{salida['respuesta']}")

    print(f"\n{NEGRITA}LO QUE PASÓ AL CONFIRMAR{FIN}")
    chequear("NO quedó confirmado sin pagar",
             salida.get("estado") != Estado.CONFIRMADO.value,
             f"estado={salida.get('estado')}")
    chequear("quedó esperando la seña",
             salida.get("estado") == Estado.ESPERANDO_SENIA.value,
             f"estado={salida.get('estado')}")
    respuesta = salida.get("respuesta") or ""
    chequear("el mensaje trae el link de pago", "http" in respuesta)
    codigo = salida.get("codigo_pendiente")
    chequear("guardó el código, para poder consultar el pago", bool(codigo), str(codigo))

    if not args.real or not codigo:
        print(f"\n{VERDE if ok else ROJO}{'Todo en verde.' if ok else 'Hay algo roto.'}{FIN}")
        return 0 if ok else 1

    # ---- La verificación que importa: qué quedó del otro lado ----
    print(f"\n{NEGRITA}Y AHORA, DEL LADO DE ATURNO{FIN}")
    async with httpx.AsyncClient(timeout=40) as http:
        uid = await cliente._uid(negocio)  # noqa: SLF001 — es una verificación
        r = await http.get(f"{cfg_app.aturno_api_url}/api/bookings/by-code/{codigo}",
                           params={"businessId": uid})
    if r.status_code != 200:
        chequear("la reserva se puede consultar por código", False,
                 f"HTTP {r.status_code}: {r.text[:120]}")
        print(f"\n{ROJO}Hay algo roto.{FIN}")
        return 1

    reserva = (r.json() or {}).get("booking") or {}
    # DOS objetos distintos y hay que mirar los dos. `deposit` lo escribe la
    # creación de la reserva y es donde está el MONTO; `depositInfo` lo escribe
    # después `create-link` y es donde está el LINK. Mirar uno solo hace creer
    # que falta la mitad del dato — pasó al escribir esta prueba.
    deposito = reserva.get("deposit") or {}
    del_link = reserva.get("depositInfo") or {}
    print(f"{GRIS}  estado={reserva.get('status')}  "
          f"holdExpiresAt={reserva.get('holdExpiresAt')}{FIN}")
    print(f"{GRIS}  deposit={deposito}{FIN}")
    print(f"{GRIS}  depositInfo={ {k: v for k, v in del_link.items() if k != 'paymentLink'} }{FIN}")

    chequear("la reserva existe en la agenda", bool(reserva.get("id")))
    chequear("nació esperando la seña, NO firme",
             reserva.get("status") == "pending_deposit",
             f"status={reserva.get('status')}")
    chequear("tiene vencimiento, así que suelta el horario sola",
             bool(reserva.get("holdExpiresAt")))
    chequear("quedó guardado el link de pago en la reserva",
             bool(del_link.get("paymentLink")))
    chequear("y el monto de la seña", bool(deposito.get("amount")),
             f"${deposito.get('amount')}")
    chequear("el turno queda marcado como venido de WhatsApp",
             reserva.get("origen") == "whatsapp",
             f"origen={reserva.get('origen')} — sin esto el negocio no puede "
             f"medir el canal (requiere desplegar el backend)")

    # Que el plazo que se le prometió a la persona sea el que de verdad dura.
    minutos_reales = _minutos_de_retencion(reserva)
    dicho = next((int(p) for p in respuesta.split() if p.isdigit()), None)
    chequear("el plazo que dice el mensaje es el que dura la retención",
             dicho is not None and minutos_reales is not None
             and abs(dicho - minutos_reales) <= 1,
             f"el bot dijo {dicho} min y la retención dura {minutos_reales} min")

    pagada = await cliente.senia_pagada(negocio, codigo)
    chequear("el bot sabe que todavía no está pagada", pagada is False,
             f"senia_pagada={pagada}")

    print(f"\n{NEGRITA}PARA COMPLETARLA A MANO{FIN}")
    for linea in respuesta.splitlines():
        if linea.startswith("http"):
            print(f"  {linea}")
    print(f"{GRIS}  Pagando ese link, la reserva pasa a confirmada y el bot "
          f"avisa por WhatsApp.{FIN}")
    print(f"{GRIS}  Si no se paga, el horario se libera solo al vencer la "
          f"retención.{FIN}")

    print(f"\n{VERDE if ok else ROJO}{'Todo en verde.' if ok else 'Hay algo roto.'}{FIN}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
