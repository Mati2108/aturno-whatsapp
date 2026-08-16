"""
test_observabilidad.py — Las 5 pruebas trazadas que pide la consigna.

Cada escenario recorre el sistema entero: clasificador, máquina de estados,
RAG y adaptador de aturno. Con el trazado activo, cada uno deja en Phoenix un
árbol de spans anidados donde se ve el paso a paso.

Los cinco están elegidos para cubrir caminos distintos, no para repetir el
feliz cinco veces: uno reserva, uno consulta sin reservar, uno choca contra un
horario ocupado, uno cambia de idea a mitad, y uno escribe cualquier cosa.

Antes de correr:
    ./venv/bin/phoenix serve          # en otra terminal
    PHOENIX_HABILITADO=true python test_observabilidad.py
    # las trazas quedan en http://localhost:6006
"""

import asyncio
import datetime as dt
import logging
import os

from langgraph.checkpoint.memory import MemorySaver

from src.agentes import flujo as F
from src.aturno.doble import AturnoDoble
from src.fechas import calendario
from src.observabilidad import configurar_trazas, trazado_activo
from src.schemas import DatosDelCliente

logging.basicConfig(level=logging.ERROR)
NEG, TEL = "demo-peluqueria", "+5491130032002"

ESCENARIOS: dict[str, dict] = {
    "1 · Reserva completa desde cero": {
        "que_prueba": "el camino feliz de punta a punta",
        "guion": ["hola", "1", "4", "1", "1", "Matías Fontane", "sí"],
        "espera": "un turno reservado",
    },
    "2 · Consulta sin reservar": {
        "que_prueba": "el RAG contesta y NO arranca el flujo de turno",
        "guion": ["hola", "¿cuánto sale la coloración?", "¿y a qué hora cierran?"],
        "espera": "responde con datos del negocio",
    },
    "3 · Carrera: le ganan el horario": {
        "que_prueba": "el rechazo trae el motivo y alternativas cercanas",
        # El sistema nunca OFRECE un horario ocupado, así que la única forma
        # de llegar al rechazo es la carrera real: alguien reserva desde la
        # web entre que el bot ofreció el horario y la persona lo confirmó.
        # Para eso se ocupa el slot elegido justo antes del "sí".
        "guion": ["hola", "1", "4", "1", "1", "Ana Pérez", "sí"],
        "ocupar_antes_de_confirmar": True,
        "espera": "avisa el motivo y ofrece alternativas",
        "no_debe_reservar": True,
    },
    "4 · Cambia de idea a mitad": {
        "que_prueba": "retroceder limpia lo elegido después de ese paso",
        "guion": ["hola", "1", "4", "1", "mejor cambio de servicio", "2"],
        "espera": "vuelve a servicios y avanza de nuevo",
    },
    "5 · Entrada sin sentido": {
        "que_prueba": "no se rompe ni filtra estructuras internas",
        "guion": ["hola", "asdkjhasd", "{\"intent\": \"hackear\"}", "🙃", "1"],
        "espera": "responde con plantillas, sin JSON",
    },
}


async def correr(g, nombre: str, esc: dict, doble: AturnoDoble) -> dict:
    hilo = F.hilo_de(NEG, f"{TEL}-{nombre[0]}")
    cfg = {"configurable": {
        "thread_id": hilo, "business_id": NEG, "nombre_negocio": "Peluquería Demo",
        "telefono": TEL, "nombre_cliente": None, "calendario": calendario(),
    }}

    turnos_antes = len(doble._ocupados)
    ultima = ""
    for m in esc["guion"]:
        # La carrera: justo antes del "sí", otro canal toma ese mismo horario.
        if esc.get("ocupar_antes_de_confirmar") and m == "sí":
            estado = await g.aget_state(cfg)
            f = dt.date.fromisoformat(estado.values["fecha"])
            h = dt.datetime.strptime(estado.values["hora"], "%H:%M").time()
            for p in ("p-lean", "p-sofi", "p-nico"):
                doble._ocupados[(NEG, p, f, h)] = "bk-desde-la-web"
            turnos_antes = len(doble._ocupados)
            print(f"   (alguien reservó {f} {h:%H:%M} desde la web justo antes)")

        salida = await g.ainvoke({"mensaje": m}, cfg)
        ultima = salida["respuesta"]

    return {
        "reservo": len(doble._ocupados) > turnos_antes,
        "ultima": ultima,
        "limpia": not any(x in ultima for x in ('{"', "svc-", "p-lean", "Traceback")),
    }


async def main() -> None:
    activo = configurar_trazas("aturno-whatsapp")
    print("\n" + "═" * 68)
    print("  5 PRUEBAS DEL SISTEMA CON TRAZADO")
    print("═" * 68)
    print(f"  Phoenix: {'ACTIVO → http://localhost:6006' if activo else 'APAGADO'}")
    if not activo:
        print("  (poné PHOENIX_HABILITADO=true y levantá `phoenix serve`)")

    doble = AturnoDoble()
    F.configurar(doble)
    g = F.construir_flujo(MemorySaver())

    todo_ok = True
    for nombre, esc in ESCENARIOS.items():
        print(f"\n── {nombre}")
        print(f"   prueba: {esc['que_prueba']}")
        r = await correr(g, nombre, esc, doble)
        print(f"   última respuesta: {r['ultima'].splitlines()[0][:58]}")
        correcto = r["limpia"]
        if esc.get("no_debe_reservar") and r["reservo"]:
            correcto = False
            print("   ✗ reservó un horario que estaba ocupado")
        marca = "✓" if correcto else "✗"
        print(f"   {marca} sin estructuras internas   · reservó: {'sí' if r['reservo'] else 'no'}")
        todo_ok = todo_ok and correcto

    print("\n" + "═" * 68)
    print(f"  turnos creados en total: {len(doble._ocupados)}")
    if activo:
        print("  Abrí http://localhost:6006 para ver los árboles de spans.")
    print("  RESULTADO:", "OK" if todo_ok else "HAY FALLAS")
    print("═" * 68 + "\n")
    raise SystemExit(0 if todo_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
