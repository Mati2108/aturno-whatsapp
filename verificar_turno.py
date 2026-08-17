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
    """Responde el número del primer día que esté al menos DIAS_ADELANTE.

    Si las opciones no son fechas, el guion se desalineó con el flujo — pasa
    apenas se agrega o se saltea un paso. El mensaje lo dice en vez de reventar
    con un "Invalid isoformat string: 'Juan Demo'", que fue exactamente lo que
    salió la primera vez que corrió esto.
    """
    try:
        fechas = [date.fromisoformat(o) for o in opciones]
    except ValueError:
        raise AssertionError(
            "el guion esperaba el paso del DÍA y el bot está mostrando otra "
            f"cosa: {opciones[:4]}"
        ) from None
    objetivo = hoy() + timedelta(days=DIAS_ADELANTE)
    for i, f in enumerate(fechas, 1):
        if f >= objetivo:
            return str(i)
    return str(len(fechas))       # no hay ninguno tan lejos: el último que haya


# El orden real del flujo es: servicio → profesional → día → horario → nombre →
# confirmación. Cualquier guion que se saltee un paso desalinea todo lo que
# sigue, así que están escritos completos.
ESCENARIOS = [
    {
        "nombre": "Por números, de principio a fin",
        "prueba": "el camino que hace casi todo el mundo",
        "cliente": "Ana Pérez",
        "guion": ["hola", "1", "3", elegir_dia, "1", "Ana Pérez", "sí"],
    },
    {
        "nombre": "Escribiendo, sin usar los números",
        "prueba": "que se pueda hablar normal y no solo tocar opciones",
        "cliente": "Bruno Díaz",
        "guion": ["hola", "quiero un turno con el dentista", "me da igual",
                  elegir_dia, "1", "Bruno Díaz", "dale"],
    },
    {
        "nombre": "Cambia de idea a mitad",
        "prueba": "que volver atrás no rompa nada y se pueda terminar igual",
        "cliente": "Carla Gómez",
        "guion": ["hola", "1", "3", elegir_dia, "mejor cambio el día",
                  elegir_dia, "1", "Carla Gómez", "sí"],
    },
    {
        "nombre": "Pide una persona y después sigue",
        "prueba": "que la salida de emergencia no borre lo elegido",
        "cliente": "Diego Luna",
        "guion": ["hola", "1", "3", elegir_dia, "quiero hablar con alguien",
                  "1", "Diego Luna", "sí"],
    },
]


# ══════════════════════════════════════════════════════════════════

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
                    guion: list, v: Verificacion) -> dict:
    """Corre la conversación entera y devuelve el estado final."""
    salida: dict = {}
    for paso in guion:
        estado_previo = await grafo.aget_state({"configurable": {"thread_id": hilo}})
        opciones = (estado_previo.values or {}).get("opciones") or []
        texto = paso(opciones) if callable(paso) else paso

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
        if not respuesta.strip():
            v.chequear(False, f"respondió vacío a «{texto}»")
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
                                    esc["guion"], v)
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
