"""
test_bordes.py — Lo que pasa cuando la persona NO hace lo esperado.

POR QUÉ ESTE ARCHIVO
--------------------
`todos_los_caminos.py` ya recorre estas bifurcaciones y las imprime lindas.
Pero imprimir no es verificar: el peor bug que tuvo este bot —contestar "no" al
resumen y que el turno se reservara igual— estaba EN ESA LISTA, salía en
pantalla en cada corrida, y nadie lo vio. Un guion que no afirma nada no
protege nada; sólo mueve el trabajo de encontrarlo a quien lo lea.

Acá cada caso afirma una propiedad y el proceso sale con 1 si alguna se rompe.

TODO CORRE SIN LLM
------------------
Los casos usan números y frases de la tabla de atajos a propósito. Eso no es
una limitación: es lo que hace que estas pruebas sigan pasando con el
clasificador caído —que es como está la cuenta de Anthropic hoy— y que un rojo
signifique siempre un bug del código y nunca "el modelo contestó otra cosa".

    python test_bordes.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from langgraph.checkpoint.memory import MemorySaver

from src.agentes import flujo as F
from src import plantillas as P
from src.agentes.estados import (
    Estado, Intencion, pedido_de_cambio, respuesta_fija, sin_contenido)
from src.aturno.doble import AturnoDoble
from src.fechas import calendario

logging.basicConfig(level=logging.ERROR)

NEG, TEL = "demo-peluqueria", "+5491130032002"
VERDE, ROJO, GRIS, NEGRITA, FIN = "\033[32m", "\033[31m", "\033[90m", "\033[1m", "\033[0m"

ok = True


def chequear(nombre: str, cond: bool, detalle: str = "") -> None:
    global ok
    ok = ok and bool(cond)
    color = VERDE if cond else ROJO
    print(f"  {color}{'✓' if cond else '✗'}{FIN} {nombre}"
          + (f"{GRIS}  ({detalle}){FIN}" if detalle else ""))


def _cfg(hilo: str, nombre: str | None = None) -> dict:
    return {"configurable": {
        "thread_id": hilo, "business_id": NEG, "nombre_negocio": "Peluquería Demo",
        "telefono": TEL, "nombre_cliente": nombre,
        "calendario": calendario(dt.date.today(), 8),
    }}


async def hasta_el_resumen(g, hilo: str) -> dict:
    """Deja la conversación parada en el resumen, sin LLM.

    El nombre se saltea pasando `nombre_cliente`: ese paso es el único que
    necesita al clasificador, y no es lo que se está probando acá.
    """
    salida = {}
    for m in ["hola", "1", "3", "1", "1"]:
        salida = await g.ainvoke({"mensaje": m}, _cfg(hilo, "Ana Pérez"))
    return salida


# ══════════════════════════════════════════════════════════════════

async def t1_el_no_no_reserva(g):
    print(f"\n{NEGRITA}[1] CONTESTAR QUE NO AL RESUMEN NO RESERVA{FIN}")
    print(f"{GRIS}  El resumen dice «Respondé SÍ o NO». El NO tiene que valer.{FIN}")

    # Cada variante en su propio hilo: una que reserve dejaría el estado en
    # CONFIRMADO y contaminaría a la siguiente.
    for i, texto in enumerate(["no", "NO", "Nop", "no gracias", "mejor no"]):
        s = await hasta_el_resumen(g, f"t1-{i}")
        assert s["estado"] == Estado.ESPERANDO_CONFIRMACION.value, "no llegó al resumen"
        s = await g.ainvoke({"mensaje": texto}, _cfg(f"t1-{i}", "Ana Pérez"))
        chequear(f"«{texto}» no reserva",
                 s["estado"] != Estado.CONFIRMADO.value, f"estado={s['estado']}")
        chequear(f"«{texto}» lo dice en la primera línea",
                 s["respuesta"].startswith("Listo, no reservé nada"),
                 s["respuesta"].splitlines()[0][:40])

    # Y los números, que era la otra cara del mismo bug: "2" señalaba el
    # renglón "no" de las opciones y reservaba igual que el "1".
    for i, texto in enumerate(["1", "2"]):
        s = await hasta_el_resumen(g, f"t1n-{i}")
        s = await g.ainvoke({"mensaje": texto}, _cfg(f"t1n-{i}", "Ana Pérez"))
        chequear(f"«{texto}» no reserva (el resumen no es una lista numerada)",
                 s["estado"] != Estado.CONFIRMADO.value, f"estado={s['estado']}")

    # El sí, para que el arreglo no rompa lo que sí tenía que andar.
    s = await hasta_el_resumen(g, "t1-si")
    s = await g.ainvoke({"mensaje": "sí"}, _cfg("t1-si", "Ana Pérez"))
    chequear("«sí» SÍ reserva", s["estado"] == Estado.CONFIRMADO.value,
             f"estado={s['estado']}")
    chequear("y devuelve el código del turno", "Código:" in s["respuesta"])


async def t2_el_no_no_borra_nada(g):
    print(f"\n{NEGRITA}[2] DECIR QUE NO NO BORRA LO ELEGIDO{FIN}")
    print(f"{GRIS}  Quien contesta que no quiere cambiar UNA cosa, no empezar de cero.{FIN}")

    s = await hasta_el_resumen(g, "t2")
    elegido = {k: s.get(k) for k in ("servicio_id", "fecha", "hora")}
    s = await g.ainvoke({"mensaje": "no"}, _cfg("t2", "Ana Pérez"))

    chequear("el servicio sigue elegido", s.get("servicio_id") == elegido["servicio_id"])
    chequear("la fecha sigue elegida", s.get("fecha") == elegido["fecha"])
    chequear("la hora sigue elegida", s.get("hora") == elegido["hora"])
    chequear("y sigue parado en el resumen",
             s["estado"] == Estado.ESPERANDO_CONFIRMACION.value)

    # Y después del no, pedir un cambio retrocede al paso correcto y limpia
    # SOLO lo que dejó de valer.
    s = await g.ainvoke({"mensaje": "el día"}, _cfg("t2", "Ana Pérez"))
    chequear("«el día» retrocede al paso del día",
             s["estado"] == Estado.ESPERANDO_DIA.value, f"estado={s['estado']}")
    chequear("y limpia la hora, que ya no vale", s.get("hora") is None)
    chequear("pero conserva el servicio", s.get("servicio_id") == elegido["servicio_id"])


async def t3_cancelar_sigue_borrando(g):
    print(f"\n{NEGRITA}[3] «CANCELAR» SIGUE SIENDO OTRA COSA QUE «NO»{FIN}")
    print(f"{GRIS}  Confundirlas sería el bug contrario: borrarle todo a quien dijo que no.{FIN}")

    s = await hasta_el_resumen(g, "t3")
    s = await g.ainvoke({"mensaje": "cancelar"}, _cfg("t3", "Ana Pérez"))
    chequear("cancelar sí limpia lo elegido", s.get("servicio_id") is None)
    chequear("cancelar vuelve al principio", s["estado"] == Estado.APERTURA.value)
    chequear("cancelar no reserva", s["estado"] != Estado.CONFIRMADO.value)


async def t4_no_te_entendi(g):
    print(f"\n{NEGRITA}[4] CUANDO NO ENTIENDE, LO DICE{FIN}")
    print(f"{GRIS}  Repetir el pedido idéntico se lee como que el bot se colgó.{FIN}")

    await g.ainvoke({"mensaje": "hola"}, _cfg("t4"))
    s = await g.ainvoke({"mensaje": "🙃"}, _cfg("t4"))
    chequear("un emoji suelto recibe «No te entendí»",
             s["respuesta"].startswith("No te entendí"),
             s["respuesta"].splitlines()[0][:40])
    chequear("y abajo repite el pedido del paso",
             "1." in s["respuesta"] or "Escribime" in s["respuesta"])
    chequear("sin avanzar de paso", s["estado"] == Estado.ESPERANDO_SERVICIO.value)


async def t5_no_hay_callejon(g):
    print(f"\n{NEGRITA}[5] EL QUE NUNCA ELIGIÓ NADA TAMBIÉN TIENE SALIDA{FIN}")
    print(f"{GRIS}  Antes el contador subía a 2, volvía a 0 y giraba para siempre.{FIN}")

    await g.ainvoke({"mensaje": "hola"}, _cfg("t5"))
    respuestas = []
    for _ in range(F.LIMITE_ATASCADO):
        s = await g.ainvoke({"mensaje": "..."}, _cfg("t5"))
        respuestas.append(s["respuesta"])

    chequear("al insistir, deja de repetir lo mismo",
             respuestas[-1] != respuestas[0])
    chequear("y ofrece una salida",
             "una persona" in respuestas[-1],
             respuestas[-1].splitlines()[0][:50])
    # Sin avance no se escala: es la propiedad que protege el teléfono del dueño.
    chequear("sin haber elegido nada, NO escala al negocio",
             s["estado"] != Estado.EN_MANOS_HUMANAS.value, f"estado={s['estado']}")


async def t6_gracias_no_es_un_pedido_nuevo(g):
    print(f"\n{NEGRITA}[6] AGRADECER DESPUÉS DE RESERVAR NO REABRE EL MENÚ{FIN}")

    await hasta_el_resumen(g, "t6")
    s = await g.ainvoke({"mensaje": "sí"}, _cfg("t6", "Ana Pérez"))
    assert s["estado"] == Estado.CONFIRMADO.value, "no reservó"

    s = await g.ainvoke({"mensaje": "gracias"}, _cfg("t6", "Ana Pérez"))
    chequear("no vuelve a saludar", "Soy el asistente" not in s["respuesta"])
    chequear("no vuelve a listar los servicios", "$8.000" not in s["respuesta"])
    chequear("cierra en una línea", len(s["respuesta"].splitlines()) == 1,
             s["respuesta"][:50])
    chequear("y el turno sigue confirmado", s["estado"] == Estado.CONFIRMADO.value)

    # Un segundo gracias sigue siendo un gracias. Puede repetirse.
    s = await g.ainvoke({"mensaje": "muchas gracias"}, _cfg("t6", "Ana Pérez"))
    chequear("agradecer dos veces sigue cerrando", "De nada" in s["respuesta"])

    # Pero un HOLA no es un gracias: es alguien que vuelve.
    #
    # Acá estaba el bucle. "hola" y "gracias" comparten intención —SALUDO— y
    # esta rama contestaba el cierre a las dos SIN mover el estado, así que
    # CONFIRMADO no se soltaba nunca: todo saludo posterior devolvía la misma
    # línea, incluida la que invita a escribir. La persona escribía, y recibía
    # otra vez "escribime". Para siempre.
    s = await g.ainvoke({"mensaje": "hola"}, _cfg("t6", "Ana Pérez"))
    chequear("saludar después del cierre NO repite el cierre",
             "De nada" not in s["respuesta"], s["respuesta"][:60])
    chequear("saluda de vuelta y muestra qué se puede hacer",
             "Soy el asistente" in s["respuesta"])
    chequear("y suelta el estado confirmado",
             s["estado"] != Estado.CONFIRMADO.value, f"estado={s['estado']}")

    # Pero pedir algo de verdad sí arranca un pedido nuevo.
    await hasta_el_resumen(g, "t6b")
    await g.ainvoke({"mensaje": "sí"}, _cfg("t6b", "Ana Pérez"))
    s = await g.ainvoke({"mensaje": "quiero otro turno"}, _cfg("t6b", "Ana Pérez"))
    chequear("un pedido nuevo sí reabre el flujo",
             s["estado"] != Estado.CONFIRMADO.value, f"estado={s['estado']}")


async def t7_la_sesion_vence(g):
    print(f"\n{NEGRITA}[7] LA SESIÓN VENCE, Y NO CONFIRMA UNA FECHA VIEJA{FIN}")
    print(f"{GRIS}  Volver a la semana con un «dale» confirmaba el día elegido entonces.{FIN}")

    s = await hasta_el_resumen(g, "t7")
    fecha_vieja = s.get("fecha")

    # Se envejece el sello a mano: es lo que hace que esto se pueda probar sin
    # esperar media hora, igual que `test_demora.py` con su reloj.
    viejo = (F.ahora() - dt.timedelta(minutes=F.ajustes().sesion_minutos + 1)).isoformat()
    await g.aupdate_state(_cfg("t7", "Ana Pérez"), {"ultimo_en": viejo})

    s = await g.ainvoke({"mensaje": "dale"}, _cfg("t7", "Ana Pérez"))
    chequear("un «dale» tardío NO reserva", s["estado"] != Estado.CONFIRMADO.value,
             f"estado={s['estado']}")
    chequear("avisa que arranca de nuevo", "arranco de nuevo" in s["respuesta"])
    chequear("y suelta la fecha vieja", s.get("fecha") is None, f"fecha={fecha_vieja}")

    # Dentro de la ventana no pasa nada de esto.
    s2 = await hasta_el_resumen(g, "t7b")
    s2 = await g.ainvoke({"mensaje": "dale"}, _cfg("t7b", "Ana Pérez"))
    chequear("y una conversación fresca reserva igual que siempre",
             s2["estado"] == Estado.CONFIRMADO.value, f"estado={s2['estado']}")


async def t8_sin_llm_para_lo_previsible():
    print(f"\n{NEGRITA}[8] LO PREVISIBLE NO PAGA UNA LLAMADA AL MODELO{FIN}")

    chequear("«no» en el resumen se resuelve sin LLM",
             respuesta_fija("no", Estado.ESPERANDO_CONFIRMACION) is not None)
    chequear("y significa RECHAZAR, no CANCELAR",
             respuesta_fija("no", Estado.ESPERANDO_CONFIRMACION)[0] == Intencion.RECHAZAR)
    chequear("«el día» también",
             respuesta_fija("el día", Estado.ESPERANDO_CONFIRMACION) is not None)
    chequear("pero «no» en otro paso NO se atajа",
             respuesta_fija("no", Estado.ESPERANDO_DIA) is None,
             "ahí significa otra cosa y lo decide el modelo")

    # Pedir cambiar algo elegido: el modelo lo leía como un "volver" genérico,
    # que retrocede UN paso. Quien pide otro servicio estando en el día
    # terminaba eligiendo profesional.
    for frase, esperada in [
        ("mejor cambio de servicio", Intencion.ELEGIR_SERVICIO),
        ("quiero otro servicio", Intencion.ELEGIR_SERVICIO),
        ("cambiame el horario", Intencion.ELEGIR_HORARIO),
        ("mejor otro día", Intencion.ELEGIR_DIA),
        ("cambiar la fecha", Intencion.ELEGIR_DIA),
        ("mejor con otro profesional", Intencion.ELEGIR_STAFF),
    ]:
        chequear(f"«{frase}» se resuelve sin LLM",
                 pedido_de_cambio(frase) == esperada,
                 str(pedido_de_cambio(frase)))

    # Y no se dispara sola: hace falta nombrar QUÉ y pedir el cambio.
    chequear("«quiero el servicio de coloración» NO es un pedido de cambio",
             pedido_de_cambio("quiero el servicio de coloración") is None,
             "nombra el servicio pero no pide cambiarlo")
    chequear("«mejor sí» tampoco", pedido_de_cambio("mejor sí") is None,
             "pide cambio pero no dice de qué")

    chequear("un emoji suelto no tiene nada que clasificar", sin_contenido("👋"))
    chequear("tres espacios tampoco", sin_contenido("   "))
    chequear("puntos suspensivos tampoco", sin_contenido("..."))
    chequear("pero «no» sí tiene contenido", not sin_contenido("no"))


async def t10_la_senia(g, doble):
    print(f"\n{NEGRITA}[10] EL SERVICIO CON SEÑA NO SE CONFIRMA SIN PAGAR{FIN}")
    print(f"{GRIS}  Por WhatsApp se salteaba el depósito que la web sí cobra.{FIN}")

    # "2" es Coloración, el servicio con seña del doble.
    for m in ["hola", "2", "3", "1", "1"]:
        s = await g.ainvoke({"mensaje": m}, _cfg("t10", "Ana Pérez"))

    chequear("el resumen avisa la seña ANTES de confirmar",
             "Seña:" in s["respuesta"], s["respuesta"].splitlines()[-3][:50])
    chequear("y dice cuánto", "$7.500" in s["respuesta"])

    s = await g.ainvoke({"mensaje": "sí"}, _cfg("t10", "Ana Pérez"))
    chequear("confirmar NO deja el turno confirmado",
             s["estado"] == Estado.ESPERANDO_SENIA.value, f"estado={s['estado']}")
    chequear("dice que falta pagar", "falta pagar la seña" in s["respuesta"])
    chequear("manda el link", "mercadopago.com" in s["respuesta"])
    chequear("y dice por cuánto tiempo aparta el horario",
             f"{doble.minutos_de_retencion} minutos" in s["respuesta"])
    chequear("guarda el código para poder consultar el pago",
             bool(s.get("codigo_pendiente")))

    # Un saludo mientras espera no le tira la lista de servicios encima.
    s = await g.ainvoke({"mensaje": "hola"}, _cfg("t10", "Ana Pérez"))
    chequear("un «hola» mientras espera no reabre el menú",
             "Sigo esperando el pago" in s["respuesta"],
             s["respuesta"][:45])

    # Escribir mientras el pago TODAVÍA no entró no reabre el menú.
    #
    # Antes, cualquier mensaje que no fuera un saludo caía en "esta persona está
    # empezando un pedido nuevo" y recibía la lista de servicios encima de un
    # turno que seguía esperando el pago.
    s = await g.ainvoke({"mensaje": "ya pagué"}, _cfg("t10", "Ana Pérez"))
    chequear("«ya pagué» sin pago acreditado no reabre el menú",
             "Sigo esperando el pago" in s["respuesta"], s["respuesta"][:45])
    chequear("y la conversación sigue esperando la seña",
             s["estado"] == Estado.ESPERANDO_SENIA.value, f"estado={s['estado']}")

    # Y ACÁ ESTÁ EL AGUJERO QUE SE VIO EN PRODUCCIÓN.
    #
    # El vigilante que consulta el pago vive en el proceso: es una tarea suelta
    # con quince minutos de presupuesto. Si el servicio reinicia o redeploya en
    # el medio, esa tarea muere y NADIE vuelve a preguntar nunca. La persona
    # paga, escribe "ya pagué", y el bot le contesta con la lista de servicios.
    #
    # Por eso el mensaje entrante tiene que ser la segunda oportunidad: antes de
    # decidir nada, se le pregunta a aturno si el pago entró.
    doble.marcar_senia_pagada(s["codigo_pendiente"])
    s = await g.ainvoke({"mensaje": "ya pagué"}, _cfg("t10", "Ana Pérez"))
    chequear("con el pago acreditado, escribir confirma el turno",
             s["estado"] == Estado.CONFIRMADO.value, f"estado={s['estado']}")
    chequear("y se lo dice", "Entró el pago" in s["respuesta"],
             s["respuesta"].splitlines()[0][:45])
    chequear("con el código del turno", "Código:" in s["respuesta"])
    chequear("y deja de quedar un pago pendiente",
             not s.get("codigo_pendiente"))

    # Y un servicio sin seña sigue confirmando derecho.
    for m in ["hola", "1", "3", "1", "1", "sí"]:
        s2 = await g.ainvoke({"mensaje": m}, _cfg("t10b", "Ana Pérez"))
    chequear("un servicio sin seña confirma como siempre",
             s2["estado"] == Estado.CONFIRMADO.value, f"estado={s2['estado']}")
    chequear("y su resumen no habla de ninguna seña",
             "Seña" not in s2["respuesta"])


async def t11_sin_link_no_hay_turno(g, doble):
    print(f"\n{NEGRITA}[11] SI NO SE PUEDE COBRAR, NO SE PROMETE EL TURNO{FIN}")
    print(f"{GRIS}  MercadoPago sin conectar, token vencido, o su API caída.{FIN}")

    doble.link_falla = True
    try:
        for m in ["hola", "2", "3", "1", "1", "sí"]:
            s = await g.ainvoke({"mensaje": m}, _cfg("t11", "Ana Pérez"))
    finally:
        doble.link_falla = False

    chequear("NO queda confirmado", s["estado"] != Estado.CONFIRMADO.value,
             f"estado={s['estado']}")
    chequear("ni esperando una seña que no se puede pagar",
             s["estado"] != Estado.ESPERANDO_SENIA.value)
    chequear("lo dice sin culpar a la persona",
             "No pude generar el link" in s["respuesta"],
             s["respuesta"].splitlines()[0][:50])
    chequear("y ofrece una salida",
             "una persona" in s["respuesta"] or "http" in s["respuesta"])


async def t12_el_aviso_del_pago():
    print(f"\n{NEGRITA}[12] AVISAR QUE ENTRÓ EL PAGO — Y NO AVISAR DE MÁS{FIN}")

    from src.aturno.base import ClienteAturno

    # La regla que más importa: "no sé" no es "no pagó". Decirle a alguien que
    # pagó que se le venció el plazo es sacarle un turno que ya abonó.
    chequear("el contrato distingue las tres respuestas",
             ClienteAturno.senia_pagada.__doc__ is not None
             and "None" in ClienteAturno.senia_pagada.__doc__)

    # Y las plantillas de los dos desenlaces existen y dicen lo que hay que decir.
    import datetime as _d
    confirmada = P.senia_confirmada("Coloración", "Sofi", _d.date(2026, 8, 20),
                                    _d.time(10, 0), "ABCD-1234")
    chequear("la de confirmación dice que entró el pago", "Entró el pago" in confirmada)
    chequear("y trae el código", "ABCD-1234" in confirmada)
    vencida = P.senia_vencida()
    chequear("la de vencimiento dice que soltó el horario", "solté el horario" in vencida)
    chequear("y ofrece volver a intentar", "escribime" in vencida)


async def t15_el_nombre_no_gasta_modelo():
    print(f"\n{NEGRITA}[15] EL NOMBRE SE RESUELVE SIN MODELO{FIN}")
    print(f"{GRIS}  Era el único paso sin atajo: todo cliente nuevo pagaba una llamada.{FIN}")

    from src.agentes.estados import nombre_propio

    for texto, esperado in [
        ("Ana", "Ana"),
        ("matías calo", "Matías Calo"),
        ("me llamo Juan Pérez", "Juan Pérez"),
        ("soy Ana", "Ana"),
        ("mi nombre es Sofía", "Sofía"),
        ("a nombre de Carlos Gómez", "Carlos Gómez"),
        ("JUAN CARLOS PEREZ", "Juan Carlos Perez"),
    ]:
        chequear(f"«{texto}» → {esperado}", nombre_propio(texto) == esperado,
                 repr(nombre_propio(texto)))

    # Y lo que importa más: cuándo NO adivina. Un turno a nombre de «No Gracias»
    # no lo arregla nadie hasta que la persona se presenta en el local.
    print(f"{GRIS}  Y donde tiene que rendirse y dejar pasar el mensaje al modelo:{FIN}")
    for texto in ["no gracias", "el jueves", "dale", "cuanto sale", "hola",
                  "quiero cambiar el horario", "mañana", "a las 3 de la tarde",
                  "", "el 3", "cualquiera"]:
        chequear(f"«{texto}» NO es un nombre", nombre_propio(texto) is None,
                 repr(nombre_propio(texto)))

    # De punta a punta: llegar al paso del nombre y contestarlo no llama al modelo.
    doble = AturnoDoble()
    F.configurar(doble)
    g = F.construir_flujo(MemorySaver())
    cfg = {"configurable": {
        "thread_id": "t15", "business_id": NEG, "nombre_negocio": "Peluquería Demo",
        "telefono": "+5491100000015", "nombre_cliente": None,
        "calendario": calendario(dt.date.today(), 8)}}
    for m in ["hola", "1", "3", "1", "1"]:
        s = await g.ainvoke({"mensaje": m}, cfg)
    assert s["estado"] == Estado.ESPERANDO_NOMBRE.value, f"no llegó al nombre: {s['estado']}"

    s = await g.ainvoke({"mensaje": "soy Ana Pérez"}, cfg)
    chequear("el flujo lo toma como nombre", s["intent"] == Intencion.DAR_NOMBRE.value,
             f"intent={s['intent']}")
    chequear("y avanza al resumen",
             s["estado"] == Estado.ESPERANDO_CONFIRMACION.value, f"estado={s['estado']}")
    chequear("con el nombre bien escrito", "Ana Pérez" in s["respuesta"],
             s["respuesta"].splitlines()[0][:40])


async def t19_todas_las_formas_de_negar_el_nombre():
    print(f"\n{NEGRITA}[19] TODAS LAS FORMAS DE DECIR «NO ME LLAMO ASÍ»{FIN}")
    print(f"{GRIS}  Sin modelo: «no soy milagros» caía en desconocido y mostraba el menú.{FIN}")

    from src.agentes.estados import correccion_de_nombre

    # (frase, ¿niega?, nombre nuevo si lo dijo)
    CASOS = [
        # Niega sin decir el nuevo
        ("no me llamo Milagros",            True, None),
        ("no me llamo milagros",            True, None),
        ("no soy Milagros",                 True, None),
        ("yo no soy Milagros",              True, None),
        ("mi nombre no es Milagros",        True, None),
        ("Milagros no es mi nombre",        True, None),
        ("ese no es mi nombre",             True, None),
        ("no me llamo así",                 True, None),
        ("te equivocaste de nombre",        True, None),
        ("está mal mi nombre",              True, None),

        # Niega Y dice el nuevo, que es lo que más importa
        ("no me llamo Milagros, me llamo Matías",  True, "Matías"),
        ("no soy Milagros, soy Matías",            True, "Matías"),
        ("mi nombre no es Milagros, es Matías",    True, "Matías"),
        ("me llamo Matías, no Milagros",           True, "Matías"),
        ("no me llamo Milagros sino Matías",       True, "Matías"),

        # Y lo que NO es una corrección: no puede robarle el mensaje al flujo
        ("me llamo Matías",                 False, None),
        ("soy Matías",                      False, None),
        ("no",                              False, None),
        ("no quiero ese horario",           False, None),
        ("no me gusta el jueves",           False, None),
        ("hola",                            False, None),
        ("no tengo preferencia",            False, None),
    ]

    for frase, niega, nuevo in CASOS:
        r = correccion_de_nombre(frase)
        esperado = (niega, nuevo) if niega else None
        obtenido = r if r is None else (r[0], r[1])
        chequear(f"«{frase[:38]}»", obtenido == esperado, f"→ {obtenido!r}")

    # Y de punta a punta, SIN modelo: no deriva al menú de servicios.
    F.configurar(AturnoDoble())
    g = F.construir_flujo(MemorySaver())
    cfg = _cfg("t19", "Milagros")

    await g.ainvoke({"mensaje": "hola"}, cfg)
    s = await g.ainvoke({"mensaje": "no soy Milagros"}, cfg)
    chequear("«no soy Milagros» NO muestra los servicios",
             "Corte de pelo" not in s["respuesta"], s["respuesta"][:44])
    chequear("y pregunta el nombre", "llamás" in s["respuesta"], s["respuesta"][:44])

    s = await g.ainvoke({"mensaje": "no me llamo Milagros, me llamo Matías"}, cfg)
    chequear("y con el nombre adentro, lo toma directo",
             "Matías" in s["respuesta"] and "Milagros" not in s["respuesta"],
             s["respuesta"][:44])


async def t18_se_puede_corregir_el_nombre():
    print(f"\n{NEGRITA}[18] «NO ME LLAMO MILAGROS, ME LLAMO MATÍAS»{FIN}")
    print(f"{GRIS}  Sacó turno para la madre y el bot lo saludó así para siempre.{FIN}")

    F.configurar(AturnoDoble())

    texto_del_caso = ["no me llamo Milagros, me llamo Matías"]

    async def avanzar(estado: Estado, intent: Intencion, nombre: str | None = None,
                      texto: str | None = None):
        texto_del_caso[0] = texto or "no me llamo Milagros, me llamo Matías"
        conv = {"mensaje": texto_del_caso[0],
                "estado": estado.value, "intent": intent.value,
                "entidades": {"nombre": nombre} if nombre else {}}
        return await F.avanzar(conv, _cfg("t18", "Milagros"))

    # El clasificador ya devuelve dar_nombre + "Matías" para estas frases —
    # está medido. Lo que faltaba era que el flujo hiciera algo con eso.
    for estado in (Estado.APERTURA, Estado.ESPERANDO_SERVICIO, Estado.ESPERANDO_DIA):
        r = await avanzar(estado, Intencion.DAR_NOMBRE, "Matías")
        chequear(f"en «{estado.value}» corrige la identidad",
                 r.get("nombre") == "Matías", f"nombre={r.get('nombre')!r}")

    # Y lo que NO se puede romper: en la confirmación, dar un nombre sigue
    # significando A NOMBRE DE QUIÉN va ESTE turno, no quién sos vos. Es la
    # distinción que evita que el padre que reserva para su hija quede
    # renombrado para siempre.
    r = await avanzar(Estado.ESPERANDO_CONFIRMACION, Intencion.DAR_NOMBRE, "Sofía")
    chequear("en la confirmación sigue siendo el nombre DEL TURNO",
             r.get("nombre_del_turno") == "Sofía" and not r.get("nombre"),
             f"turno={r.get('nombre_del_turno')!r} identidad={r.get('nombre')!r}")

    # Un nombre basura no pisa el que ya está: perder la identidad por un
    # "no" mal clasificado es peor que no corregirla nunca.
    r = await avanzar(Estado.APERTURA, Intencion.DAR_NOMBRE, "a")
    chequear("un nombre de una letra no pisa nada", not r.get("nombre"),
             f"nombre={r.get('nombre')!r}")

    # ---- Negar sin decir el nuevo ----
    #
    # "no me llamo Milagros", a secas. El clasificador devuelve dar_nombre y
    # extrae... "Milagros": el nombre NEGADO. Sin esta guarda el bot contesta
    # "Listo, te anoto como Milagros" y le reafirma el error a alguien que
    # acaba de decir que ese no es su nombre. Es peor que ignorarlo.
    from src.agentes.estados import nombre_negado

    for texto in ["no me llamo Milagros", "no me llamo milagros",
                  "mi nombre no es Milagros", "yo no soy Milagros"]:
        chequear(f"«{texto}» se detecta como negación",
                 nombre_negado(texto, "Milagros"))

    # Y lo que NO puede tomar por negación, o se rompe la corrección normal.
    for texto, nom in [("me llamo Matías", "Matías"),
                       ("no me llamo Milagros, me llamo Matías", "Matías"),
                       ("soy Matías", "Matías")]:
        chequear(f"«{texto}» NO es negación de {nom}",
                 not nombre_negado(texto, nom))

    r = await avanzar(Estado.APERTURA, Intencion.DAR_NOMBRE, "Milagros",
                      texto="no me llamo Milagros")
    chequear("negar sin decir el nuevo NO guarda el negado",
             r.get("nombre") != "Milagros", f"nombre={r.get('nombre')!r}")
    chequear("y pregunta cómo se llama", r.get("_plantilla") == "que_nombre",
             f"→ {r.get('_plantilla')!r}")
    # SIN mover el paso: desde la apertura, el siguiente es el resumen, y el
    # resumen sin nada elegido revienta. Corregir el nombre no es reservar.
    chequear("sin moverlo de paso", not r.get("estado"), f"estado={r.get('estado')!r}")

    # De punta a punta: el saludo tiene que usar el nombre NUEVO, no el que
    # traía la config. Sin esto se corrige por dentro y se sigue saludando mal.
    g = F.construir_flujo(MemorySaver())
    s = await g.ainvoke({"mensaje": "hola"}, _cfg("t18e", "Milagros"))
    chequear("primero saluda con el viejo", "Milagros" in s["respuesta"])

    s = await g.ainvoke({"mensaje": "me llamo Matías"},
                        {"configurable": {**_cfg("t18e", "Milagros")["configurable"]}})
    chequear("y después de corregir, ya no dice Milagros",
             "Milagros" not in s["respuesta"], s["respuesta"][:46])
    chequear("y sí dice Matías", "Matías" in s["respuesta"], s["respuesta"][:46])


async def t17_ningun_paso_termina_en_el_error_tecnico():
    print(f"\n{NEGRITA}[17] NINGÚN PASO LE MUESTRA UN ERROR TÉCNICO A LA PERSONA{FIN}")
    print(f"{GRIS}  «quiero otro turno» después de reservar devolvía «se me complicó».{FIN}")

    doble = AturnoDoble()
    F.configurar(doble)
    servicios = await doble.listar_servicios(NEG)
    error = P.error_tecnico()

    # `_pedir_paso` es el que arma el pedido del paso actual, y termina en el
    # error técnico para cualquier estado que no reconozca. Cada estado en el
    # que la conversación puede quedarse tiene que tener su salida.
    #
    # No es un caso hipotético: `avanzar` devuelve `{}` cuando una intención no
    # aplica en el paso —VER_MAS fuera del paso del horario, por ejemplo— y ese
    # `{}` cae justo acá. Con el estado en CONFIRMADO, la persona recibía un
    # error por pedir un segundo turno.
    base = {
        "mensaje": "hola", "servicio_id": "svc-corte", "profesional_id": "st-lean",
        "fecha": dt.date.today().isoformat(), "hora": "09:00",
        "nombre": "Ana Pérez", "opciones": [],
    }
    cfg = _cfg("t17", "Ana Pérez")["configurable"]

    for estado in Estado:
        conv = {**base, "estado": estado.value}
        try:
            paso = await F._pedir_paso(conv, cfg, NEG, "Peluquería Demo", servicios)
            respuesta = paso.get("respuesta", "")
        except Exception as e:  # noqa: BLE001
            respuesta, error_str = "", f"explotó: {e}"
            chequear(f"«{estado.value}» no explota", False, error_str)
            continue
        chequear(f"«{estado.value}» no cae en el error técnico",
                 respuesta and respuesta != error,
                 (respuesta or "vacío").splitlines()[0][:44])


async def t16_el_techo_de_gasto_degrada_sin_romper():
    print(f"\n{NEGRITA}[16] CON EL TECHO DE GASTO TOCADO, EL BOT SIGUE ATENDIENDO{FIN}")
    print(f"{GRIS}  Quedarse sin crédito ya dejó a clientes nuevos sin poder sacar turno.{FIN}")

    from src.agentes import clasificador as C
    from src.gasto import GASTO, Linea

    class NoDeberiaLlamarse:
        def __init__(self): self.llamadas = 0
        async def ainvoke(self, _datos):
            self.llamadas += 1
            raise AssertionError("se llamó al modelo con el techo tocado")

    gastado = dict(GASTO.por_motivo)
    try:
        # Se simula un día caro: por encima del tope configurado.
        GASTO.por_motivo = {"clasificar": Linea(llamadas=1, usd=999.0)}
        chequear("el techo se detecta", C.tope_alcanzado())

        cadena = NoDeberiaLlamarse()
        r = await C.clasificar(cadena, "quiero un corte mañana",
                               Estado.ESPERANDO_SERVICIO, None,
                               "2026-08-19", "miércoles", "")
        chequear("no se llama al modelo", cadena.llamadas == 0)
        chequear("y cae en DESCONOCIDO, que el flujo sabe manejar",
                 r.intent == Intencion.DESCONOCIDO, f"intent={r.intent.value}")

        # Lo que importa de verdad: con el modelo apagado, alguien que contesta
        # con números TIENE que poder sacar su turno igual.
        doble = AturnoDoble()
        F.configurar(doble)
        g = F.construir_flujo(MemorySaver())
        cfg = {"configurable": {
            "thread_id": "t16", "business_id": NEG, "nombre_negocio": "Peluquería Demo",
            "telefono": "+5491100000016", "nombre_cliente": None,
            "calendario": calendario(dt.date.today(), 8)}}
        for m in ["hola", "1", "3", "1", "1", "Ana Pérez", "sí"]:
            s = await g.ainvoke({"mensaje": m}, cfg)
        chequear("con el techo tocado igual se saca el turno",
                 s["estado"] == Estado.CONFIRMADO.value, f"estado={s['estado']}")
        chequear("y sale con su código", "Código:" in s["respuesta"])
    finally:
        GASTO.por_motivo = gastado


async def t14_el_chequeo_de_salud_no_se_paga_dos_veces():
    print(f"\n{NEGRITA}[14] /salud NO PAGA UNA LLAMADA POR PING{FIN}")
    print(f"{GRIS}  Lo pinchan dos automatismos; cada ping costaba una llamada al modelo.{FIN}")

    from src.api import webhook as W

    llamadas = []

    class ModeloFalso:
        async def ainvoke(self, _texto):
            return "ok"

    def construir(nombre, max_tokens=None, motivo=None):
        llamadas.append((nombre, max_tokens, motivo))
        return ModeloFalso()

    original, W._salud_llm = W.construir_modelo, None
    W.construir_modelo = construir
    try:
        for _ in range(5):
            piensa, quien, _ = await W._llm_responde()

        chequear("cinco pings, una sola llamada al modelo",
                 len(llamadas) == 1, f"{len(llamadas)} llamadas")
        chequear("y la respuesta sigue siendo la correcta", piensa and quien)

        # El tope es lo que evita pagar un párrafo que nadie lee.
        chequear("se le pide un solo token de respuesta",
                 llamadas[0][1] == 1, f"max_tokens={llamadas[0][1]}")
        chequear("y queda etiquetado como «salud» en el tablero de gasto",
                 llamadas[0][2] == "salud", f"motivo={llamadas[0][2]}")

        # Pero quien necesita el dato fresco lo puede pedir.
        await W._llm_responde(forzar=True)
        chequear("con ?profundo=1 sí vuelve a preguntar",
                 len(llamadas) == 2, f"{len(llamadas)} llamadas")

        # Vencida la caché, NO se bloquea: contesta con lo último que sabe y
        # pregunta aparte. Render corta a los 15 segundos y reinicia la
        # instancia si falla 60, así que su chequeo no puede quedar esperando a
        # que Anthropic conteste.
        W._salud_llm = (W._monotonic() - W.CACHE_SALUD_SEGUNDOS - 1,
                        (True, "el-de-antes", ""))
        antes = len(llamadas)
        r = await W._llm_responde()
        chequear("vencida la caché, contesta sin esperar al modelo",
                 len(llamadas) == antes and r[1] == "el-de-antes",
                 f"{len(llamadas) - antes} llamadas nuevas, quien={r[1]}")

        # Pero sí se entera después: una cuenta sin crédito tiene que poder
        # detectarse sin que nadie fuerce nada.
        await asyncio.sleep(0)
        chequear("y el refresco corre igual, por atrás",
                 len(llamadas) == antes + 1, f"{len(llamadas) - antes} llamadas nuevas")
    finally:
        W.construir_modelo, W._salud_llm = original, None


async def t13_failover_del_modelo():
    print(f"\n{NEGRITA}[13] SI EL PROVEEDOR SE CAE, CONTESTA EL RESPALDO{FIN}")
    print(f"{GRIS}  Pasó: se acabó el crédito y ningún cliente nuevo pudo sacar turno.{FIN}")

    from src.agentes import clasificador as C

    class CadenaFalsa:
        """Un clasificador de mentira, para probar el failover sin red."""

        def __init__(self, devuelve=None, explota=False):
            self.devuelve, self.explota, self.llamadas = devuelve, explota, 0

        async def ainvoke(self, _datos):
            self.llamadas += 1
            if self.explota:
                raise RuntimeError("credit balance is too low")
            return C.Clasificacion(intent=self.devuelve)

    async def clasificar(principal, respaldos):
        return await C.clasificar(principal, "Ana Pérez", Estado.ESPERANDO_NOMBRE,
                                  None, "2026-08-18", "martes", "", respaldos=respaldos)

    # 1. El principal anda: el respaldo ni se toca.
    C._anotar_exito()
    bueno, respaldo = CadenaFalsa(Intencion.DAR_NOMBRE), CadenaFalsa(Intencion.SALUDO)
    r = await clasificar(bueno, [("gemini", respaldo)])
    chequear("con el principal sano contesta el principal", r.intent == Intencion.DAR_NOMBRE)
    chequear("y no se le pregunta al respaldo", respaldo.llamadas == 0)

    # 2. El principal se cae: contesta el respaldo, no DESCONOCIDO.
    C._anotar_exito()
    muerto, respaldo = CadenaFalsa(explota=True), CadenaFalsa(Intencion.DAR_NOMBRE)
    r = await clasificar(muerto, [("gemini", respaldo)])
    chequear("con el principal caído contesta el respaldo",
             r.intent == Intencion.DAR_NOMBRE, f"intent={r.intent.value}")
    chequear("y el respaldo se usó una vez", respaldo.llamadas == 1)

    # 3. Y en los mensajes siguientes NO se vuelve a pagar la llamada fallida.
    #    Sin esto, con el proveedor caído cada mensaje paga su timeout.
    llamadas_antes = muerto.llamadas
    r = await clasificar(muerto, [("gemini", respaldo)])
    chequear("el principal caído no se reintenta en cada mensaje",
             muerto.llamadas == llamadas_antes,
             f"lo llamó {muerto.llamadas - llamadas_antes} vez más")
    chequear("y la respuesta sigue saliendo por el respaldo",
             r.intent == Intencion.DAR_NOMBRE)

    # 4. Pasado el descanso se vuelve a probar: la caída también se termina.
    chequear("mientras dura el descanso, el principal queda afuera",
             not C.principal_disponible(0),
             "medido con el reloj inyectado, sin esperar")
    chequear("pasado el descanso se lo vuelve a intentar",
             C.principal_disponible(10 ** 9))

    # 5. Sin ningún respaldo que conteste, la respuesta sigue siendo DESCONOCIDO
    #    y no una excepción: la persona nunca se queda sin respuesta.
    C._anotar_exito()
    r = await clasificar(CadenaFalsa(explota=True), [("gemini", CadenaFalsa(explota=True))])
    chequear("si tampoco contesta el respaldo, cae en DESCONOCIDO",
             r.intent == Intencion.DESCONOCIDO)
    C._anotar_exito()

    # 6. Un respaldo declarado sin credencial NO cuenta como respaldo. Es la
    #    diferencia entre cobertura y la apariencia de cobertura.
    from src.modelo import hay_credencial
    chequear("un proveedor sin clave no se toma como respaldo",
             not hay_credencial("openai"),
             "hoy OPENAI_API_KEY está vacía, así que no entra en la cadena")
    chequear("y el que tiene clave sí", hay_credencial("gemini"))


async def t9_el_borde_del_webhook():
    print(f"\n{NEGRITA}[9] LA PUERTA DE ENTRADA: NADIE SE QUEDA SIN RESPUESTA{FIN}")

    from src.api import webhook as W

    # Dedup: el mismo SID dos veces es el mismo mensaje.
    W._vistos.clear()
    chequear("un SID nuevo se atiende", not W._ya_procesado("SM123"))
    chequear("y el mismo SID repetido no", W._ya_procesado("SM123"))
    chequear("sin SID se atiende igual", not W._ya_procesado(""),
             "perder un mensaje es peor que mandar uno repetido")

    # Tope por minuto: se atiende hasta el tope, se avisa una vez, y basta.
    W._recientes.clear()
    atendidos = avisos = 0
    for _ in range(W.TOPE_POR_MINUTO + 5):
        atender, avisar = W._pasa_el_limite("+5491100000000", 1000.0)
        atendidos += atender
        avisos += avisar
    chequear(f"atiende exactamente {W.TOPE_POR_MINUTO} por minuto",
             atendidos == W.TOPE_POR_MINUTO, f"atendió {atendidos}")
    chequear("avisa una sola vez, no una por mensaje", avisos == 1, f"avisó {avisos}")

    # Y pasada la ventana vuelve a atender: es un freno, no un bloqueo.
    atender, _ = W._pasa_el_limite("+5491100000000", 1000.0 + W.VENTANA_SEGUNDOS + 1)
    chequear("pasada la ventana vuelve a atender", atender)

    # Otro teléfono no paga por el primero.
    atender, _ = W._pasa_el_limite("+5491199999999", 1000.0)
    chequear("el tope es por teléfono, no global", atender)


async def t20_al_fallar_dice_que_si_entendio(g):
    """El primer escalón de la reparación: explicar + ofrecer, no repetir.

    CHI 2019 (Ashktorab et al., N=203) comparó ocho estrategias de reparación:
    ganaron las OPCIONES y las EXPLICACIONES —muestran iniciativa y son
    accionables— y REPETIR quedó abajo. Hoy el primer escalón de este bot es
    repetir el pedido idéntico, sin una palabra de lo que sí llegó.

    El riesgo de arreglarlo mal es peor que el bug: si el bot AFIRMA haber
    entendido algo que no entendió, pasa de "no entiende" a "miente", que es
    el pecado que la literatura llama sobreestimar capacidades. Por eso la
    mitad de los casos de acá son negativos: qué NO se puede reflejar nunca.
    """
    print(f"\n{NEGRITA}[20] AL FALLAR, DICE QUÉ SÍ ENTENDIÓ{FIN}")
    print(f"{GRIS}  Explicar + ofrecer le gana a repetir. Pero sin inventar.{FIN}")

    manana = (dt.date.today() + dt.timedelta(days=1)).isoformat()

    # ---- Lo único que se puede reflejar: datos que parsean ----
    pista = P.pista_de({"fecha": manana})
    chequear("una fecha válida se refleja en palabras", bool(pista), repr(pista))
    chequear("y con el día en castellano, no en ISO",
             pista is not None and manana not in pista, repr(pista))

    pista_hora = P.pista_de({"hora": "15:30"})
    chequear("una hora válida también", pista_hora is not None and "15:30" in pista_hora,
             repr(pista_hora))

    # ---- Lo que NUNCA se refleja: texto libre del modelo ----
    #
    # `servicio`, `profesional`, `nombre` y `consulta` son prosa del modelo. Si
    # se devolvieran acá, el bot repetiría al usuario lo que el LLM alucinó y
    # lo presentaría como "lo que entendí".
    for campo in ("servicio", "profesional", "nombre", "consulta"):
        chequear(f"«{campo}» NO se refleja (es prosa del modelo)",
                 P.pista_de({campo: "cualquier cosa"}) is None)

    chequear("sin entidades no hay pista", P.pista_de({}) is None)
    chequear("ni con entidades en None", P.pista_de({"fecha": None}) is None)
    chequear("una fecha que no parsea no se refleja",
             P.pista_de({"fecha": "el jueves que viene"}) is None)
    chequear("una hora imposible tampoco", P.pista_de({"hora": "25:99"}) is None)

    # ---- Y la baranda que apareció escribiendo esto ----
    #
    # "no quiero el jueves" trae `fecha=jueves` EXACTAMENTE IGUAL que "quiero
    # el jueves": el clasificador extrae la entidad, no el signo. Sin este
    # chequeo el bot le contestaba "entendí que querés algo para el jueves" a
    # alguien que acababa de decir que el jueves no — y con cara de haberlo
    # entendido, que es peor que no entender.
    for niega in ("no quiero el jueves", "el jueves no", "no, ese día no",
                  "cualquiera menos el jueves", "nunca los jueves",
                  "tampoco el jueves"):
        chequear(f"«{niega}» no genera pista",
                 P.pista_de({"fecha": manana}, niega) is None)

    # Pero una frase normal con la misma entidad sí.
    chequear("«quiero algo para mañana» sí genera pista",
             P.pista_de({"fecha": manana}, "quiero algo para mañana") is not None)
    chequear("y «nomás» no cuenta como negación (no es «no»)",
             P.pista_de({"fecha": manana}, "para mañana nomas") is not None)

    # ---- Y de punta a punta, en el flujo ----
    #
    # Se llama a `avanzar` directo con la clasificación ya puesta: es la única
    # forma de probar esto sin LLM, y lo que se está probando es la rama del
    # flujo, no el clasificador.
    conv = {"mensaje": "quiero algo para mañana", "estado": Estado.ESPERANDO_SERVICIO.value,
            "intent": Intencion.DESCONOCIDO.value, "entidades": {"fecha": manana},
            "opciones": [], "sin_entender": 0}
    cambios = await F.avanzar(conv, _cfg("t20"))
    salida = await F.responder({**conv, **cambios}, _cfg("t20"))
    texto = salida["respuesta"]

    chequear("el bot nombra lo que sí entendió", "Entendí" in texto, texto[:70])
    chequear("y ofrece las opciones igual", "1." in texto and "Corte" in texto)
    chequear("sin decir que no entendió nada", "No te entendí" not in texto)

    # Basura no produce pista. Es la baranda que evita el bot mentiroso.
    for basura in ("!!!!", "...", "🙂🙂"):
        conv_b = {"mensaje": basura, "estado": Estado.ESPERANDO_SERVICIO.value,
                  "intent": Intencion.DESCONOCIDO.value, "entidades": {},
                  "opciones": [], "sin_entender": 0}
        c = await F.avanzar(conv_b, _cfg("t20b"))
        s = await F.responder({**conv_b, **c}, _cfg("t20b"))
        chequear(f"«{basura}» no inventa una pista", "Entendí" not in s["respuesta"])

    # ---- Y la salida a una persona, siempre a la vista ----
    #
    # 87% de los clientes dice que es esencial poder llegar a un humano
    # (Gartner, ago 2026), y la literatura de diseño es explícita en que
    # esconderla al pie en letra chica no cuenta. Cada paso la nombra.
    #
    # UNA sola vez, y por eso está el segundo chequeo: cuando el pedido del paso
    # ya la trae, agregarla otra vez en `no_entendi` la repetía en el mismo
    # mensaje — que es la forma más rápida de que deje de leerse.
    conv_1 = {"mensaje": "asdasd", "estado": Estado.ESPERANDO_SERVICIO.value,
              "intent": Intencion.DESCONOCIDO.value, "entidades": {},
              "opciones": [], "sin_entender": 0}
    s1 = await F.responder({**conv_1, **await F.avanzar(conv_1, _cfg("t20c"))}, _cfg("t20c"))
    chequear("al fallar, la salida está a la vista",
             "una persona" in s1["respuesta"], s1["respuesta"][-60:])
    chequear("y aparece UNA sola vez",
             s1["respuesta"].count("una persona") == 1,
             f"{s1['respuesta'].count('una persona')} veces")


async def t21_la_metrica_no_puede_tumbar_el_turno(g):
    """Contar conversaciones es secundario. Reservar no.

    La regla ya está escrita en `arranque.sh` —que no usa `set -e` a propósito—
    y en Phoenix, que falla blando. Acá se verifica de punta a punta y no sólo
    en el módulo: la llamada a `registrar` vive en el webhook, entre el grafo y
    la respuesta, y es exactamente el lugar donde una excepción se llevaría
    puesto un turno ya creado en aturno.
    """
    print(f"\n{NEGRITA}[21] SI LA MÉTRICA SE CAE, EL TURNO SE RESERVA IGUAL{FIN}")
    print(f"{GRIS}  Lo secundario no puede tumbar lo principal.{FIN}")

    from src import metricas as M

    original, M._URL = M._URL, "postgresql://nadie@127.0.0.1:1/no-existe"
    M.olvidar_pool()
    try:
        await hasta_el_resumen(g, "t21")
        s = await g.ainvoke({"mensaje": "sí"}, _cfg("t21", "Ana Pérez"))
        chequear("con la base de métricas caída, el turno se reserva",
                 s["estado"] == Estado.CONFIRMADO.value, f"estado={s['estado']}")
        chequear("y la persona recibe su código", "#" in s["respuesta"] or "código" in
                 s["respuesta"].lower(), s["respuesta"][:60])

        # Y leer tampoco explota: devuelve ceros, que es lo que hay.
        vacio = await M.resumen()
        chequear("y el resumen contesta ceros en vez de romper",
                 vacio["cerradas"] == 0 and vacio["containment"] is None)
    finally:
        M._URL = original
        M.olvidar_pool()


async def t22_ninguna_lista_vacia_pide_un_numero(g):
    """Anunciar una lista y no mostrarla es peor que no anunciarla.

    Apareció verificando las métricas: un negocio sin servicios cargados —el
    estado normal de cualquiera que recién se da de alta— recibía

        Elegí el servicio:

        Respondé con el número.

    o sea que se le pide a la persona que conteste con un número que no existe.
    Y encima el bot no puede aceptar ninguna respuesta, así que la conversación
    entra en el bucle del pecado 2: el callejón donde todo lo que escribís
    devuelve lo mismo.

    `apertura` ya se protege de esto desde que pasó en producción; el resto de
    los listados no. Este caso los cubre a todos, para que un listado nuevo no
    reestrene el mismo agujero.
    """
    print(f"\n{NEGRITA}[22] NINGUNA LISTA VACÍA PIDE UN NÚMERO{FIN}")
    print(f"{GRIS}  Un negocio recién dado de alta no tiene nada cargado.{FIN}")

    vacias = {
        "servicios": P.lista_servicios([]),
        "días": P.selector_dias([]),
        "horarios": P.lista_horarios(dt.date.today(), []),
    }
    for que, texto in vacias.items():
        chequear(f"sin {que}, no pide un número",
                 "con el número" not in texto and "un número" not in texto,
                 repr(texto[:70]))
        chequear(f"sin {que}, dice qué pasa y ofrece una salida",
                 "una persona" in texto or "local" in texto, repr(texto[:70]))


async def t23_otro_turno_no_es_otro_horario(g):
    """«Quiero otro turno» es un pedido nuevo, no «mostrame más horarios».

    Lo encontró `test_clasificador.py` en su primera corrida, y con dato: la
    matriz de confusión mostró `elegir_servicio → ver_mas ×2`. La regla del
    prompt decía «"más", "otro horario", "más tarde" -> ver_mas» y el modelo
    generalizaba de "otro horario" a "otro turno", que no tienen nada que ver.

    Hoy la conversación TERMINA bien igual, pero por casualidad: el reinicio de
    CONFIRMADO la lleva a la apertura antes de que la intención equivocada haga
    daño. Andar de casualidad es exactamente lo que este repo resuelve con
    tablas, así que va a la tabla — y gratis, sin llamar al modelo.
    """
    print(f"\n{NEGRITA}[23] «OTRO TURNO» NO ES «OTRO HORARIO»{FIN}")
    print(f"{GRIS}  Lo encontró la matriz de confusión del conjunto dorado.{FIN}")

    for frase in ("otro turno", "quiero otro turno", "quiero sacar otro turno",
                  "otro turno mas", "uno mas", "quiero otro"):
        fija = respuesta_fija(frase, Estado.CONFIRMADO)
        chequear(f"«{frase}» es un pedido nuevo, sin modelo",
                 fija is not None and fija[0] == Intencion.ELEGIR_SERVICIO,
                 str(fija[0].value) if fija else "None")

    # Y donde SÍ significa más horarios, sigue significando eso.
    fija = respuesta_fija("mas horarios", Estado.ESPERANDO_HORARIO)
    chequear("«más horarios» en el paso del horario sigue siendo ver_mas",
             fija is not None and fija[0] == Intencion.VER_MAS,
             str(fija[0].value) if fija else "None")

    # De punta a punta: después de reservar, pedir otro arranca de cero.
    await hasta_el_resumen(g, "t23")
    await g.ainvoke({"mensaje": "sí"}, _cfg("t23", "Ana Pérez"))
    s = await g.ainvoke({"mensaje": "quiero otro turno"}, _cfg("t23", "Ana Pérez"))
    chequear("y la conversación arranca un pedido nuevo",
             s["estado"] != Estado.CONFIRMADO.value, f"estado={s['estado']}")
    chequear("sin mostrar un error", "complicó" not in s["respuesta"])


async def t24_buscador_caido_no_es_dato_faltante(g):
    """El buscador caído y el dato no cargado NO son lo mismo.

    Apareció de verdad: se agotó la cuota diaria de embeddings de Google —1.000
    por día en el plan gratuito— y a partir de ahí el bot le contestaba a cada
    persona «ese dato no lo tengo cargado». Es mentira: el negocio SÍ lo tiene
    cargado, lo que no funciona es la búsqueda.

    Y hace un daño que nadie ve: cada una de esas preguntas se le manda al panel
    como «alguien preguntó algo que no tenés contestado», así que el negocio
    abre su panel y encuentra una lista de preguntas que YA respondió. Después
    de dos o tres veces, deja de mirar el panel — y ahí las preguntas que sí
    faltaban tampoco las ve.

    El comentario del código ya decía que había que distinguirlos. El código no
    lo hacía.
    """
    print(f"\n{NEGRITA}[24] EL BUSCADOR CAÍDO NO ES UN DATO QUE FALTA{FIN}")
    print(f"{GRIS}  Pasó de verdad: se agotó la cuota de embeddings.{FIN}")

    from src.api import conversaciones as C

    avisos = []

    async def espiar(negocio, consulta):
        avisos.append((negocio, consulta))

    class Caido:
        async def contexto_y_cuantos(self, consulta):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

        def temas(self):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    class SinDato:
        async def contexto_y_cuantos(self, consulta):
            return "", 0

        def temas(self):
            return ["Servicios y precios", "Horarios de atención"]

    orig_rag, orig_avisar = F._rag, F.avisar_sin_respuesta
    F.avisar_sin_respuesta = espiar

    async def preguntar(hilo):
        conv = {"mensaje": "aceptan tarjeta?", "estado": Estado.ESPERANDO_SERVICIO.value,
                "intent": Intencion.CONSULTAR_INFO.value, "entidades": {},
                "opciones": [], "sin_entender": 0}
        cambios = await F.avanzar(conv, _cfg(hilo))
        salida = await F.responder({**conv, **cambios}, _cfg(hilo))
        await asyncio.sleep(0)   # que corra el aviso en segundo plano, si sale
        return salida["respuesta"]

    try:
        # ---- El buscador caído ----
        F._rag = lambda n: Caido()
        avisos.clear()
        texto = await preguntar("t24a")
        chequear("NO le dice que el dato no está cargado",
                 "no lo tengo cargado" not in texto.lower(), texto[:60])
        chequear("dice que ahora no puede consultarlo",
                 "ahora" in texto.lower() or "en un rato" in texto.lower(), texto[:60])
        chequear("y ofrece una persona", "persona" in texto.lower())
        chequear("NO le llena el panel al negocio con algo que sí tiene",
                 not avisos, str(avisos))

        # ---- El dato que de verdad no está ----
        F._rag = lambda n: SinDato()
        avisos.clear()
        texto = await preguntar("t24b")
        chequear("acá SÍ dice que no lo tiene cargado",
                 "no lo tengo cargado" in texto.lower(), texto[:60])
        chequear("y nombra de qué sí puede hablar", "Servicios y precios" in texto)
        chequear("y SÍ le avisa al panel", len(avisos) == 1, str(avisos))
    finally:
        F._rag, F.avisar_sin_respuesta = orig_rag, orig_avisar


async def t25_no_se_tira_lo_que_ya_dijo(g, doble):
    """Lo que la persona ya dijo NO se vuelve a preguntar.

    El clasificador entiende «necesito cortarme el pelo el viernes a la tarde»
    entero: devuelve servicio, fecha y hora en la misma respuesta, y ya está
    paga. El flujo agarraba el servicio, avanzaba UN paso y tiraba el resto —
    así que a esa persona le preguntaba el día que acababa de decir.

    Es el pecado 8 de la investigación: demasiadas preguntas antes de mostrar
    algo. "Si tu chatbot hace diez preguntas antes de que el cliente vea un solo
    horario, lo perdés."

    DOS BARANDAS, y las dos son la razón de que esto sea seguro:

      · Se avanza SÓLO mientras cada paso resuelva sin ambigüedad. `_resolver`
        ya devuelve None cuando el nombre coincide con dos servicios o con
        ninguno, y ahí se frena y se muestra la lista.
      · NUNCA se llega solo a la confirmación. El resumen sigue estando siempre,
        y nada se reserva sin que la persona diga que sí.
    """
    print(f"\n{NEGRITA}[25] LO QUE YA DIJO NO SE VUELVE A PREGUNTAR{FIN}")
    print(f"{GRIS}  El clasificador ya lo entendió entero. No se tira.{FIN}")

    servicios = await doble.listar_servicios(NEG)
    gente = await doble.listar_personal(NEG)
    manana = (dt.date.today() + dt.timedelta(days=1)).isoformat()

    async def con(entidades, estado=Estado.ESPERANDO_SERVICIO,
                  intent=Intencion.ELEGIR_SERVICIO, mensaje="quiero un corte"):
        conv = {"mensaje": mensaje, "estado": estado.value, "intent": intent.value,
                "entidades": entidades, "opciones": [], "sin_entender": 0,
                "servicio_id": None, "profesional_id": None, "fecha": None,
                "hora": None, "desde_horario": 0}
        return await F.avanzar(conv, _cfg("t25"))

    # ---- Servicio + día, con el paso del profesional en el medio ----
    #
    # Acá NO se puede saltar al horario de una: quién te atiende es una elección
    # que la persona no hizo. Lo que sí tiene que pasar es que el día NO se
    # pierda — y que cuando elija al profesional, el bot salte el día solo.
    c = await con({"servicio": servicios[0].nombre, "fecha": manana})
    chequear("frena en el profesional, que no eligió",
             c.get("estado") == Estado.ESPERANDO_STAFF.value, f"paso={c.get('estado')}")
    chequear("pero NO tira el día: lo guarda para su paso",
             (c.get("_pendientes") or {}).get("fecha") == manana,
             str(c.get("_pendientes")))

    # Y de punta a punta: elige al profesional y el día ya no se pregunta.
    s1 = await g.ainvoke({"mensaje": "hola"}, _cfg("t25e"))
    conv2 = {"mensaje": "un corte", "estado": Estado.ESPERANDO_SERVICIO.value,
             "intent": Intencion.ELEGIR_SERVICIO.value,
             "entidades": {"servicio": servicios[0].nombre, "fecha": manana},
             "opciones": [], "sin_entender": 0}
    c2 = await F.avanzar(conv2, _cfg("t25e"))
    conv3 = {**conv2, **c2, "mensaje": gente[0].nombre,
             "intent": Intencion.ELEGIR_STAFF.value,
             "entidades": {"profesional": gente[0].nombre}}
    c3 = await F.avanzar(conv3, _cfg("t25e"))
    chequear("al elegir al profesional, el día YA está y no se pregunta",
             c3.get("fecha") == manana and c3.get("estado") == Estado.ESPERANDO_HORARIO.value,
             f"fecha={c3.get('fecha')} paso={c3.get('estado')}")

    # ---- Servicio + profesional + día ----
    c = await con({"servicio": servicios[0].nombre, "profesional": gente[0].nombre,
                   "fecha": manana})
    chequear("con los tres, también guarda al profesional",
             c.get("profesional_id") == gente[0].id, str(c.get("profesional_id")))

    # ---- Una franja NO elige el horario por la persona ----
    #
    # "a la tarde" le llega al clasificador como hora=14:00, pero la persona no
    # dijo las 14: dijo la tarde. Reservarle las 14 sería elegir por ella.
    c = await con({"servicio": servicios[0].nombre, "fecha": manana, "hora": "14:00"},
                  mensaje="quiero un corte mañana a la tarde")
    chequear("«a la tarde» ni siquiera se guarda como hora",
             "hora" not in (c.get("_pendientes") or {}), str(c.get("_pendientes")))

    # ---- Pero una hora escrita con números SÍ vale ----
    c = await con({"servicio": servicios[0].nombre, "profesional": gente[0].nombre,
                   "fecha": manana, "hora": "10:00"},
                  mensaje="quiero un corte con Lean mañana a las 10")
    chequear("«a las 10» sí se toma", c.get("hora") == "10:00", str(c.get("hora")))
    chequear("y ahí sí llega hasta el nombre o el resumen",
             c.get("estado") in (Estado.ESPERANDO_NOMBRE.value,
                                 Estado.ESPERANDO_CONFIRMACION.value),
             f"paso={c.get('estado')}")

    # ---- Un servicio ambiguo frena y muestra la lista ----
    c = await con({"servicio": "algo", "fecha": manana})
    chequear("un servicio que no existe NO avanza a ciegas",
             c.get("_plantilla") == "no_entendi" or c.get("servicio_id") is None,
             str(c)[:60])

    # ---- Y NUNCA se reserva solo ----
    c = await con({"servicio": servicios[0].nombre, "profesional": gente[0].nombre,
                   "fecha": manana, "hora": "10:00", "nombre": "Ana Pérez"},
                  mensaje="corte con Lean mañana a las 10, soy Ana Pérez")
    chequear("con TODO junto, igual para en la confirmación",
             c.get("estado") == Estado.ESPERANDO_CONFIRMACION.value, f"paso={c.get('estado')}")
    chequear("y NO reservó nada todavía", not c.get("codigo"), str(c.get("codigo")))

    # ---- Y el PRIMER mensaje también cuenta ----
    #
    # La apertura sale siempre en una sesión nueva —dice quién es el bot, qué
    # hace el negocio y dónde está la salida— y eso no se toca. Lo que sí se
    # arregló: además de saludar, aplica lo que la persona dijo en ese mismo
    # mensaje. Antes lo tiraba, así que quien abría con «un corte el viernes»
    # recibía el menú entero como si no hubiera escrito nada.
    c = await con({"servicio": servicios[0].nombre, "fecha": manana},
                  estado=Estado.APERTURA, mensaje="hola, quiero un corte mañana")
    chequear("el primer mensaje sigue mostrando la apertura",
             c.get("_plantilla") == "apertura", str(c.get("_plantilla")))
    chequear("pero también toma el servicio que dijo",
             c.get("servicio_id") == servicios[0].id, str(c.get("servicio_id")))
    chequear("y no vuelve a pedir el servicio",
             c.get("estado") != Estado.ESPERANDO_SERVICIO.value, f"paso={c.get('estado')}")

    # Un saludo pelado no arrastra nada: la apertura de siempre.
    c = await con({}, estado=Estado.APERTURA, mensaje="hola")
    chequear("un «hola» pelado deja la apertura como estaba",
             c.get("estado") == Estado.ESPERANDO_SERVICIO.value, f"paso={c.get('estado')}")

    # ---- Un número suelto no se aplica a todos los pasos ----
    #
    # "1" trae `_indice` y significa "el renglón 1 de la lista que estoy
    # viendo". Aplicarlo también al paso siguiente elegiría el profesional 1 y
    # el día 1 sin que nadie los haya mirado.
    c = await con({"_indice": 0}, mensaje="1")
    chequear("un «1» elige el servicio y NADA más",
             c.get("profesional_id") is None and c.get("fecha") is None,
             f"prof={c.get('profesional_id')} fecha={c.get('fecha')}")


async def t26_una_pregunta_no_es_un_nombre(g):
    """Preguntar algo en el paso del nombre no puede quedar como tu nombre.

    Salió corriendo el guion de una demo: en «¿cómo te llamás?» alguien escribe
    «hacen depilación láser?» y el resumen decía

        Para: Hacen Depilación Láser?

    Nadie se llama así, y la persona lo ve recién en el resumen — o no lo ve, y
    el negocio recibe un turno a nombre de una pregunta.

    Pasaba porque el atajo de nombres mira si el texto PARECE un nombre —dos o
    tres palabras, sin números, ninguna en la lista de palabras que no son
    nombres— y una pregunta corta pasa ese filtro sin problema. El signo de
    pregunta es la señal que faltaba mirar.
    """
    print(f"\n{NEGRITA}[26] UNA PREGUNTA NO ES UN NOMBRE{FIN}")
    print(f"{GRIS}  «Para: Hacen Depilación Láser?» — salió de verdad.{FIN}")

    from src.agentes.estados import nombre_propio

    for pregunta in ("hacen depilación láser?", "cuánto sale?", "aceptan tarjeta?",
                     "¿dónde quedan?", "tienen estacionamiento?",
                     "puedo pagar en cuotas?"):
        chequear(f"«{pregunta}» no se toma como nombre",
                 nombre_propio(pregunta) is None, repr(nombre_propio(pregunta)))

    # Y los nombres de verdad siguen entrando, incluidos los raros.
    for nombre in ("Matías Calo", "Ana", "María José Pérez", "Pitu Ehrman",
                   "me llamo Lucas", "soy Juan Cruz"):
        chequear(f"«{nombre}» sigue siendo un nombre",
                 nombre_propio(nombre) is not None, repr(nombre_propio(nombre)))

    # De punta a punta: preguntar en el paso del nombre CONTESTA la pregunta.
    conv = {"mensaje": "hacen depilación láser?", "estado": Estado.ESPERANDO_NOMBRE.value,
            "intent": Intencion.CONSULTAR_INFO.value, "entidades": {},
            "opciones": [], "sin_entender": 0}
    c = await F.avanzar(conv, _cfg("t26"))
    chequear("y en el flujo no queda guardada como nombre",
             not c.get("nombre") and not c.get("nombre_del_turno"),
             f"nombre={c.get('nombre')} turno={c.get('nombre_del_turno')}")


async def main():
    doble = AturnoDoble()
    F.configurar(doble)
    g = F.construir_flujo(MemorySaver())

    for prueba in (t1_el_no_no_reserva, t2_el_no_no_borra_nada,
                   t3_cancelar_sigue_borrando, t4_no_te_entendi,
                   t5_no_hay_callejon, t6_gracias_no_es_un_pedido_nuevo,
                   t7_la_sesion_vence):
        await prueba(g)
    await t8_sin_llm_para_lo_previsible()
    await t9_el_borde_del_webhook()
    await t10_la_senia(g, doble)
    await t11_sin_link_no_hay_turno(g, doble)
    await t12_el_aviso_del_pago()
    await t13_failover_del_modelo()
    await t14_el_chequeo_de_salud_no_se_paga_dos_veces()
    await t15_el_nombre_no_gasta_modelo()
    await t16_el_techo_de_gasto_degrada_sin_romper()
    await t17_ningun_paso_termina_en_el_error_tecnico()
    await t18_se_puede_corregir_el_nombre()
    await t19_todas_las_formas_de_negar_el_nombre()
    await t20_al_fallar_dice_que_si_entendio(g)
    await t21_la_metrica_no_puede_tumbar_el_turno(g)
    await t22_ninguna_lista_vacia_pide_un_numero(g)
    await t23_otro_turno_no_es_otro_horario(g)
    await t24_buscador_caido_no_es_dato_faltante(g)
    await t25_no_se_tira_lo_que_ya_dijo(g, doble)
    await t26_una_pregunta_no_es_un_nombre(g)

    print(f"\n{'─' * 58}")
    print(f"{VERDE}Todo en verde.{FIN}" if ok else f"{ROJO}Hay bordes rotos.{FIN}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

