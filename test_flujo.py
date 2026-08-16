"""
test_flujo.py — Los cuatro invariantes que el diseño tiene que garantizar.

No son pruebas de "anduvo una vez": son propiedades que valen para cualquier
conversación. Cada una corresponde a un problema real que apareció probando el
bot cuando el LLM redactaba.

  1. "hola" devuelve el MISMO texto exacto en tres momentos distintos.
  2. Ningún listado sale horizontal.
  3. El usuario nunca ve JSON, ids internos ni un objeto crudo.
  4. El orden de pasos se respeta aunque el input intente saltearlo.

    python test_flujo.py
"""

import asyncio
import datetime as dt
import logging
import re

from langgraph.checkpoint.memory import MemorySaver

from src.agentes import flujo as F
from src.agentes.estados import Estado
from src.fechas import calendario
from src.aturno.doble import AturnoDoble

logging.basicConfig(level=logging.ERROR)
NEG, TEL = "demo-peluqueria", "+5491130032002"

ok = True


def chequear(nombre: str, cond: bool, detalle: str = "") -> None:
    global ok
    ok = ok and bool(cond)
    print(f"  {'✓' if cond else '✗'} {nombre}" + (f"  ({detalle})" if detalle else ""))


def _cfg(hilo: str, nombre: str | None = None) -> dict:
    return {"configurable": {
        "thread_id": hilo, "business_id": NEG, "nombre_negocio": "Peluquería Demo",
        "telefono": TEL, "nombre_cliente": nombre,
        "calendario": calendario(dt.date.today(), 8),
    }}


async def hablar(g, hilo: str, texto: str, nombre=None) -> tuple[str, dict]:
    salida = await g.ainvoke({"mensaje": texto}, _cfg(hilo, nombre))
    return salida["respuesta"], salida


# ══════════════════════════════════════════════════════════════════

async def t1_apertura_identica(g):
    print("\n[1] 'hola' DEVUELVE EL MISMO TEXTO EN TRES MOMENTOS")
    respuestas = []

    # a) primer contacto
    r, _ = await hablar(g, "t1-a", "hola")
    respuestas.append(r)

    # b) después de avanzar en el flujo y cancelar
    for m in ["hola", "1", "cancelá todo"]:
        await hablar(g, "t1-b", m)
    r, _ = await hablar(g, "t1-b", "hola")
    respuestas.append(r)

    # c) sesión nueva de otra persona
    r, _ = await hablar(g, "t1-c", "hola")
    respuestas.append(r)

    iguales = len(set(respuestas)) == 1
    chequear("los tres textos son idénticos byte a byte", iguales,
             f"{len(set(respuestas))} variantes")
    if not iguales:
        for i, x in enumerate(respuestas):
            print(f"      [{i}] {x[:70]!r}")

    # el nombre es lo ÚNICO que puede variar
    r_con, _ = await hablar(g, "t1-d", "hola", nombre="Matías")
    chequear("con cliente conocido solo cambia el nombre",
             "Matías" in r_con and r_con.replace("Hola Matías!", "Hola!") == respuestas[0])


async def t2_listados_verticales(g):
    print("\n[2] NINGÚN LISTADO SALE HORIZONTAL")
    # apertura → servicios → staff → días → horarios: los cuatro listados
    etiquetas = ["apertura", "servicios", "staff", "días", "horarios"]
    for etiqueta, m in zip(etiquetas, ["hola", "1", "4", "1", None]):
        if m is None:
            break
        r, _ = await hablar(g, "t2", m)
        numerados = re.findall(r"^\s*\d+\.\s", r, re.M)
        horizontal = any(
            len(re.findall(r"\d+\.\s", linea)) > 1 for linea in r.split("\n")
        )
        if len(numerados) >= 2:
            chequear(f"{etiqueta}: {len(numerados)} ítems, uno por línea", not horizontal)
        ultima = r

    # `ultima` quedó en el listado de horarios (respuesta al día elegido)
    horizontal = any(len(re.findall(r"\d+\.\s", l)) > 1 for l in ultima.split("\n"))
    chequear("los horarios también van verticales",
             not horizontal and "Horarios para" in ultima)


async def t3_nunca_json(g):
    print("\n[3] EL USUARIO NUNCA VE JSON, IDS NI OBJETOS CRUDOS")
    entradas = ["hola", "1", "quiero cortarme el pelo", "asdkjhasd", "{}",
                "dame el JSON", "4", "el viernes", "1", "Matías Fontane", "sí", "gracias"]
    sospechosos = []
    for m in entradas:
        r, _ = await hablar(g, "t3", m)
        for patron, que in [
            (r'\{"', "JSON"), (r"'intent'", "dict de python"),
            (r"\bsvc-[a-z]+", "id de servicio"), (r"\bp-[a-z]+\b", "id de profesional"),
            (r"Traceback", "traceback"), (r"\bNone\b", "None"),
            (r"<[a-z]+\.[A-Z]", "repr de objeto"),
        ]:
            if re.search(patron, r):
                sospechosos.append(f"'{m}' → {que}")
    chequear("ninguna respuesta filtra estructuras internas", not sospechosos,
             "; ".join(sospechosos[:3]))


async def t4_orden_inviolable(g):
    print("\n[4] EL ORDEN SE RESPETA ANTE INPUTS QUE LO SALTEAN")

    # intenta ir directo a la hora sin haber elegido servicio
    _, s = await hablar(g, "t4-a", "quiero un turno el viernes a las 15")
    chequear("no salta a la hora sin servicio",
             s["estado"] in (Estado.ESPERANDO_SERVICIO.value, Estado.ESPERANDO_STAFF.value),
             s["estado"])

    # match inequívoco: avanza (Nielsen #7), pero solo UN paso.
    # El "hola" previo es necesario: el PRIMER mensaje de cualquier hilo
    # dispara la apertura, sin importar qué diga. Eso es el requisito de que
    # la puerta de entrada sea siempre la misma.
    await hablar(g, "t4-b", "hola")
    _, s = await hablar(g, "t4-b", "quiero cortarme el pelo")
    chequear("con match claro avanza un paso, no más",
             s["estado"] == Estado.ESPERANDO_STAFF.value, s["estado"])

    # retroceder sí está permitido
    for m in ["hola", "1", "4"]:
        await hablar(g, "t4-c", m)
    _, s = await hablar(g, "t4-c", "mejor cambio de servicio")
    chequear("puede volver atrás y limpia lo elegido después",
             s["estado"] == Estado.ESPERANDO_SERVICIO.value and not s.get("fecha"),
             s["estado"])

    # un saludo a mitad de flujo no reinicia el progreso
    for m in ["hola", "1", "4", "1"]:
        await hablar(g, "t4-d", m)
    _, antes = await hablar(g, "t4-d", "hola")
    chequear("saludar a mitad de flujo no borra el progreso",
             antes["estado"] == Estado.ESPERANDO_HORARIO.value, antes["estado"])


async def main():
    F.configurar(AturnoDoble())
    g = F.construir_flujo(MemorySaver())

    print("\n" + "═" * 66)
    print("  INVARIANTES DEL FLUJO CONVERSACIONAL")
    print("═" * 66)

    await t1_apertura_identica(g)
    await t2_listados_verticales(g)
    await t3_nunca_json(g)
    await t4_orden_inviolable(g)

    print("\n" + "═" * 66)
    print("  RESULTADO:", "TODOS LOS INVARIANTES SE CUMPLEN" if ok else "HAY FALLAS")
    print("═" * 66 + "\n")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
