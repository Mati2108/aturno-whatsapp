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


async def t6_las_senales_se_acumulan():
    """Lo que el bot ya sabe y hasta ahora tiraba.

    Cada vez que no entiende, que el guardián frena una redacción, que alguien
    pide un horario que no está, o que preguntan algo sin cargar — el bot lo
    detecta, lo usa para contestar, y lo tira. Acá se guarda.

    Lo que importa de este test no es que escriba: es que AGRUPE. Una lista de
    incidentes sueltos no se mira; «esta frase falló 7 veces» se arregla. La
    diferencia entre las dos es todo el valor de la función.
    """
    print(f"\n{NEGRITA}[6] LAS SEÑALES SE ACUMULAN Y SE AGRUPAN{FIN}")
    print(f"{GRIS}  El bot ya detecta todo esto. Hasta ahora lo tiraba.{FIN}")

    # La misma frase, cuatro veces, en el mismo paso. Y otra, una sola vez.
    for _ in range(4):
        await M.anotar("no_entendio", NEG, "esperando_servicio", "tenes turno pa hoy?")
    await M.anotar("no_entendio", NEG, "esperando_dia", "el finde que viene")

    await M.anotar("guardian", NEG, None, "sirven", "vocabulario")
    await M.anotar("guardian", NEG, None, "sirven", "vocabulario")

    await M.anotar("demanda_perdida", NEG, "esperando_dia", "sábado", "cerrado")
    for _ in range(3):
        await M.anotar("demanda_perdida", NEG, "esperando_horario", "sábado", "ocupado")

    await M.anotar("sin_respuesta", NEG, None, "tienen estacionamiento?")

    r = await M.senales(NEG)

    no_entendio = r["no_entendio"]
    igual("la frase repetida se cuenta junta", no_entendio[0]["veces"], 4)
    igual("y es la primera de la lista", no_entendio[0]["texto"], "tenes turno pa hoy?")
    igual("la frase suelta también está", len(no_entendio), 2)
    chequear("con el paso donde falló",
             no_entendio[0]["paso"] == "esperando_servicio", str(no_entendio[0]))

    igual("lo que frena el guardián se cuenta", r["guardian"][0]["veces"], 2)
    igual("y dice qué regla fue", r["guardian"][0]["detalle"], "vocabulario")

    perdida = r["demanda_perdida"]
    igual("la demanda perdida se agrupa por lo pedido", len(perdida), 2)
    igual("y la más frecuente va primera", perdida[0]["veces"], 3)

    igual("las preguntas sin responder también", len(r["sin_respuesta"]), 1)

    # ---- Lo que NO se guarda ----
    #
    # El paso del nombre es donde la gente escribe su nombre completo. Un nombre
    # que el bot no entendió no se arregla mirando una tabla, así que guardarlo
    # es quedarse con un dato personal a cambio de nada.
    await M.anotar("no_entendio", NEG, "esperando_nombre", "María José Fernández")
    r2 = await M.senales(NEG)
    chequear("del paso del NOMBRE no se guarda el texto",
             not any("María" in (x["texto"] or "") for x in r2["no_entendio"]),
             str(r2["no_entendio"])[:70])

    # ---- Y si la base se cae, no pasa nada ----
    original, M._URL = M._URL, "postgresql://nadie@127.0.0.1:1/no-existe"
    M.olvidar_pool()
    try:
        await M.anotar("no_entendio", NEG, "esperando_dia", "algo")
        chequear("anotar con la base caída no levanta", True)
        vacio = await M.senales(NEG)
        chequear("y leer devuelve listas vacías, no un error",
                 vacio["no_entendio"] == [], str(vacio)[:50])
    except Exception as e:  # noqa: BLE001
        chequear(f"NO tenía que levantar: {type(e).__name__}", False, str(e)[:70])
    finally:
        M._URL = original
        M.olvidar_pool()


async def t7_el_embudo():
    """Dónde EXACTAMENTE se traba la gente.

    Contar tropiezos sueltos no alcanza: «24 veces no entendió eligiendo el
    servicio» puede ser un desastre o ser normal. Lo que se puede leer es el
    embudo — cuántos LLEGARON a cada paso y cuántos lo PASARON — porque ahí
    «se cae 1 de cada 5» sale solo.

    Y hay dos fallas distintas que sólo el embudo separa:

      · Un paso donde se CAEN muchos: está mal planteado.
      · Un paso que CUESTA muchos mensajes aunque casi nadie se caiga: se
        entiende mal, pero la gente insiste. Ese no se ve de ninguna otra forma
        y es el que se arregla más barato.

    El patrón de abajo tiene los dos a propósito.
    """
    print(f"\n{NEGRITA}[7] EL EMBUDO: LLEGARON, PASARON, SE CAYERON{FIN}")
    print(f"{GRIS}  «24 tropiezos» no se puede leer. «1 de cada 5» sí.{FIN}")

    NEGE = NEG + "-embudo"

    # 10 conversaciones. Forma EXACTA, calculada a mano:
    #   · las 10 llegan a elegir el servicio y lo pasan en 1 mensaje
    #   · las 10 llegan a elegir el día; 3 se caen ahí  → 30% de caída
    #   · las 7 que siguen tardan 3 mensajes en el horario → cuesta, no se cae
    for i in range(10):
        conv = f"{NEGE}:c{i}"
        await M.evento(conv, NEGE, "esperando_servicio", "esperando_dia", avanzo=True)
        if i < 3:
            # Se van eligiendo el día: entran y no salen.
            await M.evento(conv, NEGE, "esperando_dia", "esperando_dia", avanzo=False)
        else:
            await M.evento(conv, NEGE, "esperando_dia", "esperando_horario", avanzo=True)
            for _ in range(2):   # dos intentos que no avanzan
                await M.evento(conv, NEGE, "esperando_horario", "esperando_horario",
                               avanzo=False)
            await M.evento(conv, NEGE, "esperando_horario", "esperando_nombre",
                           avanzo=True)

    e = {f["paso"]: f for f in await M.embudo(NEGE)}

    igual("al servicio llegaron 10", e["esperando_servicio"]["llegaron"], 10)
    igual("y lo pasaron los 10", e["esperando_servicio"]["pasaron"], 10)

    igual("al día llegaron 10", e["esperando_dia"]["llegaron"], 10)
    igual("lo pasaron 7", e["esperando_dia"]["pasaron"], 7)
    igual("o sea que se cayó el 30%", e["esperando_dia"]["caida"], 0.3)

    igual("al horario llegaron 7", e["esperando_horario"]["llegaron"], 7)
    igual("y lo pasaron los 7", e["esperando_horario"]["pasaron"], 7)
    igual("no se cayó nadie", e["esperando_horario"]["caida"], 0.0)
    # Pero costó: 3 mensajes por conversación contra 1 en los otros pasos.
    igual("aunque costó 3 mensajes cada uno",
          e["esperando_horario"]["mensajes_por_conversacion"], 3.0)
    igual("y el servicio costó 1", e["esperando_servicio"]["mensajes_por_conversacion"], 1.0)

    # ---- Y quien llega y NUNCA vuelve a escribir, llegó igual ----
    #
    # Es la definición de abandonar, y era lo único que el embudo no mostraba:
    # contando sólo a quien mandó un mensaje ESTANDO en el paso, el que llega y
    # se va no figuraba ni como que llegó. El embudo decía «13 de 13» en un
    # paso donde tres personas se habían ido.
    for i in range(4):
        conv = f"{NEGE}:fuga{i}"
        await M.evento(conv, NEGE, "esperando_servicio", "esperando_staff",
                       avanzo=True)
        # Y se fue. No hay un solo evento con paso_antes = esperando_staff.

    e2 = {f["paso"]: f for f in await M.embudo(NEGE)}
    igual("los que llegaron y se fueron cuentan como que llegaron",
          e2["esperando_staff"]["llegaron"], 4)
    igual("y como que NO pasaron", e2["esperando_staff"]["pasaron"], 0)
    igual("o sea, se fue el 100% de los que llegaron ahí",
          e2["esperando_staff"]["caida"], 1.0)

    # El orden se afirma contra `_ORDEN_PASOS` y no contra una lista escrita a
    # mano acá: un embudo desordenado deja de ser un embudo —lo que se lee es
    # la caída de un escalón al siguiente— y con la lista repetida en dos
    # lados, agregar un paso al flujo rompía el test por el motivo equivocado.
    vistos = [f["paso"] for f in await M.embudo(NEGE)]
    esperado = [p for p in M._ORDEN_PASOS if p in vistos]
    chequear("los pasos vienen en el orden del flujo, no por frecuencia",
             vistos == esperado, str(vistos))

    # ---- Y no puede tumbar nada ----
    original, M._URL = M._URL, "postgresql://nadie@127.0.0.1:1/no-existe"
    M.olvidar_pool()
    try:
        await M.evento("x", NEGE, "esperando_dia", "esperando_dia", avanzo=False)
        chequear("registrar un evento con la base caída no levanta", True)
        chequear("y el embudo devuelve vacío, no un error", await M.embudo(NEGE) == [])
    except Exception as ex:  # noqa: BLE001
        chequear(f"NO tenía que levantar: {type(ex).__name__}", False, str(ex)[:70])
    finally:
        M._URL = original
        M.olvidar_pool()
        await M.borrar_negocio(NEGE)


async def t8_el_catalogo_de_fallas():
    """Todo lo que puede salir mal, y también lo que NUNCA salió mal.

    El catálogo no hay que inventarlo: `plantillas.py` tiene 41 plantillas y 18
    son «algo salió mal». Cada una es un modo de falla que el bot ya detecta y
    ya sabe nombrar. Lo único que faltaba era contar cuál salió.

    Contarlas así —por plantilla y no con código a medida por cada una— tiene
    una propiedad que vale más que el ahorro: **una plantilla de error nueva
    aparece en el tablero sin tocar el tablero**. Con cinco contadores a mano,
    las trece restantes pasaban y se perdían, incluidas `error_tecnico` y
    `no_pudo_contestar`, que son las peores.

    Y la mitad que no existía: **cuáles nunca pasaron**. Un tablero que sólo
    lista lo que falló no dice si el resto está bien o si nadie lo está
    mirando, y esas dos cosas se ven igual desde afuera.
    """
    print(f"\n{NEGRITA}[8] EL CATÁLOGO: LO QUE FALLÓ Y LO QUE NUNCA FALLÓ{FIN}")
    print(f"{GRIS}  18 plantillas de error ya existen. Sólo había que contarlas.{FIN}")

    NEGC = NEG + "-catalogo"

    # Salen tres plantillas de error, con formas distintas a propósito.
    for i in range(4):
        await M.evento(f"{NEGC}:a{i}", NEGC, "esperando_servicio",
                       "esperando_servicio", avanzo=False, plantilla="no_entendi")
    for i in range(2):
        await M.evento(f"{NEGC}:b{i}", NEGC, "esperando_dia", "esperando_dia",
                       avanzo=False, plantilla="sin_dato")
    await M.evento(f"{NEGC}:c", NEGC, "esperando_dia", "esperando_dia",
                   avanzo=False, plantilla="error_tecnico")
    # Y una que NO es un error: no tiene que aparecer en el catálogo.
    await M.evento(f"{NEGC}:d", NEGC, "esperando_dia", "esperando_horario",
                   avanzo=True, plantilla="lista_horarios")

    cat = {f["plantilla"]: f for f in await M.catalogo(NEGC)}

    igual("cuenta la que más salió", cat["no_entendi"]["veces"], 4)
    igual("y cuenta las otras", cat["sin_dato"]["veces"], 2)
    igual("incluido el error técnico, que antes se perdía",
          cat["error_tecnico"]["veces"], 1)

    chequear("una plantilla que NO es un error no entra al catálogo",
             "lista_horarios" not in cat, str(sorted(cat))[:80])

    # ---- La mitad que faltaba: lo que nunca pasó ----
    #
    # Sin esto, «no aparece» y «no lo estamos mirando» se ven igual, y son
    # cosas opuestas: una es que está bien, la otra es que no sabés.
    chequear("las que nunca salieron también están listadas",
             "atascado" in cat and "no_pudo_contestar" in cat,
             str(sorted(cat))[:110])
    igual("y figuran en cero", cat["atascado"]["veces"], 0)

    chequear("cada una viene con un nombre en castellano, no el de la función",
             cat["no_entendi"]["titulo"] and "_" not in cat["no_entendi"]["titulo"],
             repr(cat["no_entendi"].get("titulo")))
    chequear("y con a quién le importa",
             cat["no_entendi"]["grupo"] in ("persona", "turno", "sistema"),
             str(cat["no_entendi"].get("grupo")))

    # Las que pasaron van primero: es a lo que hay que mirar.
    orden = [f["plantilla"] for f in await M.catalogo(NEGC)]
    igual("la más frecuente encabeza la lista", orden[0], "no_entendi")

    # ---- Y no puede tumbar nada ----
    original, M._URL = M._URL, "postgresql://nadie@127.0.0.1:1/no-existe"
    M.olvidar_pool()
    try:
        vacio = await M.catalogo(NEGC)
        chequear("con la base caída sigue listando el catálogo, todo en cero",
                 len(vacio) > 10 and all(f["veces"] == 0 for f in vacio),
                 f"{len(vacio)} filas")
    except Exception as ex:  # noqa: BLE001
        chequear(f"NO tenía que levantar: {type(ex).__name__}", False, str(ex)[:70])
    finally:
        M._URL = original
        M.olvidar_pool()
        await M.borrar_negocio(NEGC)


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
        await t6_las_senales_se_acumulan()
        await t7_el_embudo()
        await t8_el_catalogo_de_fallas()
    finally:
        await M.borrar_negocio(NEG)
        await M.cerrar()

    print(f"\n{'─' * 58}")
    print(f"{VERDE}El instrumento mide lo que dice medir.{FIN}" if ok
          else f"{ROJO}Los números no coinciden con el patrón. NO usarlos.{FIN}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
