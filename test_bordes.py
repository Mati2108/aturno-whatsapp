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

    # Pero pedir algo de verdad sí arranca un pedido nuevo.
    s = await g.ainvoke({"mensaje": "quiero otro turno"}, _cfg("t6", "Ana Pérez"))
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

    print(f"\n{'─' * 58}")
    print(f"{VERDE}Todo en verde.{FIN}" if ok else f"{ROJO}Hay bordes rotos.{FIN}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
