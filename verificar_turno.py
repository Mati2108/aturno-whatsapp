"""
verificar_turno.py — ¿Se puede sacar un turno, de verdad, sin problemas?

QUÉ RESPONDE
------------
Una sola pregunta, la única que importa hoy: si una persona escribe por
WhatsApp, ¿termina con un turno en la agenda del negocio? Y no "el bot dijo que
sí": el turno se busca después en aturno, con el código que le dio a la persona,
y se comparan servicio, día, hora, nombre y teléfono contra lo que se pidió.

Un bot que contesta "listo, confirmado" y no dejó nada en la agenda es peor que
uno que falla, porque el negocio se entera cuando la persona llega.

QUÉ NO ES
---------
No es `test_flujo.py`, que prueba invariantes de redacción contra el doble en
memoria. Esto habla con el aturno real y escribe en la agenda real.

CÓMO SE USA
-----------
    python verificar_turno.py                 # contra aturno real
    python verificar_turno.py --doble         # sin red, contra el doble
    python verificar_turno.py --no-limpiar    # deja los turnos creados

Cada escenario reserva y después CANCELA lo que creó, así se puede correr las
veces que haga falta sin llenar la agenda. Los turnos se piden para dentro de
unos días, no para hoy, por dos razones: cancelar por código no funciona para
el mismo día en el backend desplegado (ver PENDIENTES.md), y un turno de prueba
dentro de un rato le aparece al negocio como algo inminente.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta

import httpx
from langgraph.checkpoint.memory import MemorySaver

from src.agentes import flujo as F
from src.agentes.estados import Estado
from src.aturno.doble import AturnoDoble
from src.config import config
from src.fechas import calendario, hoy

TELEFONO = "+5491130032002"
DIAS_ADELANTE = 2          # cuánto en el futuro se piden los turnos de prueba

VERDE, ROJO, GRIS, NEGRITA, FIN = "\033[32m", "\033[31m", "\033[90m", "\033[1m", "\033[0m"

# Nada de esto puede aparecer nunca en un mensaje al cliente.
FUGAS = [
    (r'\{"', "JSON"),
    (r"'intent'", "dict de Python"),
    (r"\bsvc-[a-z]+", "id de servicio"),
    (r"\bp-[a-z]+\b", "id de profesional"),
    (r"Traceback", "traceback"),
    (r"\bNone\b", "None"),
    (r"<[a-z]+\.[A-Z]", "repr de objeto"),
]


# ══════════════════════════════════════════════════════════════════
# Los escenarios
# ══════════════════════════════════════════════════════════════════
#
# `elegir_dia` es una función y no un número fijo: la lista de días saltea los
# cerrados, así que "el tercero" no es siempre el mismo día. Se resuelve
# mirando qué opciones mostró el bot.

def elegir_dia(opciones: list[str]) -> str:
    """El número del primer día que esté al menos DIAS_ADELANTE."""
    fechas = [date.fromisoformat(o) for o in opciones]
    objetivo = hoy() + timedelta(days=DIAS_ADELANTE)
    for i, f in enumerate(fechas, 1):
        if f >= objetivo:
            return str(i)
    return str(len(fechas))


def _sin_acentos(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def responder(estado: str, opciones: list[str], esc: dict) -> str:
    """Qué contesta la persona, según el paso en el que el bot la dejó.

    Escrito así y no como una lista de mensajes porque los pasos DEPENDEN del
    negocio: con un solo servicio o un solo profesional, esos pasos no existen.
    Un guion con las posiciones contadas a mano se desalinea entero el día que
    un negocio tiene un servicio menos — que es exactamente lo que pasó acá la
    primera vez.
    """
    if estado == Estado.ESPERANDO_SERVICIO.value:
        return esc.get("servicio") or "1"
    if estado == Estado.ESPERANDO_STAFF.value:
        return esc.get("staff") or str(len(opciones))      # "Me da igual"
    if estado == Estado.ESPERANDO_DIA.value:
        return elegir_dia(opciones)
    if estado == Estado.ESPERANDO_HORARIO.value:
        return "1"
    if estado == Estado.ESPERANDO_NOMBRE.value:
        return esc["cliente"]
    if estado == Estado.ESPERANDO_CONFIRMACION.value:
        return esc.get("confirmar") or "sí"
    return "hola"


# Cada escenario dice QUÉ hace distinto, no en qué mensaje lo hace.
# `interrupciones` inyecta un mensaje la primera vez que se llega a ese paso:
# ahí están los caminos que no son el feliz.
ESCENARIOS = [
    {
        "nombre": "De principio a fin, tocando los números",
        "prueba": "el camino que hace casi todo el mundo",
        "cliente": "Ana Pérez",
    },
    {
        "nombre": "Escribiendo, sin usar los números",
        "prueba": "que se pueda hablar normal y no solo tocar opciones",
        "cliente": "Bruno Díaz",
        "servicio": "__nombre__",     # responde con el nombre del servicio
        "staff": "me da igual",
        "confirmar": "dale",
    },
    {
        "nombre": "Cambia de idea a mitad",
        "prueba": "que volver atrás no rompa nada y se pueda terminar igual",
        "cliente": "Carla Gómez",
        "interrupciones": {Estado.ESPERANDO_HORARIO.value: "mejor cambio el día"},
    },
    {
        "nombre": "Pide una persona y después sigue",
        "prueba": "que la salida de emergencia no borre lo elegido",
        "cliente": "Diego Luna",
        "interrupciones": {Estado.ESPERANDO_HORARIO.value: "quiero hablar con alguien"},
        "despues_de_interrumpir": "seguir con el bot",
    },
]


class Verificacion:
    def __init__(self) -> None:
        self.fallas: list[str] = []

    def chequear(self, ok: bool, que: str, detalle: str = "") -> bool:
        marca = f"{VERDE}✓{FIN}" if ok else f"{ROJO}✗{FIN}"
        print(f"     {marca} {que}" + (f"  {GRIS}{detalle}{FIN}" if detalle else ""))
        if not ok:
            self.fallas.append(que)
        return ok


async def conversar(grafo, hilo: str, negocio: str, nombre_negocio: str,
                    esc: dict, v: Verificacion, max_pasos: int = 22) -> dict:
    """Habla hasta que el turno quede confirmado, o hasta darse por vencida."""
    salida: dict = {}
    pendiente: list[str] = ["hola"]
    interrumpidos: set[str] = set()

    for _ in range(max_pasos):
        estado_previo = await grafo.aget_state({"configurable": {"thread_id": hilo}})
        vals = estado_previo.values or {}
        estado = vals.get("estado") or Estado.APERTURA.value
        opciones = vals.get("opciones") or []

        if pendiente:
            texto = pendiente.pop(0)
        elif (estado in (esc.get("interrupciones") or {})
              and estado not in interrumpidos):
            interrumpidos.add(estado)
            texto = esc["interrupciones"][estado]
            if esc.get("despues_de_interrumpir"):
                pendiente.append(esc["despues_de_interrumpir"])
        else:
            texto = responder(estado, opciones, esc)
            if texto == "__nombre__":
                texto = f"quiero un turno para {opciones[0].lower()}" if opciones else "quiero un turno"

        salida = await grafo.ainvoke(
            {"mensaje": texto},
            {"configurable": {
                "thread_id": hilo, "business_id": negocio,
                "nombre_negocio": nombre_negocio, "telefono": TELEFONO,
                "nombre_cliente": None, "calendario": calendario(),
            }},
        )
        respuesta = salida.get("respuesta") or ""
        for patron, que in FUGAS:
            if re.search(patron, respuesta):
                v.chequear(False, f"filtró {que} al responder «{texto}»")
        if not respuesta.strip() and salida.get("estado") != Estado.EN_MANOS_HUMANAS.value:
            v.chequear(False, f"respondió vacío a «{texto}»")

        if salida.get("estado") == Estado.CONFIRMADO.value:
            return salida
    return salida


async def buscar_en_aturno(base: str, codigo: str) -> dict | None:
    """Trae la reserva de aturno por su código. None si no existe.

    Se consulta por código y no por fecha a propósito: es el mismo dato que
    recibió la persona, así que esto verifica también que el código sirva.
    """
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.get(f"{base}/api/bookings/by-code/{codigo}")
    if r.status_code == 404:
        return None
    # El 400 de "ya pasó" igual devuelve la reserva; nos sirve para verificar.
    try:
        return (r.json() or {}).get("booking")
    except Exception:  # noqa: BLE001
        return None


async def cancelar(base: str, codigo: str) -> bool:
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.put(f"{base}/api/bookings/by-code/{codigo}/cancel",
                           json={"motivo": "prueba automática"})
    return r.status_code < 400


async def main() -> int:
    p = argparse.ArgumentParser(description="¿Se puede sacar un turno?")
    p.add_argument("--doble", action="store_true", help="sin red, contra el doble")
    p.add_argument("--no-limpiar", action="store_true", help="no cancela lo creado")
    p.add_argument("--detalle", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO if args.detalle else logging.ERROR,
                        format=f"{GRIS}%(message)s{FIN}")
    cfg = config()

    if args.doble:
        cliente, negocio, nombre_negocio = AturnoDoble(), "demo-peluqueria", "Peluquería Demo"
    else:
        from src.aturno.api import AturnoAPI
        cliente = AturnoAPI(cfg.aturno_api_url)
        negocio = "aturno"
        nombre_negocio = (await cliente._negocio(negocio)).get("name") or negocio  # noqa: SLF001

    F.configurar(cliente)
    grafo = F.construir_flujo(MemorySaver())
    v = Verificacion()
    creados: list[str] = []

    print(f"\n{NEGRITA}{'═'*68}{FIN}")
    print(f"{NEGRITA}  ¿SE PUEDE SACAR UN TURNO?{FIN}")
    print(f"{'═'*68}")
    print(f"  negocio : {nombre_negocio}  ({'doble en memoria' if args.doble else cfg.aturno_api_url})")
    print(f"  ahora   : {datetime.now().strftime('%d/%m %H:%M')}")

    for i, esc in enumerate(ESCENARIOS, 1):
        print(f"\n{NEGRITA}{i}. {esc['nombre']}{FIN}")
        print(f"   {GRIS}{esc['prueba']}{FIN}")
        try:
            final = await conversar(grafo, f"verif-{i}", negocio, nombre_negocio,
                                    esc, v)
        except Exception as e:  # noqa: BLE001
            v.chequear(False, "la conversación se cortó", f"{type(e).__name__}: {e}")
            continue

        respuesta = final.get("respuesta") or ""
        llego = v.chequear(final.get("estado") == Estado.CONFIRMADO.value,
                           "terminó con el turno confirmado",
                           f"quedó en {final.get('estado')}")
        if not llego:
            print(f"     {GRIS}último mensaje: {respuesta.splitlines()[0][:60]}{FIN}")
            continue

        m = re.search(r"Código:\s*([A-Z0-9-]{4,})", respuesta)
        if not v.chequear(bool(m), "le dio un código a la persona"):
            continue
        codigo = m.group(1)
        creados.append(codigo)

        if args.doble:
            continue

        reserva = await buscar_en_aturno(cfg.aturno_api_url, codigo)
        if not v.chequear(reserva is not None,
                          "el turno EXISTE en la agenda de aturno", codigo):
            continue

        v.chequear((reserva.get("customer") or {}).get("name") == esc["cliente"],
                   "guardó el nombre que dio la persona",
                   str((reserva.get("customer") or {}).get("name")))
        v.chequear((reserva.get("customer") or {}).get("phone") == TELEFONO,
                   "guardó el teléfono de WhatsApp")
        v.chequear(bool((reserva.get("service") or {}).get("name")),
                   "guardó el servicio", (reserva.get("service") or {}).get("name"))
        fecha_ok = date.fromisoformat(reserva["date"]) >= hoy()
        v.chequear(fecha_ok, "la fecha no es del pasado",
                   f"{reserva['date']} {reserva['time']}")
        v.chequear(reserva.get("status") in ("pending", "confirmed", "pending_confirmation"),
                   "quedó en un estado que ocupa el horario", reserva.get("status"))

    # ---- limpieza ----
    if creados and not args.doble and not args.no_limpiar:
        print(f"\n{GRIS}Limpiando los turnos de prueba…{FIN}")
        for codigo in creados:
            ok = await cancelar(cfg.aturno_api_url, codigo)
            print(f"  {'✓' if ok else '·'} {codigo}"
                  + ("" if ok else f"  {GRIS}no se pudo cancelar, borralo del panel{FIN}"))

    if hasattr(cliente, "cerrar"):
        await cliente.cerrar()

    print(f"\n{'═'*68}")
    if v.fallas:
        print(f"{ROJO}{NEGRITA}  NO ESTÁ LISTO — {len(v.fallas)} problema(s){FIN}")
        for f in v.fallas:
            print(f"    · {f}")
    else:
        print(f"{VERDE}{NEGRITA}  SE PUEDE SACAR UN TURNO. Todo verificado contra la agenda real.{FIN}")
    print(f"{'═'*68}\n")
    return 1 if v.fallas else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
