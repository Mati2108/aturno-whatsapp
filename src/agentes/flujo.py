"""
flujo.py — El orquestador: máquina de estados + plantillas + aturno, sobre LangGraph.

EL GRAFO
--------
    INICIO → entender → avanzar → responder → FIN

Tres responsabilidades separadas a propósito:

    entender   qué quiso decir la persona. Un número se resuelve en código;
               solo el texto libre llega al LLM.
    avanzar    qué paso sigue y qué se guardó. Determinístico, sin LLM.
    responder  qué texto sale. Siempre de plantillas.

El checkpointer de Postgres guarda el estado entre mensajes: en qué paso está
la conversación, qué eligió y quién es. Eso es a la vez la sesión del producto
y la persistencia que pide el Capstone.

LO QUE ESTE DISEÑO HACE IMPOSIBLE
---------------------------------
- Que el saludo salga distinto dos veces: lo escribe una plantilla.
- Que un listado salga horizontal: las plantillas usan "\\n".
- Que el usuario vea un JSON: el clasificador devuelve un objeto que nunca se
  imprime; lo que sale es siempre plantilla.
- Que se saltee un paso: `avanzar` solo consulta la tabla ORDEN.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import NotRequired, TypedDict

from src import plantillas as P
from src.agentes.clasificador import (
    Clasificacion, clasificar, construir_clasificador, construir_respaldos)
from src.agentes.estados import (
    AVANZA_CON,
    ELIGE_DE_LISTA,
    ORDEN,
    Estado,
    Intencion,
    anterior,
    afirmacion_sobre_lo_unico,
    dice_que_pago,
    numero_elegido,
    opcion_por_nombre,
    es_numero_suelto,
    pedido_de_cambio,
    respuesta_fija,
    siguiente,
    sin_contenido,
)
from src.aturno.base import ClienteAturno
# Alias a propósito: los nodos de LangGraph reciben un parámetro llamado
# `config`, que tapaba a esta función y la volvía un dict. El error salía
# recién al usarla, y decía "'dict' object is not callable" — que no señala
# a ningún lado.
from src.config import config as ajustes
from src.escalacion import Escalacion, notificar
from src.fechas import ahora
from src.fechas import hoy as hoy_del_negocio
from src.api.conversaciones import avisar_sin_respuesta
from src.rag.indice import Recuperador, abrir_indice
from src.schemas import (
    Alternativa,
    DatosDelCliente,
    MotivoNoDisponible,
    SinLugar,
    limpiar_nombre,
)

logger = logging.getLogger("pipeline.flujo")

DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# Hasta dónde se lee un mensaje. Un pedido de turno real no pasa de dos
# renglones; lo que venga después es ruido, y ese ruido se paga: cada carácter
# entra al prompt del clasificador. Sin tope, alguien puede mandar diez mil
# caracteres y hacerle gastar plata al negocio sin sacar ningún turno.
#
# Se recorta en vez de rechazar porque la intención suele estar al principio:
# quien pega un texto largo igual escribió "quiero un turno" en la primera
# línea, y rechazarlo lo dejaría sin respuesta por algo que se entendía.
MAX_MENSAJE = 400

# Cuántos mensajes seguidos sin entender antes de ofrecer una persona. Dos y
# no tres: al tercer "no te entendí" idéntico, la gente ya se fue.
LIMITE_SIN_ENTENDER = 2

# Y cuántos antes de ofrecerle una salida a quien NUNCA eligió nada.
#
# Escalar exige que la conversación haya avanzado (ver `_hubo_avance`): sin esa
# condición, dos mensajes de basura desde cualquier número le hacen sonar el
# teléfono al dueño. Pero el que no avanzó tampoco puede quedar girando sobre
# el mismo pedido para siempre, que es lo que pasaba: el contador subía a 2,
# volvía a 0 y arrancaba de nuevo, sin que nada cambiara nunca.
#
# Más alto que el otro a propósito. Acá no se avisa a nadie: se ofrecen las dos
# puertas que la persona puede abrir sola —el link y pedir una persona con
# todas las letras—, así que equivocarse cuesta un mensaje, no una molestia.
LIMITE_ATASCADO = 4

# Cuánto puede errarle alguien a un horario y que siga siendo un tipeo.
#
# "9:39" con turnos cada media hora es un dedo que se fue, no un pedido. Corto
# a propósito: con media hora de tolerancia esto dejaría de corregir un tipeo y
# pasaría a elegir por la persona, que es otra cosa.
TOLERANCIA_TIPEO = 15   # minutos

# Cuántos horarios entran en un mensaje. Veinte horarios en un celular obligan
# a hacer scroll para elegir, y elegir es justo lo que la persona vino a hacer.
# El resto se pide con "más" (Intencion.VER_MAS).
PAGINA = 8

_aturno: ClienteAturno | None = None
_recuperadores: dict[str, Recuperador] = {}
_clasificador = None
# Las cadenas de otros proveedores, para cuando el principal no conteste. Se
# arman al configurar y no en el momento de la falla: ver `construir_respaldos`.
_respaldos: list[tuple[str, object]] = []


def configurar(cliente: ClienteAturno) -> None:
    global _aturno, _clasificador, _respaldos
    _aturno = cliente
    _clasificador = construir_clasificador()
    _respaldos = construir_respaldos()


def _rag(business_id: str) -> Recuperador:
    if business_id not in _recuperadores:
        _recuperadores[business_id] = Recuperador(business_id, abrir_indice())
    return _recuperadores[business_id]


def olvidar_recuperador(business_id: str) -> None:
    """Tira el recuperador cacheado de un negocio.

    Se llama después de reindexar. El objeto guarda la conexión al índice
    abierta desde que se creó, así que sin esto el negocio contesta el
    formulario, ve que se guardó, y el bot le sigue diciendo "ese dato no lo
    tengo cargado" hasta el próximo reinicio del servicio.
    """
    _recuperadores.pop(business_id, None)


class Conversacion(TypedDict):
    """El estado que persiste entre mensajes."""

    mensaje: str
    estado: str                       # Estado.value
    respuesta: NotRequired[str]

    # Lo que la persona fue eligiendo
    servicio_id: NotRequired[str | None]
    profesional_id: NotRequired[str | None]
    fecha: NotRequired[str | None]    # ISO
    hora: NotRequired[str | None]     # HH:MM

    # DOS nombres, y confundirlos fue un bug real.
    #
    # `nombre` es QUIÉN SOS: lo que contestaste cuando el bot preguntó cómo te
    # llamás. Persiste entre conversaciones y es lo que hace que la segunda vez
    # no te lo vuelva a preguntar.
    #
    # `nombre_del_turno` es A NOMBRE DE QUIÉN va ESTA reserva, y sólo aparece
    # cuando es distinto del contacto: alguien saca un turno para su hija y lo
    # corrige en el resumen. Es de una reserva, no de la persona, así que se
    # limpia al reservar.
    #
    # Estaban unificados. El "nombre recordado" se leía del estado guardado
    # —`previo.values["nombre"]`— así que corregir el nombre en la confirmación
    # para reservarle a otro te reescribía la identidad: a partir de ahí el bot
    # te saludaba con el nombre de tu hija y le ponía ese nombre a todos tus
    # turnos siguientes, sin que nada lo mostrara.
    nombre: NotRequired[str | None]
    nombre_del_turno: NotRequired[str | None]

    # Las opciones que se mostraron en el último mensaje. Sin esto no se puede
    # resolver "3": hay que saber contra qué lista.
    opciones: NotRequired[list[str]]

    # Desde qué horario arranca la lista que se está mostrando. Avanza cuando
    # la persona pide "más" y vuelve a cero al cambiar de día — si no, elegir
    # otro día heredaría el desplazamiento del anterior y la lista empezaría
    # por el medio sin razón visible.
    desde_horario: NotRequired[int]

    # Cuántos mensajes seguidos no se entendieron. Se resetea con cualquier
    # mensaje que sí avance o que el clasificador reconozca.
    sin_entender: NotRequired[int]

    # Cuándo fue el último mensaje de esta conversación, en ISO. Es lo único
    # que hace falta para que la sesión pueda vencer: sin esto, quien llegó al
    # resumen y volvió tres semanas después seguía parado en el mismo paso, y
    # un "dale" confirmaba la fecha de entonces — que ya pasó.
    ultimo_en: NotRequired[str | None]

    # El código del turno que está esperando la seña. Con esto el webhook puede
    # preguntarle a aturno si el pago entró, que es la única forma de avisarle a
    # la persona: paga en Mercado Pago, cierra la pestaña, y del lado del chat
    # lo último que leyó fue "te falta pagar".
    codigo_pendiente: NotRequired[str | None]

    # Qué turno es ese, para poder anunciarlo cuando el pago aparezca. Sobrevive
    # entre mensajes a propósito: el aviso del pago llega tarde —lo trae el
    # vigilante, o el mensaje siguiente de la persona— y para entonces el
    # scratch del turno (`_datos`) ya se borró.
    turno_pendiente: NotRequired[dict | None]

    # Cuando la conversación pasa al negocio: en qué paso estaba y desde cuándo
    # está esperando. Sin el paso previo, volver del modo humano tiraría a la
    # persona al principio y le haría repetir todo.
    estado_previo: NotRequired[str | None]
    humano_desde: NotRequired[str | None]

    # Resultado del paso `entender`, para que `avanzar` lo lea
    intent: NotRequired[str]
    entidades: NotRequired[dict]

    # Claves de un solo turno: `avanzar` le avisa a `responder` que use una
    # plantilla puntual en vez de la del paso. Van declaradas porque LangGraph
    # descarta cualquier clave que no esté en el esquema — se perdían en
    # silencio entre nodos y la confirmación salía como error técnico.
    # Se limpian al empezar cada mensaje: son de este turno, no de la sesión.
    # Solo primitivos: el checkpointer serializa a Postgres y guardar objetos
    # Pydantic ahí genera avisos de deserialización y ata el formato guardado
    # a la clase de hoy. Si el esquema cambia, las sesiones viejas se rompen.
    _plantilla: NotRequired[str | None]
    _datos: NotRequired[dict | None]
    # Si este mensaje llegó después del vencimiento de la sesión. Lo calcula
    # `entender`, que es el único nodo que todavía ve el sello anterior.
    _vencida: NotRequired[bool]


# ══════════════════════════════════════════════════════════════════
# Nodo 1 · entender
# ══════════════════════════════════════════════════════════════════

async def entender(conv: Conversacion, config) -> dict:
    """Un número se resuelve gratis; el texto libre va al clasificador."""
    estado = Estado(conv.get("estado") or Estado.APERTURA.value)
    opciones = conv.get("opciones") or []
    texto = conv["mensaje"]

    # Lo efímero del turno anterior no puede sobrevivir: si no, la plantilla
    # de confirmación se repetiría en el mensaje siguiente.
    #
    # `_vencida` se calcula ACÁ y no en `avanzar` porque acá todavía se puede
    # leer el sello del mensaje ANTERIOR: los nodos corren en orden sobre el
    # mismo estado, así que para cuando `avanzar` mirara `ultimo_en` ya sería
    # el de este mensaje y la resta daría cero siempre.
    limpio_turno = {
        "_plantilla": None,
        "_datos": None,
        "_vencida": _sesion_vencida(conv.get("ultimo_en")),
        "ultimo_en": ahora().isoformat(),
    }

    # Sin una sola letra ni número no hay nada que clasificar: un "👋", un
    # "..." o tres espacios en blanco. El modelo sólo puede devolver
    # DESCONOCIDO —que ya se sabe antes de preguntar— y cobra la llamada igual.
    if sin_contenido(texto):
        logger.info("sin contenido «%s» → desconocido (sin LLM)", texto[:24])
        return {**limpio_turno, "intent": Intencion.DESCONOCIDO.value, "entidades": {}}

    # Por número o por nombre: las dos cosas valen. La lista está en pantalla,
    # así que "2" y "Matias Calo" señalan lo mismo y ninguno necesita al modelo.
    #
    # SÓLO en los pasos donde contestar es señalar un renglón (`ELIGE_DE_LISTA`).
    # Fuera de ellos `opciones` viaja igual al clasificador como contexto, pero
    # no se indexa: en el paso de confirmación las opciones son "sí" y "no", y
    # tratarlas como renglones convertía al "no" —el renglón 2— en la intención
    # que AVANZA el paso, o sea en un turno reservado contra lo que la persona
    # acababa de contestar. Está contado en el comentario de `ELIGE_DE_LISTA`.
    if estado in ELIGE_DE_LISTA:
        indice = numero_elegido(texto, len(opciones))
        como = "número"
        if indice is None:
            indice = opcion_por_nombre(texto, opciones)
            como = "nombre"
        if indice is None:
            indice = afirmacion_sobre_lo_unico(texto, opciones)
            como = "sí"
        if indice is not None:
            logger.info("%s «%s» → %s (sin LLM)", como, texto[:24], opciones[indice])
            return {
                **limpio_turno,
                "intent": (AVANZA_CON.get(estado) or Intencion.DESCONOCIDO).value,
                "entidades": {"_indice": indice},
            }

    # En el resumen no hay ninguna lista numerada, así que un número suelto no
    # señala nada: es alguien contestando la pantalla ANTERIOR, que sí la
    # tenía. Se pregunta de nuevo en vez de interpretarlo, y sin gastar modelo.
    #
    # Es el único paso donde vale la pena esta guarda, porque es el único donde
    # el movimiento siguiente es irreversible: confirmar crea el turno. Un
    # mensaje de más cuesta un mensaje; un turno que la persona no pidió le
    # cuesta el lugar al negocio y un viaje al cliente. La misma regla que ya
    # aplica `opcion_por_nombre` con los nombres ambiguos.
    if estado == Estado.ESPERANDO_CONFIRMACION and es_numero_suelto(texto):
        logger.info("número suelto «%s» en el resumen: no lo interpreto", texto[:16])
        return {**limpio_turno, "intent": Intencion.DESCONOCIDO.value, "entidades": {}}

    # Las frases de siempre ("dale", "me da igual", "hablar con alguien") no
    # necesitan al modelo: significan lo mismo todas las veces. Cada una que se
    # resuelve acá ahorra la llamada ENTERA, que son ~1.677 tokens de entrada
    # aunque el mensaje sean tres letras.
    fija = respuesta_fija(texto, estado)
    if fija is not None:
        intencion, entidades = fija
        logger.info("«%s» → %s (frase fija, sin LLM)", texto[:24], intencion.value)
        return {**limpio_turno, "intent": intencion.value, "entidades": entidades}

    # "Mejor cambio el servicio" es un pedido concreto, no un "volver" genérico.
    # Se resuelve acá porque el modelo lo leía como VOLVER —que retrocede un
    # solo paso— y la persona terminaba eligiendo profesional cuando lo que
    # quería era otro servicio.
    cambio = pedido_de_cambio(texto)
    if cambio is not None:
        logger.info("«%s» → %s (pedido de cambio, sin LLM)", texto[:24], cambio.value)
        return {**limpio_turno, "intent": cambio.value, "entidades": {}}

    cfg = (config.get("configurable") or {})
    hoy = hoy_del_negocio()
    if len(texto) > MAX_MENSAJE:
        logger.info("mensaje de %d caracteres recortado a %d", len(texto), MAX_MENSAJE)
        texto = texto[:MAX_MENSAJE]
    resultado: Clasificacion = await clasificar(
        _clasificador, texto, estado, opciones,
        hoy.isoformat(), DIAS_ES[hoy.weekday()], cfg.get("calendario", ""),
        respaldos=_respaldos,
    )
    return {
        **limpio_turno,
        "intent": resultado.intent.value,
        "entidades": resultado.entities.model_dump(exclude_none=True),
    }


# ══════════════════════════════════════════════════════════════════
# Nodo 2 · avanzar
# ══════════════════════════════════════════════════════════════════

# En qué paso se decide cada dato. Sirve para detectar cuando alguien quiere
# volver atrás ("mejor cambio de servicio" estando en el día).
PASO_DE = {
    Intencion.ELEGIR_SERVICIO: Estado.ESPERANDO_SERVICIO,
    Intencion.ELEGIR_STAFF: Estado.ESPERANDO_STAFF,
    Intencion.ELEGIR_DIA: Estado.ESPERANDO_DIA,
    Intencion.ELEGIR_HORARIO: Estado.ESPERANDO_HORARIO,
    Intencion.DAR_NOMBRE: Estado.ESPERANDO_NOMBRE,
}


async def avanzar(conv: Conversacion, config) -> dict:
    """Decide el próximo paso. Determinístico: no hay LLM en este nodo."""
    cfg = config.get("configurable") or {}
    negocio = cfg["business_id"]
    estado = Estado(conv.get("estado") or Estado.APERTURA.value)
    intent = Intencion(conv.get("intent") or Intencion.DESCONOCIDO.value)
    ent = conv.get("entidades") or {}
    cambios: dict = {}

    saltear = await _pasos_a_saltear(negocio, cfg)

    # ---- La conversación es del negocio: el bot no interrumpe ----
    #
    # Va ANTES que todo lo demás. Si alguien está esperando que le conteste una
    # persona y el bot le sigue mandando listas de horarios, la escalación no
    # sirvió de nada: quedan dos hablando encima.
    if estado == Estado.EN_MANOS_HUMANAS:
        if intent == Intencion.VOLVER_AL_BOT or _espera_vencida(conv):
            return {"estado": conv.get("estado_previo") or Estado.APERTURA.value,
                    "estado_previo": None, "humano_desde": None,
                    "_plantilla": "volvio_el_bot"}
        # Silencio. El mensaje igual queda guardado en el hilo para quien
        # atienda, pero el bot no contesta.
        logger.info("en manos del negocio: no contesto «%s»", conv["mensaje"][:40])
        return {"_plantilla": "silencio"}

    if intent == Intencion.HABLAR_CON_PERSONA:
        return await _escalar(conv, cfg, negocio, estado, "pedido")

    if intent == Intencion.PEDIR_LINK:
        return {"_plantilla": "link"}

    # ---- La sesión venció: se arranca de nuevo, no se sigue donde estaba ----
    #
    # Va después de la salida de emergencia y antes de todo lo que decide sobre
    # datos guardados, porque de eso se trata: los datos guardados ya no valen.
    # Una fecha elegida hace tres semanas no es una fecha, es un turno para un
    # día que pasó — y el paso siguiente era confirmarla.
    #
    # NO alcanza el estado de manos humanas (tiene su propio reloj, más corto,
    # arriba) ni CONFIRMADO (ahí no hay nada a medias que se pueda pudrir: el
    # turno ya salió y el mensaje siguiente empieza un pedido nuevo igual).
    #
    # Se conserva `nombre`: quién sos no vence. Lo que vence es lo que elegiste.
    if conv.get("_vencida") and estado not in (Estado.APERTURA, Estado.CONFIRMADO):
        logger.info("sesión vencida en %s: arranco de nuevo", estado.value)
        return {**await _abrir(saltear, negocio),
                "servicio_id": None, "profesional_id": None,
                "fecha": None, "hora": None, "nombre_del_turno": None,
                "opciones": [], "desde_horario": 0, "sin_entender": 0,
                "_plantilla": "sesion_reiniciada"}

    # Un gracias o un saludo después de reservar es un cierre, no un pedido
    # nuevo. Sin esta rama caía en la apertura de más abajo y la persona
    # recibía el saludo entero con la lista de servicios y un "¿querés sacar un
    # turno?", que es contestarle algo que no preguntó justo después de haberle
    # contestado lo que sí.
    if estado == Estado.CONFIRMADO and intent == Intencion.SALUDO:
        return {"_plantilla": "de_nada"}

    # ---- Esperando la seña: cualquier mensaje es una segunda oportunidad ----
    #
    # El camino normal es que el pago lo confirme el vigilante de webhook.py,
    # que consulta cada veinte segundos. Pero ese vigilante es una tarea suelta
    # dentro del proceso: si el servicio reinicia o redeploya mientras la
    # persona paga, muere y NADIE vuelve a preguntar nunca. La conversación
    # queda esperando un pago que ya entró.
    #
    # Pasó exactamente así: pagó, el turno no se confirmó, y escribir "ya pagué"
    # le contestaba con la lista de servicios —porque más abajo cualquier
    # mensaje que no fuera un saludo se leía como "empieza un pedido nuevo"—.
    #
    # Por eso acá, antes de decidir nada, se pregunta. Es barato (una consulta
    # por mensaje, y sólo en este estado) y es la única forma de que el bot se
    # recupere solo de un pago que se perdió del otro lado.
    if estado == Estado.ESPERANDO_SENIA:
        codigo = conv.get("codigo_pendiente")
        pagada = None
        if codigo:
            try:
                pagada = await _aturno.senia_pagada(negocio, codigo)
            except Exception:  # noqa: BLE001 — no saber no es saber que no pagó
                logger.warning("no se pudo consultar la seña %s", codigo, exc_info=True)

        if pagada is True:
            logger.info("la seña de %s ya estaba paga: confirmo al escribir", codigo)
            return {
                "estado": Estado.CONFIRMADO.value,
                "codigo_pendiente": None,
                "turno_pendiente": None,
                "_plantilla": "senia_confirmada",
                "_datos": {**(conv.get("turno_pendiente") or {}), "codigo": codigo},
            }

        # Todavía no entró. Un saludo —o cualquier forma de "ya pagué"— recibe
        # el recordatorio, no la lista de servicios. Se resuelve con la tabla y
        # no con el modelo por lo mismo que el resto de los atajos: es previsible
        # y no vale gastar un token.
        if intent == Intencion.SALUDO or dice_que_pago(conv["mensaje"]):
            return {"_plantilla": "falta_pagar"}

    # Turno ya cerrado: el mensaje siguiente empieza un pedido nuevo. Se limpia
    # lo elegido pero NO quién es la persona — formulario nuevo, cliente
    # conocido. Es lo que espera alguien que vuelve a escribir después de
    # reservar, igual que en la web.
    # `ESPERANDO_SENIA` entra acá por lo mismo: quien escribe otra cosa está
    # empezando un pedido nuevo. El turno que espera el pago no se toca —sigue
    # apartado hasta que venza— y el nuevo pedido arranca limpio.
    if estado in (Estado.CONFIRMADO, Estado.ESPERANDO_SENIA):
        estado = Estado.APERTURA
        conv = {**conv, "estado": estado.value, "servicio_id": None,
                "profesional_id": None, "fecha": None, "hora": None}
        cambios.update({"servicio_id": None, "profesional_id": None,
                        "fecha": None, "hora": None})

    # ---- "No" delante del resumen: NO se reserva, y no se pierde nada ----
    #
    # Es la contracara exacta de CONFIRMAR y por eso está pegada a él, antes
    # que cualquier otra transversal. Lo único que hace es no avanzar: el paso
    # queda donde estaba y todo lo elegido sigue en pie, porque quien contesta
    # que no quiere cambiar UNA cosa, no empezar de cero. Para empezar de cero
    # está "cancelar", que es la rama de acá abajo y sí limpia.
    #
    # Fuera del resumen un "no" suelto no significa esto —significa que no a lo
    # que sea que se estaba preguntando— así que la intención sólo se atiende
    # acá y en el resto de los pasos cae en el pedido repetido de siempre.
    if intent == Intencion.RECHAZAR:
        if estado == Estado.ESPERANDO_CONFIRMACION:
            logger.info("rechazó el resumen: no reservo y espero qué cambia")
            return {"sin_entender": 0, "_plantilla": "no_reservo"}
        return {"_plantilla": "no_entendi"}

    # ---- Transversales: no mueven el flujo ----
    if intent == Intencion.CANCELAR:
        # "Cancelar" quiere decir dos cosas distintas según dónde esté la
        # persona, y contestarlas igual es lo que hacía daño.
        #
        # Con algo en curso —eligió un servicio, un día, una hora— cancelar es
        # abandonar ESE pedido, y el bot sí puede: no hay nada reservado que
        # deshacer.
        #
        # Sin nada en curso está hablando del turno que ya tiene. Ese el bot no
        # lo puede cancelar, y decir que sí es peor que decir que no: la
        # persona no va y el lugar le queda ocupado al negocio.
        #
        # El bloque de CONFIRMADO de más arriba ya limpió lo elegido, así que
        # justo después de reservar esto cae —bien— en la segunda rama.
        en_curso = any(conv.get(k) for k in ("servicio_id", "fecha", "hora"))
        return {"estado": Estado.APERTURA.value, "servicio_id": None,
                "profesional_id": None, "fecha": None, "hora": None,
                "opciones": [],
                "_plantilla": "cancelado" if en_curso else "no_puedo_cancelar"}

    if intent == Intencion.CONSULTAR_INFO:
        consulta = ent.get("consulta") or conv["mensaje"]
        # Si la búsqueda falla, la respuesta es "no lo tengo", no un error.
        #
        # Pasó de verdad: se agotó la cuota diaria de embeddings (1.000 por
        # día en el plan gratuito) y la excepción subía hasta arriba, así que
        # CUALQUIER pregunta contestaba "se me complicó procesar eso". El
        # proveedor de embeddings caído no es algo que la persona pueda
        # arreglar ni entender; que el bot no sepa un dato, sí.
        try:
            texto = await _rag(negocio).contexto(consulta)
        except Exception:  # noqa: BLE001
            logger.warning("la búsqueda falló para %s", negocio, exc_info=True)
            texto = ""

        # Sin texto, el negocio no tiene cargada esa respuesta. La pregunta va
        # al panel en vez de perderse: hasta ahora el bot decía "no lo tengo
        # cargado" y ahí terminaba, así que el negocio nunca se enteraba de qué
        # le preguntaban y nunca podía cargarlo.
        #
        # En segundo plano y sin esperar: la persona no puede quedarse
        # esperando a que el panel conteste. Y sólo cuando la búsqueda no
        # encontró nada, no cuando el buscador está caído — una cuota agotada
        # llenaría la lista de preguntas que el negocio SÍ tiene contestadas.
        if not texto:
            asyncio.create_task(avisar_sin_respuesta(negocio, consulta))

        return {"_plantilla": "info", "_datos": {"texto": texto}}

    if intent == Intencion.VOLVER:
        return {"estado": anterior(estado, saltear).value}

    if intent == Intencion.VER_MAS:
        # La plantilla de horarios corta en 8 y dice "pedime 'más'". Sin esto
        # cableado, pedir "más" caía en desconocido y el bot repetía la misma
        # lista: ofrecer algo y no cumplirlo es peor que no ofrecerlo.
        # Se queda en el mismo paso; lo único que se mueve es la ventana.
        if estado == Estado.ESPERANDO_HORARIO:
            return {"desde_horario": int(conv.get("desde_horario") or 0) + PAGINA}
        return {}

    if intent in (Intencion.DESCONOCIDO, Intencion.SALUDO):
        # Un saludo a mitad de flujo no reinicia nada: repite el pedido actual.
        if estado == Estado.APERTURA:
            return await _abrir(saltear, negocio)
        if intent == Intencion.SALUDO:
            return {"sin_entender": 0}

        # Dos mensajes seguidos sin entender: repetir el mismo pedido una
        # tercera vez es la definición de callejón sin salida. A la segunda se
        # ofrece una persona, sin que haga falta que se le ocurra pedirlo — que
        # es justo lo que no se le ocurre a alguien que ya se está frustrando.
        fallas = int(conv.get("sin_entender") or 0) + 1
        # Escalar por confusión SOLO si la conversación venía avanzando.
        #
        # Sin esta condición, "!!!!" y tres emojis alcanzaban para hacerle
        # sonar el teléfono al dueño: dos mensajes de basura desde un número
        # cualquiera y sale la notificación. Un canal de aviso que cualquiera
        # puede disparar gratis se ignora en una semana, y ahí el que se
        # trabó de verdad tampoco recibe ayuda.
        #
        # Quien eligió algo y después se trabó merece una persona. Quien
        # nunca eligió nada y manda ruido recibe el pedido de nuevo.
        if fallas >= LIMITE_SIN_ENTENDER and _hubo_avance(conv):
            return {**await _escalar(conv, cfg, negocio, estado, "trabado"),
                    "sin_entender": 0}

        # Y el que nunca eligió nada tampoco puede girar para siempre. Antes el
        # contador se reseteaba solo al pasarse del límite —subía a 2, volvía a
        # 0, arrancaba de nuevo— así que el bot repetía el mismo pedido sin
        # cambiar nunca y sin ninguna puerta de salida. Ahora se le ofrecen las
        # dos que puede abrir sola, sin molestar a nadie del negocio.
        if fallas >= LIMITE_ATASCADO:
            logger.info("atascado sin avanzar tras %d intentos", fallas)
            return {"sin_entender": 0, "_plantilla": "atascado"}

        # Y mientras tanto, decirle que no se entendió. Repetir el pedido
        # idéntico, sin una palabra que lo reconozca, se lee como que el bot se
        # colgó — justo cuando hace falta la señal contraria.
        return {"sin_entender": fallas, "_plantilla": "no_entendi"}

    # Primer contacto: SIEMPRE la apertura, sin importar qué haya escrito.
    # Es el requisito de que la puerta de entrada sea siempre la misma; el
    # dato que trajo no se pierde, se interpreta en el mensaje siguiente.
    if estado == Estado.APERTURA:
        return await _abrir(saltear, negocio)

    # Un nombre escrito en la confirmación corrige a nombre de QUIÉN va el
    # turno, y no quién sos vos.
    #
    # Sin esto caía en "volver a un paso anterior": lo mandaba al paso del
    # nombre y le pedía el nombre que acababa de escribir. Corregir un dato
    # que está a la vista no puede costar dos mensajes y perder el resumen.
    #
    # Escribe `nombre_del_turno` y NO `nombre`: acá es donde alguien dice "es
    # para mi hija". Guardarlo como identidad hacía que el bot la saludara a
    # ella para siempre y le pusiera su nombre a todos los turnos que sacara
    # después el padre.
    if estado == Estado.ESPERANDO_CONFIRMACION and intent == Intencion.DAR_NOMBRE:
        limpio = limpiar_nombre(ent.get("nombre") or "")
        if len(limpio) >= 2:
            logger.info("el turno va a nombre de: %s", limpio)
            return {"nombre_del_turno": limpio, "sin_entender": 0}
        return {}

    # ---- ¿Quiere volver a un paso anterior? ----
    paso = PASO_DE.get(intent)
    if paso and paso in ORDEN and estado in ORDEN:
        if ORDEN.index(paso) < ORDEN.index(estado):
            # "mejor cambio de servicio" estando en el día. Retroceder está
            # permitido; saltear hacia adelante no. Se limpia lo elegido
            # después de ese paso: ya no es válido.
            logger.info("retrocede de %s a %s", estado.value, paso.value)
            return {"estado": paso.value, **_limpiar_desde(paso)}

    # ---- Avanza el paso actual ----
    if intent != AVANZA_CON.get(estado):
        return {}  # no corresponde a este paso: se repite el pedido

    resuelto = await _resolver(estado, conv, ent, negocio, cfg)
    if resuelto is None:
        # Se entendió la intención pero no a qué apuntaba: nombró un servicio
        # que no existe, o uno que coincide con dos. Se repite el pedido y se
        # dice que no se entendió, que es exactamente lo que pasó.
        return {"_plantilla": "no_entendi"}
    if "_rechazo" in resuelto:
        # Pidió algo que no está disponible. NO avanza: se explica el motivo y
        # se ofrecen las opciones cercanas, en el mismo paso.
        return {"_plantilla": "no_disponible", "_datos": resuelto["_rechazo"]}
    cambios.update(resuelto)

    if estado == Estado.ESPERANDO_CONFIRMACION:
        return {**cambios, **await _reservar(conv, cambios, negocio, cfg)}

    # Se entendió y avanzó: la cuenta de frustración vuelve a cero.
    cambios["sin_entender"] = 0

    # Día nuevo, lista de horarios desde el principio.
    if estado == Estado.ESPERANDO_DIA:
        cambios["desde_horario"] = 0

    cambios["estado"] = siguiente(estado, saltear).value
    return cambios


async def _abrir(saltear: set[Estado], negocio: str) -> dict:
    """Muestra la apertura y deja el flujo listo para el próximo mensaje.

    El estado avanza a ESPERANDO_SERVICIO pero la plantilla que sale es la de
    apertura: sin esta marca, `responder` elegiría la plantilla del estado
    nuevo y el saludo con el nombre del negocio no se mostraría nunca.

"""
    return {"estado": siguiente(Estado.APERTURA, saltear).value,
            "_plantilla": "apertura"}


def _limpiar_desde(paso: Estado) -> dict:
    """Al retroceder, lo elegido después de ese paso deja de valer."""
    campos = {
        Estado.ESPERANDO_SERVICIO: ["servicio_id", "profesional_id", "fecha", "hora"],
        Estado.ESPERANDO_STAFF: ["profesional_id", "fecha", "hora"],
        Estado.ESPERANDO_DIA: ["fecha", "hora"],
        Estado.ESPERANDO_HORARIO: ["hora"],
    }.get(paso, [])
    # La ventana de horarios siempre vuelve al principio: si no, elegir otro
    # día heredaría el desplazamiento del anterior y la lista arrancaría por el
    # medio sin nada que lo explique.
    return {c: None for c in campos} | {"desde_horario": 0}


def _mas_parecida(pedida: str, libres: list[time]) -> str | None:
    """El horario libre que más se parece a lo que la persona escribió.

    Dos criterios, en este orden:

    1. Tiene que estar CERCA en el tiempo (`TOLERANCIA_TIPEO`). Es el filtro
       que decide si esto es un tipeo o un pedido distinto: a más de un cuarto
       de hora, la persona quiso otra cosa y hay que mostrarle la lista.

    2. Entre los que pasan, gana el que más caracteres comparte con lo escrito.
       Con turnos cada 15 minutos, "09:39" está a 6 de las 09:45 y a 9 de las
       09:30 — el más cercano en tiempo sería 09:45, pero quien escribió eso
       quiso poner 09:30 y se le fue un dedo. El parecido de texto lo captura
       sin tener que modelar la distancia entre teclas, que además cambia
       según el teclado del teléfono.

    Y no hace falta acertar siempre: lo que se hace con esto es PREGUNTAR.
    Equivocarse cuesta un mensaje, no un turno mal dado.
    """
    pedidos_min = int(pedida[:2]) * 60 + int(pedida[3:5])
    candidatas = [
        h for h in libres
        if abs(h.hour * 60 + h.minute - pedidos_min) <= TOLERANCIA_TIPEO
    ]
    if not candidatas:
        return None

    def parecido(h: time) -> tuple[int, int]:
        texto = h.strftime("%H:%M")
        iguales = sum(1 for a, b in zip(texto, pedida) if a == b)
        distancia = abs(h.hour * 60 + h.minute - pedidos_min)
        return (-iguales, distancia)   # más parecida primero; empata la cercana

    return min(candidatas, key=parecido).strftime("%H:%M")


def _hubo_avance(conv: Conversacion) -> bool:
    """¿La persona llegó a elegir algo, o viene mandando ruido desde el arranque?"""
    return any(conv.get(k) for k in ("servicio_id", "profesional_id",
                                     "fecha", "hora", "nombre"))


def _sesion_vencida(ultimo_en: str | None) -> bool:
    """¿Pasó tanto desde el último mensaje que lo elegido ya no sirve?

    Es distinto de `_espera_vencida`, que mide cuánto hace que el negocio no
    contesta una escalación. Acá se mide a la persona, y lo que está en juego
    son los DATOS: una fecha elegida hace tres semanas sigue guardada como si
    fuera de ahora, y el paso siguiente era confirmarla.

    Sin sello previo no está vencida: es el primer mensaje de la conversación,
    o uno guardado antes de que este campo existiera. Ante la duda, seguir
    donde estaba es menos molesto que reiniciarle el pedido a alguien que
    acaba de escribir.
    """
    if not ultimo_en:
        return False
    try:
        transcurrido = (ahora() - datetime.fromisoformat(ultimo_en)).total_seconds()
    except (ValueError, TypeError):
        return False
    return transcurrido > ajustes().sesion_minutos * 60


def _espera_vencida(conv: Conversacion) -> bool:
    """¿Pasó demasiado tiempo sin que nadie del negocio conteste?

    Un bot que se calla para siempre es peor que uno que molesta: la persona no
    sabe si la están leyendo. Pasado el rato, el bot retoma y al menos se puede
    sacar el turno sola.
    """
    desde = conv.get("humano_desde")
    if not desde:
        return True
    try:
        return (ahora() - datetime.fromisoformat(desde)).total_seconds() > \
            ajustes().escalacion_minutos * 60
    except ValueError:
        return True


async def _escalar(conv: Conversacion, cfg: dict, negocio: str,
                   estado: Estado, motivo: str) -> dict:
    """Le avisa al negocio y pone la conversación en sus manos.

    El estado NO se limpia: cuando la persona vuelva —o cuando el bot retome—
    sigue exactamente donde estaba. Es la misma regla que el botón atrás.
    """
    aviso = Escalacion(
        business_id=negocio,
        telefono=cfg.get("telefono") or "",
        nombre=conv.get("nombre") or cfg.get("nombre_cliente"),
        motivo=motivo,
        paso=estado.value,
        ultimo_mensaje=conv.get("mensaje") or "",
    )
    # El panel ya recibe TODOS los mensajes, y el del pedido viaja con
    # `necesita_humano` en true. Si está configurado, el negocio se entera por
    # ahí y no hace falta un segundo canal: dos avisos del mismo hecho son dos
    # lugares donde mirar, y el día que discrepan nadie sabe cuál creer.
    #
    # `escalacion_webhook` queda para quien no tenga el panel — un negocio que
    # quiera el aviso en Slack o en su propio sistema.
    cfg_ = ajustes()
    por_el_panel = bool(cfg_.panel_url and cfg_.panel_secreto)
    llego = await notificar(aviso, cfg_.escalacion_webhook or None) or por_el_panel
    return {
        "estado": Estado.EN_MANOS_HUMANAS.value,
        "estado_previo": estado.value,
        "humano_desde": ahora().isoformat(),
        "_plantilla": "escalado",
        "_datos": {"aviso_llego": llego},
    }


async def _pasos_a_saltear(negocio: str, cfg: dict) -> set[Estado]:
    """Los pasos que en este negocio no son una decisión.

    Un paso con una sola opción no es una pregunta: es un trámite. Pedirle a
    alguien que elija "1" de una lista de uno agrega un mensaje, una espera y
    una oportunidad de equivocarse, y no cambia el resultado — ya estaba
    decidido antes de preguntar.

    Se saltea el servicio si el negocio vende uno solo, el profesional si
    atiende una sola persona, y el nombre si el teléfono ya es de un cliente
    conocido. Los tres son el mismo criterio.
    """
    saltear = set()
    # El servicio NO se saltea aunque haya uno solo. Ese primer paso no es
    # "elegí de la lista": es la única pregunta abierta de toda la conversación,
    # donde alguien puede decir que quiere un turno, preguntar cualquier cosa o
    # pedir una persona. Saltearlo ahorra un mensaje y cierra la puerta por la
    # que entra todo lo que no es reservar.
    if len(await _aturno.listar_personal(negocio)) <= 1:
        saltear.add(Estado.ESPERANDO_STAFF)
    if cfg.get("nombre_cliente"):
        saltear.add(Estado.ESPERANDO_NOMBRE)
    return saltear


async def _resolver(
    estado: Estado, conv: Conversacion, ent: dict, negocio: str, cfg: dict
) -> dict | None:
    """Traduce lo que dijo la persona al dato que guarda el estado."""
    indice = ent.get("_indice")

    if estado == Estado.ESPERANDO_SERVICIO:
        servicios = await _aturno.listar_servicios(negocio)
        if indice is not None:
            return {"servicio_id": servicios[indice].id}
        candidatos = _buscar(ent.get("servicio"), [(s.id, s.nombre) for s in servicios])
        # Un solo match: avanza (Nielsen #7). Cero o varios: se repite el
        # listado, que ahí sí es desambiguación y no confirmación redundante.
        return {"servicio_id": candidatos[0]} if len(candidatos) == 1 else None

    if estado == Estado.ESPERANDO_STAFF:
        gente = await _aturno.listar_personal(negocio, conv.get("servicio_id"))
        if indice is not None:
            if indice < len(gente):
                return {"profesional_id": gente[indice].id}
            # El renglón que sigue al último nombre es "Me da igual", que lo
            # agrega `_pedir_paso`: no es una persona, es la opción de no
            # elegir ninguna. Por eso ESE índice vale y significa `None`.
            if indice == len(gente):
                return {"profesional_id": None}
            # Cualquier otro índice sí está fuera de la lista, y ahí `None` es
            # "no se pudo resolver" y no "me da igual": elegir por alguien que
            # señaló mal es peor que volver a mostrarle la lista.
            return None
        nombre = (ent.get("profesional") or "").lower()
        if nombre in {"cualquiera", "me da igual", "el que sea", "no importa"}:
            return {"profesional_id": None}
        candidatos = _buscar(nombre, [(p.id, p.nombre) for p in gente])
        return {"profesional_id": candidatos[0]} if len(candidatos) == 1 else None

    if estado == Estado.ESPERANDO_DIA:
        cupos = await _cupos(conv, negocio)
        dias = P.dias_elegibles(cupos)
        if indice is not None and indice < len(dias):
            return {"fecha": dias[indice].fecha.isoformat()}
        if ent.get("fecha"):
            # Lo que sacó el modelo NO se guarda sin verificar que exista.
            # Se aceptaba tal cual, así que pedir un día cerrado o completo
            # avanzaba el flujo igual y el problema recién aparecía al final.
            pedida = ent["fecha"]
            if any(d.fecha.isoformat() == pedida for d in dias):
                return {"fecha": pedida}
            elegido = next((c for c in cupos if c.fecha.isoformat() == pedida), None)
            return {"_rechazo": {
                "motivo": (MotivoNoDisponible.CERRADO
                           if elegido is None or elegido.motivo == SinLugar.CERRADO
                           else MotivoNoDisponible.OCUPADO).value,
                "alternativas": [],
            }}
        return None

    if estado == Estado.ESPERANDO_HORARIO:
        # El número se resuelve contra la lista que se MOSTRÓ, no contra los
        # horarios recalculados. Acá se indexaba la lista completa: después de
        # pedir "más", la pantalla decía 17:00 y el "1" guardaba las 13:00 —
        # el bot confirmaba una hora que la persona nunca vio.
        # Mientras mostrar y resolver lean fuentes distintas, ese desfasaje
        # puede volver; `opciones` es exactamente lo que se imprimió.
        mostradas = conv.get("opciones") or []
        if indice is not None and indice < len(mostradas):
            return {"hora": mostradas[indice]}

        if ent.get("hora"):
            # La hora que extrae el modelo se VERIFICA contra los horarios
            # libres. Antes se guardaba tal cual: alguien escribía "9:39" —una
            # hora que no existe en la grilla— y el bot la aceptaba, la ponía
            # en el resumen y la reservaba. Confirmar un turno a una hora que
            # nunca se ofreció es lo peor que puede hacer este sistema: la
            # persona se presenta cuando no la esperan.
            pedida = ent["hora"]
            libres = await _horarios(conv, negocio)
            if any(h.strftime("%H:%M") == pedida for h in libres):
                return {"hora": pedida}

            # No está. Se dice por qué y qué hay cerca, que es para lo que
            # existe `consultar_pedido`.
            try:
                dia = date.fromisoformat(conv["fecha"])
                reloj = datetime.strptime(pedida, "%H:%M").time()
            except (ValueError, KeyError, TypeError):
                return None
            consulta = await _aturno.consultar_pedido(
                negocio, conv["servicio_id"], dia, reloj, conv.get("profesional_id"))

            # ¿Se le fue la mano por unos minutos?
            #
            # Alguien que escribe "9:39" con turnos cada media hora no quiso
            # decir 9:39: se le escapó un dedo. Devolverle tres opciones
            # numeradas lo obliga a elegir otra vez algo que ya había elegido.
            # Con una diferencia así, se le pregunta por LA que quiso.
            #
            # El umbral es corto a propósito. Con media hora de tolerancia esto
            # dejaría de ser "corregí un tipeo" y pasaría a ser "te doy otra
            # hora", que es una decisión de la persona y no del sistema.
            sugerencia = _mas_parecida(pedida, libres)

            return {"_rechazo": {
                "motivo": (consulta.motivo or MotivoNoDisponible.FUERA_DE_HORARIO).value,
                "pedida": pedida,
                "sugerencia": sugerencia,
                "alternativas": [{"fecha": a.fecha.isoformat(),
                                  "hora": a.hora.strftime("%H:%M")}
                                 for a in consulta.alternativas[:3]],
            }}
        return None

    if estado == Estado.ESPERANDO_NOMBRE:
        # Limpio ya acá, no solo al reservar: si no, el resumen le muestra a la
        # persona un nombre distinto del que se va a guardar.
        limpio = limpiar_nombre(ent.get("nombre") or "")
        return {"nombre": limpio} if len(limpio) >= 2 else None

    if estado == Estado.ESPERANDO_CONFIRMACION:
        return {}

    return None


def _buscar(texto: str | None, opciones: list[tuple[str, str]]) -> list[str]:
    """Los ids cuyo nombre coincide. Sin tildes ni mayúsculas."""
    if not texto:
        return []
    import unicodedata

    def norm(s: str) -> str:
        d = unicodedata.normalize("NFD", s.lower())
        return " ".join("".join(c for c in d if unicodedata.category(c) != "Mn").split())

    objetivo = norm(texto)
    exactos = [i for i, n in opciones if norm(n) == objetivo]
    if exactos:
        return exactos
    return [i for i, n in opciones if objetivo in norm(n) or norm(n) in objetivo]


async def _cupos(conv: Conversacion, negocio: str):
    return await _aturno.dias_con_cupo(
        negocio, conv["servicio_id"], hoy_del_negocio(), 7, conv.get("profesional_id")
    )


async def _horarios(conv: Conversacion, negocio: str):
    disp = await _aturno.consultar_disponibilidad(
        negocio, conv["servicio_id"],
        date.fromisoformat(conv["fecha"]), conv.get("profesional_id"),
    )
    return disp.horarios


async def _reservar(conv: Conversacion, cambios: dict, negocio: str, cfg: dict) -> dict:
    """Crea el turno. Un rechazo es un resultado normal, no una excepción."""
    # El del turno gana si alguien lo corrigió; si no, va el del contacto.
    nombre = (conv.get("nombre_del_turno")
              or conv.get("nombre")
              or cfg.get("nombre_cliente") or "")
    fecha = date.fromisoformat(conv["fecha"])
    hora = datetime.strptime(conv["hora"], "%H:%M").time()

    turno = await _aturno.crear_turno(
        negocio, conv["servicio_id"], fecha, hora,
        DatosDelCliente(nombre=nombre, telefono=cfg.get("telefono", "")),
        conv.get("profesional_id"),
    )

    # ---- El servicio se seña y no se pudo cobrar ----
    #
    # No se promete el turno. El negocio puso la seña justamente para no dar ese
    # servicio sin garantía, así que darlo igual por WhatsApp sería usar este
    # canal para saltear una regla suya — que es exactamente el agujero que esto
    # vino a cerrar.
    #
    # La reserva que se llegó a crear NO se cancela a mano: quedó apartando el
    # horario con su vencimiento y lo suelta sola. Para eso existe la retención,
    # y además el endpoint público de cancelar exige dos horas de anticipación,
    # o sea que para un turno de hoy no serviría.
    if turno.motivo_del_rechazo == "no_se_pudo_cobrar_la_senia":
        logger.warning("no se pudo cobrar la seña en %s: no confirmo el turno", negocio)
        return {"estado": Estado.ESPERANDO_HORARIO.value, "hora": None,
                "_plantilla": "sin_cobro"}

    # ---- El turno existe, falta que entre la seña ----
    #
    # No es "confirmado" y no puede contarse como tal: el horario está apartado
    # por un rato y se suelta si nadie paga. El estado propio es lo que permite
    # que el mensaje siguiente de la persona no arranque un pedido nuevo encima
    # de uno que todavía puede cerrarse.
    if turno.estado.value == "pendiente_de_sena":
        logger.info("turno %s esperando la seña de $%s", turno.booking_id, turno.senia)
        # Qué turno es, para poder anunciarlo cuando el pago entre.
        #
        # Va en un campo propio y NO en `_datos`, aunque los mismos valores
        # viajen por los dos lados. `_datos` es el scratch del turno: `entender`
        # lo borra al empezar el mensaje siguiente, para que la plantilla de un
        # mensaje no se repita en el otro. El anuncio del pago, en cambio, llega
        # después —minutos después, o al mensaje siguiente— así que necesita un
        # dato que sobreviva, igual que `codigo_pendiente`.
        pendiente = {
            "codigo": turno.codigo,
            "servicio": turno.servicio,
            "profesional": turno.profesional,
            "fecha": turno.fecha.isoformat() if turno.fecha else None,
            "hora": turno.hora.strftime("%H:%M") if turno.hora else None,
        }
        return {
            "estado": Estado.ESPERANDO_SENIA.value,
            "nombre_del_turno": None,
            "codigo_pendiente": turno.codigo,
            "turno_pendiente": pendiente,
            "_plantilla": "senia",
            "_datos": {**pendiente, "monto": turno.senia,
                       "link": turno.link_de_pago,
                       "minutos": turno.minutos_de_retencion},
        }

    if turno.estado.value == "rechazado":
        consulta = await _aturno.consultar_pedido(
            negocio, conv["servicio_id"], fecha, hora, conv.get("profesional_id")
        )
        return {
            "_plantilla": "no_disponible",
            "_datos": {
                "motivo": consulta.motivo.value if consulta.motivo else None,
                "alternativas": [
                    {"fecha": a.fecha.isoformat(), "hora": a.hora.strftime("%H:%M")}
                    for a in consulta.alternativas[:3]
                ],
            },
            "estado": Estado.ESPERANDO_HORARIO.value, "hora": None,
        }

    return {
        "estado": Estado.CONFIRMADO.value,
        # El nombre del turno se limpia acá, y es lo que evita que el arreglo
        # dure una sola reserva: es de ESTA, no de la persona. Sin esto, quien
        # saca un turno para su hija le sigue sacando turnos a ella el mes que
        # viene sin haberlo pedido — el mismo bug de antes, corrido un lugar.
        # El del contacto NO se toca: ese es justamente el que tiene que durar.
        "nombre_del_turno": None,
        "_plantilla": "confirmado",
        "_datos": {
            "servicio": turno.servicio,
            "fecha": turno.fecha.isoformat(),
            "hora": turno.hora.strftime("%H:%M"),
            "codigo": turno.codigo,
            "profesional": turno.profesional,
        },
    }


# ══════════════════════════════════════════════════════════════════
# Nodo 3 · responder
# ══════════════════════════════════════════════════════════════════

async def responder(conv: Conversacion, config) -> dict:
    """Renderiza. Es el único lugar que produce texto para la persona."""
    cfg = config.get("configurable") or {}
    negocio = cfg["business_id"]
    nombre_negocio = cfg.get("nombre_negocio", "el negocio")
    estado = Estado(conv.get("estado") or Estado.APERTURA.value)
    especial = conv.get("_plantilla")

    # Plantillas puntuales que no dependen del paso
    if especial == "cancelado":
        return {"respuesta": P.cancelado(), "opciones": []}
    if especial == "de_nada":
        return {"respuesta": P.de_nada(), "opciones": []}
    datos = conv.get("_datos") or {}

    if especial == "no_reservo":
        # Se queda en el resumen y con las mismas opciones: no se reservó nada
        # y tampoco se perdió nada. El paso siguiente lo elige la persona.
        return {"respuesta": P.no_reservo(), "opciones": ["sí", "no"]}

    if especial == "senia":
        # El horario está apartado y falta el pago. Sin opciones: acá no se
        # elige nada de una lista, se paga (o se deja vencer).
        return {"respuesta": P.pedir_senia(datos["monto"], datos["link"],
                                           datos.get("minutos")),
                "opciones": []}

    if especial == "falta_pagar":
        return {"respuesta": P.falta_pagar(), "opciones": []}

    if especial == "senia_confirmada":
        # El pago ya estaba hecho y lo descubrimos al llegar este mensaje.
        #
        # Es la MISMA plantilla que manda el vigilante de webhook.py cuando la
        # consulta le da que entró: la persona tiene que leer lo mismo haya
        # escrito o no. Las fechas vuelven de `_datos`, donde las dejó el paso
        # que creó el turno, en el formato en que sobreviven al checkpointer.
        return {"respuesta": P.senia_confirmada(
                    datos.get("servicio") or "Tu turno",
                    datos.get("profesional"),
                    date.fromisoformat(datos["fecha"]),
                    datetime.strptime(datos["hora"], "%H:%M").time(),
                    datos.get("codigo") or "",
                ),
                "opciones": []}

    if especial == "sin_cobro":
        return {"respuesta": P.no_se_pudo_cobrar(_link_del_negocio(negocio)),
                "opciones": []}

    if especial == "atascado":
        return {"respuesta": P.atascado(_link_del_negocio(negocio)), "opciones": []}

    if especial == "no_entendi":
        # El pedido del paso, con una línea adelante que reconoce que no se
        # entendió. Repetir el texto idéntico se lee como que el bot se colgó.
        paso = await _pedir_paso(conv, cfg, negocio, nombre_negocio,
                                 await _aturno.listar_servicios(negocio))
        return {"respuesta": P.no_entendi(paso["respuesta"]),
                "opciones": paso.get("opciones", [])}

    if especial == "sesion_reiniciada":
        servicios = await _aturno.listar_servicios(negocio)
        cabecera = P.apertura(nombre_negocio, servicios, cfg.get("nombre_cliente"))
        if estado == Estado.ESPERANDO_SERVICIO:
            return {"respuesta": P.sesion_reiniciada(cabecera),
                    "opciones": [s.nombre for s in servicios]}
        paso = await _pedir_paso(conv, cfg, negocio, nombre_negocio, servicios)
        return {"respuesta": P.sesion_reiniciada(f"{cabecera}\n\n{paso['respuesta']}"),
                "opciones": paso.get("opciones", [])}

    if especial == "apertura":
        servicios = await _aturno.listar_servicios(negocio)
        cabecera = P.apertura(nombre_negocio, servicios, cfg.get("nombre_cliente"))
        if estado == Estado.ESPERANDO_SERVICIO:
            return {"respuesta": cabecera, "opciones": [s.nombre for s in servicios]}

        # El negocio vende un solo servicio, así que ese paso no existe y el
        # estado ya avanzó. El saludo se pega con el pedido del paso REAL, en
        # un mismo mensaje: si saliera solo, las opciones guardadas serían las
        # de servicios y el "1" del mensaje siguiente apuntaría a otra lista.
        paso = await _pedir_paso(conv, cfg, negocio, nombre_negocio, servicios)
        return {"respuesta": f"{cabecera}\n\n{paso['respuesta']}",
                "opciones": paso.get("opciones", [])}

    if especial == "info":
        # Sin texto, el negocio no cargó esa respuesta. Se dice eso y no
        # "no puedo hacer eso", que era lo que salía antes y hacía sonar la
        # pregunta como el problema.
        texto = datos.get("texto") or ""
        return {"respuesta": P.respuesta_info(texto) if texto else P.sin_dato()}

    if especial == "silencio":
        # Cadena vacía: el webhook no manda nada. La persona ve el chat como lo
        # dejó, esperando a que le conteste alguien del negocio.
        return {"respuesta": ""}

    if especial == "escalado":
        try:
            datos_contacto = await _aturno.contacto(negocio)
        except Exception:  # noqa: BLE001
            datos_contacto = None
        return {"respuesta": P.escalado(
            nombre_negocio, bool(datos.get("aviso_llego")), datos_contacto),
            "opciones": []}

    if especial == "volvio_el_bot":
        paso = await _pedir_paso(conv, cfg, negocio, nombre_negocio,
                                 await _aturno.listar_servicios(negocio))
        return {"respuesta": P.volvio_el_bot(paso["respuesta"]),
                "opciones": paso.get("opciones", [])}

    if especial == "no_puedo_cancelar":
        return {"respuesta": P.no_puedo_cancelar(_link_del_negocio(negocio)),
                "opciones": []}

    if especial == "link":
        return {"respuesta": P.link_web(nombre_negocio, _link_del_negocio(negocio))}

    if especial == "confirmado":
        return {"respuesta": P.confirmado(
            datos["servicio"], datos.get("profesional"),
            date.fromisoformat(datos["fecha"]),
            datetime.strptime(datos["hora"], "%H:%M").time(),
            datos["codigo"]), "opciones": []}

    if especial == "no_disponible":
        alts = [Alternativa(fecha=date.fromisoformat(a["fecha"]),
                            hora=datetime.strptime(a["hora"], "%H:%M").time(),
                            distancia_minutos=0) for a in datos.get("alternativas", [])]
        motivo = MotivoNoDisponible(datos["motivo"]) if datos.get("motivo") else None
        sugerida = datos.get("sugerencia")
        # Con una sugerencia, ella es la única opción: así un "sí" no puede
        # apuntar a otra cosa. Sin sugerencia, la lista de alternativas.
        return {"respuesta": P.no_disponible(motivo, alts, datos.get("pedida"), sugerida),
                "opciones": ([sugerida] if sugerida
                             else [a["hora"] for a in datos.get("alternativas", [])])}

    servicios = await _aturno.listar_servicios(negocio)
    return await _pedir_paso(conv, cfg, negocio, nombre_negocio, servicios)


def _link_del_negocio(negocio: str) -> str | None:
    """La página pública del negocio, o None si no hay con qué armarla.

    Sin `ATURNO_WEB_URL` no se inventa ninguna: un link roto es peor que
    ninguno, porque la persona lo abre, ve un 404 y concluye que el negocio no
    funciona. Es la misma regla que aplican `link_web` y `demorado`.

    OJO con el identificador: la página es `<web>/<slug>`, y acá `negocio` es
    el `business_id`, que en este proyecto ES el slug (ver el comentario de
    TENANTS en config.py). Si algún día el business_id pasa a ser el uid de
    Firebase, este es uno de los tres lugares que hay que cambiar, o el link
    empieza a dar 404.
    """
    base = (ajustes().aturno_web_url or "").rstrip("/")
    return f"{base}/{negocio}" if base else None


async def _pedir_paso(conv: Conversacion, cfg: dict, negocio: str,
                      nombre_negocio: str, servicios: list) -> dict:
    """El texto que pide lo que falta en el paso actual, y sus opciones.

    Separado de `responder` porque la apertura también lo necesita: cuando el
    negocio vende un solo servicio, el saludo y el pedido del paso siguiente
    salen juntos en un mismo mensaje.
    """
    estado = Estado(conv.get("estado") or Estado.APERTURA.value)

    if estado == Estado.APERTURA:
        return {"respuesta": P.apertura(nombre_negocio, servicios, cfg.get("nombre_cliente")),
                "opciones": [s.nombre for s in servicios]}

    if estado == Estado.ESPERANDO_SERVICIO:
        return {"respuesta": P.lista_servicios(servicios, conv.get("servicio_id")),
                "opciones": [s.nombre for s in servicios]}

    if estado == Estado.ESPERANDO_STAFF:
        gente = await _aturno.listar_personal(negocio, conv.get("servicio_id"))
        nombre_svc = next((s.nombre for s in servicios
                           if s.id == conv.get("servicio_id")), None)
        return {"respuesta": P.lista_staff(gente, nombre_svc),
                "opciones": [p.nombre for p in gente] + ["Me da igual"]}

    if estado == Estado.ESPERANDO_DIA:
        cupos = await _cupos(conv, negocio)
        elegibles = P.dias_elegibles(cupos)
        return {"respuesta": P.selector_dias(cupos),
                "opciones": [d.fecha.isoformat() for d in elegibles]}

    if estado == Estado.ESPERANDO_HORARIO:
        libres = await _horarios(conv, negocio)
        dia = date.fromisoformat(conv["fecha"])
        if not libres:
            return {"respuesta": P.no_disponible(None, []), "opciones": []}
        # La ventana que se está mostrando. Los números que responde la persona
        # se resuelven contra `opciones`, así que mostrar y numerar tienen que
        # salir del MISMO recorte o el "3" apunta a otra hora.
        desde = int(conv.get("desde_horario") or 0)
        if desde >= len(libres):       # pidió "más" cuando ya no quedaba
            desde = 0
        ventana = libres[desde:]
        return {"respuesta": P.lista_horarios(dia, ventana, PAGINA),
                "opciones": [h.strftime("%H:%M") for h in ventana[:PAGINA]]}

    if estado == Estado.ESPERANDO_NOMBRE:
        return {"respuesta": P.pedir_nombre(), "opciones": []}

    if estado == Estado.ESPERANDO_CONFIRMACION:
        elegido = next(s for s in servicios if s.id == conv["servicio_id"])
        nombre_svc = elegido.nombre
        senia_del_servicio = elegido.senia if elegido.requiere_senia else 0
        quien = None
        if conv.get("profesional_id"):
            gente = await _aturno.listar_personal(negocio)
            quien = next((p.nombre for p in gente if p.id == conv["profesional_id"]), None)
        return {
            "respuesta": P.resumen(
                nombre_svc, quien,
                date.fromisoformat(conv["fecha"]),
                datetime.strptime(conv["hora"], "%H:%M").time(),
                (conv.get("nombre_del_turno")
                 or conv.get("nombre") or cfg.get("nombre_cliente")),
                # La seña se avisa ACÁ, antes de que diga que sí, igual que la
                # web abre su modal antes de crear nada. Enterarse recién cuando
                # llega el link es aceptar una cosa y recibir otra.
                senia=senia_del_servicio,
                # Si el nombre no se dijo en ESTA conversación, viene de la vez
                # anterior y puede no ser de quien va el turno. El resumen lo
                # pregunta en vez de afirmarlo. Es acá y no con un mensaje
                # aparte a propósito: el resumen ya se manda igual, así que
                # confirmar sale gratis.
                de_memoria=not (conv.get("nombre_del_turno") or conv.get("nombre")),
            ),
            "opciones": ["sí", "no"],
        }

    return {"respuesta": P.error_tecnico(), "opciones": []}


async def retomar(grafo, config_del_hilo: dict) -> str | None:
    """Le devuelve la conversación al bot. Devuelve qué mandarle a la persona.

    La escribe el negocio desde su panel, no el cliente. Por eso no se hace
    pasar un mensaje por el grafo: el flujo avanzaría un paso, y quien apretó
    el botón no dijo nada en nombre del cliente.

    Devuelve None si la conversación no estaba en manos de nadie — apretar dos
    veces el botón no puede mandar dos mensajes.

    Lo que se manda incluye el pedido del paso actual. Alguien que venía
    hablando con una persona y de golpe recibe "sigo yo" sin más queda sin
    saber qué contestar: hay que recordarle en qué estaban.
    """
    estado_actual = await grafo.aget_state(config_del_hilo)
    vals = estado_actual.values or {}
    if vals.get("estado") != Estado.EN_MANOS_HUMANAS.value:
        return None

    previo = vals.get("estado_previo") or Estado.APERTURA.value
    await grafo.aupdate_state(config_del_hilo, {
        "estado": previo, "estado_previo": None, "humano_desde": None,
    })

    conv = {**vals, "estado": previo}
    cfg = config_del_hilo.get("configurable") or {}
    negocio = cfg["business_id"]
    servicios = await _aturno.listar_servicios(negocio)
    paso = await _pedir_paso(conv, cfg, negocio,
                            cfg.get("nombre_negocio") or "el negocio", servicios)
    # Las opciones que se muestran tienen que quedar guardadas, o el número
    # que conteste la persona se resuelve contra la lista anterior.
    await grafo.aupdate_state(config_del_hilo,
                              {"opciones": paso.get("opciones", [])})
    return P.volvio_el_bot(paso["respuesta"])


# ══════════════════════════════════════════════════════════════════
# El grafo
# ══════════════════════════════════════════════════════════════════

def construir_flujo(checkpointer: BaseCheckpointSaver):
    g = StateGraph(Conversacion)
    g.add_node("entender", entender)
    g.add_node("avanzar", avanzar)
    g.add_node("responder", responder)
    g.add_edge(START, "entender")
    g.add_edge("entender", "avanzar")
    g.add_edge("avanzar", "responder")
    g.add_edge("responder", END)
    return g.compile(checkpointer=checkpointer)


def hilo_de(business_id: str, telefono: str) -> str:
    """El negocio va adelante: la misma persona puede ser cliente de dos."""
    return f"{business_id}:{telefono}"
