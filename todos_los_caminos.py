"""
todos_los_caminos.py — Cada camino que puede tomar una persona, y qué ve.

PARA QUÉ
--------
Las pruebas que ya existen verifican que el camino principal funcione. Esto es
otra cosa: recorre TODAS las bifurcaciones —contestar bien, contestar mal,
preguntar, pedir una persona, cambiar de idea, cancelar, callarse, insistir— y
muestra el texto exacto que recibe la persona en cada una.

Sirve para leer la experiencia completa de una sentada, que es la única forma
de notar que dos respuestas se contradicen o que un callejón no tiene salida.

Además chequea, en cada mensaje, cuatro cosas que valen para TODOS los caminos:

    · nunca queda sin respuesta (salvo el silencio a propósito)
    · nunca sale un JSON, un id interno ni un error de programa
    · nunca es una pared de texto
    · siempre dice qué hacer después

El último es el que más se rompe: una respuesta correcta que no dice cómo
seguir deja a la persona mirando la pantalla.

    python todos_los_caminos.py            # contra el doble, sin red
    python todos_los_caminos.py --real     # contra aturno de verdad
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re

from langgraph.checkpoint.memory import MemorySaver

from src.agentes import flujo as F
from src.agentes.estados import Estado
from src.aturno.doble import AturnoDoble
from src.config import config
from src.fechas import calendario

TEL = "+5491130032002"
VERDE, ROJO, AMBAR, GRIS, AZUL, NEGRITA, FIN = (
    "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[36m", "\033[1m", "\033[0m")

FUGAS = [
    (r'\{"', "JSON"), (r"\bsvc-[a-z]+|\bp-[a-z]+\b", "un id interno"),
    (r"Traceback|Exception", "un error de programa"), (r"\bNone\b", "None"),
    (r"Clasificás mensajes|Reglas de extracción", "el prompt"),
]

# Cómo saber si una respuesta le dice a la persona qué hacer. No alcanza con
# que termine en signo de pregunta: "Listo, turno confirmado" es un final
# legítimo y no pide nada.
PIDE_ALGO = re.compile(
    r"respond[eé]|escribime|deci?me|pedime|confirm|eleg[ií]|quer[eé]s|"
    r"\?|necesito|avisame|segu[ií]mos", re.I)
FINALES = ("turno confirmado", "le avisé a", "sigo yo")


# ══════════════════════════════════════════════════════════════════
# Los caminos. Cada uno es un grupo con lo que la persona escribe.
# ══════════════════════════════════════════════════════════════════

CAMINOS = {
    "Entrada": {
        "Saluda": ["hola"],
        "Escribe cualquier cosa como primer mensaje": ["asdkjhasd"],
        "Pide un turno directo, sin saludar": ["quiero un turno"],
        "Pregunta antes de saludar": ["¿cuánto sale un corte?"],
        "Manda un emoji solo": ["👋"],
    },
    "Elegir servicio": {
        "Por número": ["hola", "1"],
        "Por nombre": ["hola", "corte de pelo"],
        "Nombre parcial": ["hola", "coloración"],
        "Número que no existe": ["hola", "9"],
        "Algo que no es un servicio": ["hola", "quiero comprar shampoo"],
        "Pregunta en vez de elegir": ["hola", "¿cuánto sale la coloración?"],
    },
    "Elegir profesional": {
        "Por número": ["hola", "1", "1"],
        "Por nombre": ["hola", "1", "Lean"],
        "Le da igual": ["hola", "1", "me da igual"],
        "Pide a alguien que no hace ese servicio": ["hola", "2", "Lean"],
        "Pide a alguien que no existe": ["hola", "1", "con Roberto"],
    },
    "Elegir día": {
        "Por número": ["hola", "1", "3", "1"],
        "Por nombre del día": ["hola", "1", "3", "el jueves"],
        "Mañana": ["hola", "1", "3", "mañana"],
        "Un día cerrado": ["hola", "1", "3", "el domingo"],
        "Un día del pasado": ["hola", "1", "3", "ayer"],
        "Muy lejos en el futuro": ["hola", "1", "3", "el 25 de diciembre"],
    },
    "Elegir horario": {
        "Por número": ["hola", "1", "3", "1", "1"],
        "Por hora exacta": ["hola", "1", "3", "1", "a las 10:00"],
        "Hora que no existe en la grilla": ["hola", "1", "3", "1", "10:07"],
        "Hora fuera del horario": ["hola", "1", "3", "1", "a las 4 de la mañana"],
        "Pide más horarios": ["hola", "1", "3", "1", "más"],
    },
    "Nombre y confirmación": {
        "Da el nombre y confirma": ["hola", "1", "3", "1", "1", "Ana Pérez", "sí"],
        "Corrige el nombre en la confirmación":
            ["hola", "1", "3", "1", "1", "Ana Pérez", "es para Sofía Ramírez", "sí"],
        "Dice que no en la confirmación": ["hola", "1", "3", "1", "1", "Ana Pérez", "no"],
        "Manda algo raro como nombre": ["hola", "1", "3", "1", "1", "xd"],
    },
    "Cambiar de idea": {
        "Vuelve al servicio desde el día": ["hola", "1", "3", "1", "mejor cambio el servicio"],
        "Cambia el día desde el horario": ["hola", "1", "3", "1", "1", "mejor otro día"],
        "Cancela todo": ["hola", "1", "3", "cancelá todo"],
        "Saluda a mitad de camino": ["hola", "1", "3", "hola"],
    },
    "Preguntar cosas": {
        "Pregunta el horario": ["hola", "¿a qué hora abren?"],
        "Pregunta algo que no está cargado": ["hola", "¿tienen estacionamiento?"],
        "Pregunta a mitad de una reserva": ["hola", "1", "3", "¿dónde quedan?"],
        "Pide el link de la web": ["hola", "mandame el link"],
    },
    "Pedir una persona": {
        "Lo pide de entrada": ["hola", "quiero hablar con alguien"],
        "Lo pide a mitad": ["hola", "1", "3", "1", "quiero hablar con una persona"],
        "Escribe mientras espera": ["hola", "1", "quiero hablar con alguien", "hola? están?"],
        "Vuelve al bot": ["hola", "1", "quiero hablar con alguien", "seguir con el bot"],
    },
    "Después de reservar": {
        "Agradece": ["hola", "1", "3", "1", "1", "Ana Pérez", "sí", "gracias"],
        "Saca otro turno": ["hola", "1", "3", "1", "1", "Ana Pérez", "sí", "quiero otro turno"],
        "Pregunta después de reservar":
            ["hola", "1", "3", "1", "1", "Ana Pérez", "sí", "¿a qué hora abren?"],
    },
    "Se traba": {
        "No entiende dos veces": ["hola", "1", "asdkjhasd", "qwertyuiop"],
        "Contesta con espacios": ["hola", "1", "   ", "..."],
        "Repite lo mismo tres veces": ["hola", "1", "no sé", "no sé", "no sé"],
    },
}


class Revision:
    def __init__(self) -> None:
        self.problemas: list[str] = []

    def anotar(self, camino: str, que: str) -> None:
        self.problemas.append(f"{camino}: {que}")


async def recorrer(grafo, negocio, nombre_negocio, camino, guion, rev, mostrar):
    hilo = F.hilo_de(negocio, f"{TEL}-{abs(hash(camino)) % 99999}")
    cfg = {"configurable": {
        "thread_id": hilo, "business_id": negocio, "nombre_negocio": nombre_negocio,
        "telefono": TEL, "nombre_cliente": None, "calendario": calendario()}}

    if mostrar:
        print(f"\n  {AZUL}▸ {camino}{FIN}")
    for m in guion:
        try:
            salida = await grafo.ainvoke({"mensaje": m}, cfg)
        except Exception as e:  # noqa: BLE001
            rev.anotar(camino, f"se cayó con «{m}»: {type(e).__name__}: {e}")
            if mostrar:
                print(f"    {ROJO}✗ se cayó con «{m}»{FIN}")
            return
        r = (salida.get("respuesta") or "").strip()
        estado = salida.get("estado")

        if mostrar:
            print(f"    {GRIS}«{m}»{FIN}")
            if r:
                for linea in r.split("\n")[:6]:
                    print(f"      {linea}")
                if len(r.split("\n")) > 6:
                    print(f"      {GRIS}…{FIN}")
            else:
                print(f"      {GRIS}(sin respuesta, a propósito){FIN}")

        # ---- lo que vale para todos los caminos ----
        if not r and estado != Estado.EN_MANOS_HUMANAS.value:
            rev.anotar(camino, f"no contestó nada a «{m}»")
        for patron, que in FUGAS:
            if re.search(patron, r):
                rev.anotar(camino, f"filtró {que} al responder «{m}»")
        if len(r) > 1200:
            rev.anotar(camino, f"contestó {len(r)} caracteres a «{m}»")
        if r and not PIDE_ALGO.search(r) and not any(f in r.lower() for f in FINALES):
            rev.anotar(camino, f"no dice qué hacer después, tras «{m}»")


async def main() -> int:
    p = argparse.ArgumentParser(description="Todos los caminos posibles.")
    p.add_argument("--real", action="store_true", help="contra aturno de verdad")
    p.add_argument("--callado", action="store_true", help="solo el resumen")
    p.add_argument("--grupo", help="recorrer un solo grupo")
    args = p.parse_args()
    logging.basicConfig(level=logging.ERROR)

    if args.real:
        from src.aturno.api import AturnoAPI
        cliente = AturnoAPI(config().aturno_api_url)
        negocio = "aturno"
        nombre = await cliente.nombre_visible(negocio) or negocio
    else:
        cliente, negocio, nombre = AturnoDoble(), "demo-peluqueria", "Peluquería Demo"

    F.configurar(cliente)
    grafo = F.construir_flujo(MemorySaver())
    rev = Revision()

    print(f"\n{NEGRITA}{'═' * 68}")
    print(f"  TODOS LOS CAMINOS · {nombre}")
    print(f"{'═' * 68}{FIN}")

    total = 0
    for grupo, caminos in CAMINOS.items():
        if args.grupo and args.grupo.lower() not in grupo.lower():
            continue
        print(f"\n{NEGRITA}{grupo}{FIN}  {GRIS}({len(caminos)} caminos){FIN}")
        for camino, guion in caminos.items():
            antes = len(rev.problemas)
            await recorrer(grafo, negocio, nombre, camino, guion, rev, not args.callado)
            total += 1
            if args.callado:
                marca = f"{VERDE}✓{FIN}" if len(rev.problemas) == antes else f"{ROJO}✗{FIN}"
                print(f"  {marca} {camino}")

    if hasattr(cliente, "cerrar"):
        await cliente.cerrar()

    print(f"\n{'═' * 68}")
    if rev.problemas:
        print(f"{AMBAR}{NEGRITA}  {len(rev.problemas)} cosa(s) para mirar, en {total} caminos{FIN}\n")
        for x in dict.fromkeys(rev.problemas):
            print(f"  · {x}")
    else:
        print(f"{VERDE}{NEGRITA}  Los {total} caminos dan una respuesta coherente.{FIN}")
    print(f"{'═' * 68}\n")
    return 1 if rev.problemas else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
