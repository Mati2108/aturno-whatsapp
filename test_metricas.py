"""
test_metricas.py — El instrumento de medición, calibrado contra un patrón.

POR QUÉ ESTE ARCHIVO ES EL QUE DECIDE SI EL PASO 2 SIRVE
--------------------------------------------------------
Un número que nadie verificó es PEOR que ningún número, porque se toman
decisiones con él y se le muestra a un cliente. "El bot te resuelve el 70%" es
una afirmación sobre el negocio de otra persona: si el 70% está mal calculado,
la mentira es tuya.

Los instrumentos de medición no se prueban mirándolos: se calibran contra un
patrón conocido. Acá se arman conversaciones de forma EXACTA —tantas que
reservan, tantas que abandonan en tal paso, tantas que escalan— y después se
afirma que `/metricas` devuelve exactamente esos números. No aproximados.

Si este archivo no existe, las métricas no están terminadas.

QUÉ NECESITA
------------
Un Postgres al que escribir. Usa el mismo de `DATABASE_URL` pero con un
`business_id` propio y sorteado, y borra sus filas al terminar: no ensucia los
números reales ni depende de que la base esté vacía.

    python test_metricas.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import timedelta

from src import metricas as M
from src.config import config
from src.fechas import ahora

logging.basicConfig(level=logging.ERROR)

VERDE, ROJO, GRIS, NEGRITA, FIN = "\033[32m", "\033[31m", "\033[90m", "\033[1m", "\033[0m"

# Un negocio que no existe, con sufijo del proceso: dos corridas en paralelo no
# se pisan, y ninguna toca los datos de un negocio de verdad.
NEG = f"test-metricas-{os.getpid()}"

ok = True


def chequear(nombre: str, cond: bool, detalle: str = "") -> None:
    global ok
    ok = ok and bool(cond)
    color = VERDE if cond else ROJO
    print(f"  {color}{'✓' if cond else '✗'}{FIN} {nombre}"
          + (f"{GRIS}  ({detalle}){FIN}" if detalle else ""))


def igual(nombre: str, obtenido, esperado) -> None:
    """Igualdad exacta. En un instrumento, «cerca» no existe."""
    chequear(nombre, obtenido == esperado, f"dio {obtenido!r}, esperaba {esperado!r}")


# ══════════════════════════════════════════════════════════════════
#  EL PATRÓN
#
#  Diez conversaciones de forma conocida. Los números que tienen que
#  salir están calculados a mano acá abajo, no leídos del programa.
# ══════════════════════════════════════════════════════════════════

VIEJO = 3          # horas: más que el vencimiento de sesión → abandonada
RECIEN = 0.05      # horas: sigue viva


async def sembrar() -> None:
    """5 reservan · 2 escalan · 2 abandonan · 1 sigue escribiendo."""
    t = ahora()

    for i in range(5):
        h = f"{NEG}:reserva-{i}"
        for _ in range(6):                      # 6 mensajes hasta reservar
            await M.registrar(h, NEG, "esperando_servicio", cuando=t - timedelta(hours=VIEJO))
        await M.registrar(h, NEG, "confirmado", desenlace="reservado",
                          cuando=t - timedelta(hours=VIEJO))

    for i in range(2):
        h = f"{NEG}:escala-{i}"
        await M.registrar(h, NEG, "esperando_dia", cuando=t - timedelta(hours=VIEJO))
        await M.registrar(h, NEG, "en_manos_humanas", desenlace="escalado",
                          cuando=t - timedelta(hours=VIEJO))

    # Abandonan: se van sin cerrar, y hace rato. Una en el horario y otra en el
    # servicio, para que el desglose por paso tenga algo que distinguir.
    for paso in ("esperando_horario", "esperando_servicio"):
        await M.registrar(f"{NEG}:abandona-{paso}", NEG, paso,
                          cuando=t - timedelta(hours=VIEJO))

    # Y una que está escribiendo ahora mismo. No es abandono todavía.
    await M.registrar(f"{NEG}:en-curso", NEG, "esperando_dia",
                      cuando=t - timedelta(hours=RECIEN))


# ══════════════════════════════════════════════════════════════════

async def t1_los_numeros(r: dict):
    print(f"\n{NEGRITA}[1] LOS NÚMEROS, CONTRA EL PATRÓN{FIN}")
    print(f"{GRIS}  5 reservan · 2 escalan · 2 abandonan · 1 en curso.{FIN}")

    # La que sigue viva NO se cuenta en el denominador: todavía no terminó, y
    # meterla como fracaso castiga al bot por conversaciones que puede ganar.
    igual("cerradas = 9 (la que sigue viva no cuenta)", r["cerradas"], 9)
    igual("en curso = 1", r["en_curso"], 1)

    igual("reservadas = 5", r["reservadas"], 5)
    igual("escaladas = 2", r["escaladas"], 2)
    igual("abandonadas = 2", r["abandonadas"], 2)

    igual("containment = 5/9", r["containment"], round(5 / 9, 4))
    igual("escalación = 2/9", r["escalacion"], round(2 / 9, 4))

    chequear("las tres tajadas suman 1",
             abs(r["containment"] + r["escalacion"] + r["abandono"] - 1.0) < 1e-9,
             f"{r['containment']} + {r['escalacion']} + {r['abandono']}")


async def t2_donde_se_caen(r: dict):
    print(f"\n{NEGRITA}[2] EN QUÉ PASO SE CAEN{FIN}")
    print(f"{GRIS}  El número que dice QUÉ arreglar, no sólo que algo anda mal.{FIN}")

    caidas = r["abandono_por_paso"]
    igual("una se cayó eligiendo el horario", caidas.get("esperando_horario"), 1)
    igual("y otra eligiendo el servicio", caidas.get("esperando_servicio"), 1)
    chequear("las reservadas NO figuran como caídas",
             "confirmado" not in caidas, str(caidas))
    chequear("ni las escaladas", "en_manos_humanas" not in caidas, str(caidas))
    igual("y el desglose suma el total de abandonos", sum(caidas.values()), 2)


async def t3_los_turnos(r: dict):
    print(f"\n{NEGRITA}[3] CUÁNTOS MENSAJES CUESTA UN TURNO{FIN}")
    print(f"{GRIS}  «12 turnos para sacar un turno es fallar, aunque suene natural».{FIN}")

    igual("mediana de mensajes hasta reservar = 7", r["turnos_hasta_reservar"], 7)
    chequear("y sólo mira las que reservaron",
             r["turnos_hasta_reservar"] is not None)


async def t4_no_tumba_el_bot():
    print(f"\n{NEGRITA}[4] SI LA MÉTRICA SE CAE, EL BOT CONTESTA IGUAL{FIN}")
    print(f"{GRIS}  Regla del repo: lo secundario no puede tumbar lo principal.{FIN}")

    original = M._URL
    M._URL = "postgresql://nadie@127.0.0.1:1/no-existe"
    M.olvidar_pool()
    try:
        await M.registrar("hilo-x", NEG, "esperando_dia")
        chequear("registrar con la base caída no levanta excepción", True)
        vacio = await M.resumen(NEG)
        chequear("y el resumen devuelve algo usable, no un error",
                 isinstance(vacio, dict) and vacio.get("cerradas") == 0, str(vacio)[:60])
    except Exception as e:  # noqa: BLE001
        chequear(f"NO tenía que levantar: {type(e).__name__}", False, str(e)[:80])
    finally:
        M._URL = original
        M.olvidar_pool()


async def t5_no_guarda_telefonos():
    print(f"\n{NEGRITA}[5] NO SE GUARDA NINGÚN TELÉFONO{FIN}")
    print(f"{GRIS}  Pecado 7: pedir o guardar más datos de los necesarios.{FIN}")

    await M.registrar(f"{NEG}:+5491130032002", NEG, "esperando_dia")
    crudo = await M.volcado(NEG)
    chequear("ninguna fila contiene un número de teléfono",
             not any("+549" in str(f) for f in crudo), str(crudo)[:80])
    chequear("y el hilo se guarda hasheado, no en claro",
             not any("5491130032002" in str(f) for f in crudo))


async def main() -> int:
    print(f"\n{NEGRITA}EL INSTRUMENTO DE MEDICIÓN, CALIBRADO{FIN}")
    print(f"{GRIS}  negocio de prueba: {NEG}{FIN}")

    try:
        await M.preparar()
    except Exception as e:  # noqa: BLE001
        print(f"\n{ROJO}Sin Postgres no se puede calibrar nada.{FIN}")
        print(f"{GRIS}  {config().database_url.split('@')[-1]} → {type(e).__name__}: {e}{FIN}")
        print(f"{GRIS}  Levantá Postgres y volvé a correr.{FIN}")
        return 1

    try:
        await sembrar()
        r = await M.resumen(NEG)
        await t1_los_numeros(r)
        await t2_donde_se_caen(r)
        await t3_los_turnos(r)
        await t4_no_tumba_el_bot()
        await t5_no_guarda_telefonos()
    finally:
        await M.borrar_negocio(NEG)
        await M.cerrar()

    print(f"\n{'─' * 58}")
    print(f"{VERDE}El instrumento mide lo que dice medir.{FIN}" if ok
          else f"{ROJO}Los números no coinciden con el patrón. NO usarlos.{FIN}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
