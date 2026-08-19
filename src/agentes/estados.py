"""
estados.py — La máquina de estados del flujo conversacional.

POR QUÉ UNA MÁQUINA Y NO UN AGENTE SUELTO
-----------------------------------------
Cuando el LLM manejaba el flujo, cada conversación era una tirada de dados: el
saludo cambiaba de forma, un listado salía horizontal y el siguiente vertical,
se filtraron los ids internos, y el bot volvía a preguntar cosas ya
respondidas. Ninguno de esos se arregla con más instrucciones en el prompt.

Acá el flujo es determinístico. El LLM se usa para una sola cosa —entender qué
quiso decir la persona— y todo lo demás lo decide código: qué paso viene,
qué se muestra y con qué texto.

EL REPARTO
----------
    código  →  qué estado sigue, qué opciones son válidas, qué texto sale,
               resolver "3" contra la lista que se mostró
    LLM     →  solo cuando la respuesta no es un número: qué intención tiene
               este texto y qué datos trae

Un "3" no cuesta un token. La mayoría de los mensajes de este flujo son "3".
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from enum import Enum

from src.fechas import hoy


class Estado(str, Enum):
    """Cada paso del flujo. Hereda de str para poder guardarlo en Postgres."""

    APERTURA = "apertura"
    ESPERANDO_SERVICIO = "esperando_servicio"
    ESPERANDO_STAFF = "esperando_staff"
    ESPERANDO_DIA = "esperando_dia"
    ESPERANDO_HORARIO = "esperando_horario"
    ESPERANDO_NOMBRE = "esperando_nombre"
    ESPERANDO_CONFIRMACION = "esperando_confirmacion"
    CONFIRMADO = "confirmado"
    # El turno existe, tiene el horario apartado, y falta que entre la seña.
    # No está en ORDEN: no es un paso que la persona complete escribiendo — se
    # sale de acá pagando (o dejando que venza), no contestando.
    ESPERANDO_SENIA = "esperando_senia"
    # La conversación es del negocio, no del bot. No está en ORDEN: no es un
    # paso del formulario, es una pausa que puede pasar en cualquier momento.
    EN_MANOS_HUMANAS = "en_manos_humanas"


class Intencion(str, Enum):
    """Lo único que el LLM puede devolver. Cerrado a propósito.

    Un enum cerrado significa que una intención inventada no existe: si el
    modelo devuelve cualquier otra cosa, cae en DESCONOCIDO y el flujo sigue.
    """

    # Avanzan el flujo
    ELEGIR_SERVICIO = "elegir_servicio"
    ELEGIR_STAFF = "elegir_staff"
    ELEGIR_DIA = "elegir_dia"
    ELEGIR_HORARIO = "elegir_horario"
    DAR_NOMBRE = "dar_nombre"
    CONFIRMAR = "confirmar"

    # Transversales: no cambian de paso
    SALUDO = "saludo"
    CONSULTAR_INFO = "consultar_info"   # precio, horario, dirección
    VOLVER = "volver"
    CANCELAR = "cancelar"
    VER_MAS = "ver_mas"                 # más horarios
    HABLAR_CON_PERSONA = "hablar_con_persona"   # la salida de emergencia
    PEDIR_LINK = "pedir_link"           # prefiere reservar desde la web
    VOLVER_AL_BOT = "volver_al_bot"     # sale de manos humanas
    # El "no" delante del resumen. Separada de CANCELAR a propósito: no
    # significan lo mismo. Cancelar es abandonar el pedido y perder lo elegido;
    # esto es "algo de esto está mal, no lo reserves". Tratar el segundo como
    # el primero le borra a la persona todo lo que venía armando por haber
    # contestado que no a una pregunta.
    RECHAZAR = "rechazar"
    DESCONOCIDO = "desconocido"


# El orden del flujo, tal cual la web. La máquina nunca inventa un salto:
# solo puede avanzar al siguiente o saltear pasos que no aplican.
ORDEN = [
    Estado.ESPERANDO_SERVICIO,
    Estado.ESPERANDO_STAFF,
    Estado.ESPERANDO_DIA,
    Estado.ESPERANDO_HORARIO,
    Estado.ESPERANDO_NOMBRE,
    Estado.ESPERANDO_CONFIRMACION,
]

# Qué intención hace avanzar cada paso. Cualquier otra cosa que llegue en ese
# estado es transversal o desconocida: nunca salta de paso.
AVANZA_CON: dict[Estado, Intencion] = {
    Estado.ESPERANDO_SERVICIO: Intencion.ELEGIR_SERVICIO,
    Estado.ESPERANDO_STAFF: Intencion.ELEGIR_STAFF,
    Estado.ESPERANDO_DIA: Intencion.ELEGIR_DIA,
    Estado.ESPERANDO_HORARIO: Intencion.ELEGIR_HORARIO,
    Estado.ESPERANDO_NOMBRE: Intencion.DAR_NOMBRE,
    Estado.ESPERANDO_CONFIRMACION: Intencion.CONFIRMAR,
}

# Los pasos donde contestar es SEÑALAR UN RENGLÓN de una lista numerada.
#
# Es la condición para que "3" —o el nombre de una opción— se pueda resolver
# contra `opciones` sin pasar por el modelo. Fuera de estos pasos, `opciones`
# sigue existiendo (se le pasa al clasificador como contexto) pero no se indexa.
#
# LA DISTINCIÓN NO ES COSMÉTICA. Sin ella, el paso de confirmación —que muestra
# `["sí", "no"]` para que el modelo sepa qué se espera— hacía que un "no" fuera
# "el renglón 2", y todo renglón elegido se traducía a la intención que AVANZA
# el paso. O sea: contestar QUE NO al resumen reservaba el turno, igual que
# contestar que sí. La persona se presentaba a un turno que había rechazado y
# el negocio perdía el horario.
#
# Al declararlo como una tabla y no como un `if`, un paso nuevo entra en la
# lista sólo si de verdad se elige de una lista.
ELIGE_DE_LISTA: frozenset[Estado] = frozenset({
    Estado.ESPERANDO_SERVICIO,
    Estado.ESPERANDO_STAFF,
    Estado.ESPERANDO_DIA,
    Estado.ESPERANDO_HORARIO,
})


def siguiente(actual: Estado, saltear: set[Estado] | None = None) -> Estado:
    """El próximo paso, salteando los que no aplican a este negocio.

    Se saltea `ESPERANDO_STAFF` si el negocio no tiene equipo o tiene una sola
    persona, y `ESPERANDO_NOMBRE` si el teléfono ya es de un cliente conocido.
    Saltear es lo único que la máquina puede hacer fuera del orden: nunca
    adelanta dos pasos ni cambia la secuencia.
    """
    saltear = saltear or set()
    if actual == Estado.APERTURA:
        indice = 0
    else:
        indice = ORDEN.index(actual) + 1

    while indice < len(ORDEN) and ORDEN[indice] in saltear:
        indice += 1

    return ORDEN[indice] if indice < len(ORDEN) else Estado.CONFIRMADO


def anterior(actual: Estado, saltear: set[Estado] | None = None) -> Estado:
    """Un paso atrás, para la intención VOLVER. Nunca antes del primero."""
    saltear = saltear or set()
    if actual in (Estado.APERTURA, Estado.ESPERANDO_SERVICIO):
        return Estado.ESPERANDO_SERVICIO

    indice = ORDEN.index(actual) - 1
    while indice > 0 and ORDEN[indice] in saltear:
        indice -= 1
    return ORDEN[max(indice, 0)]


def _normalizar(texto: str) -> str:
    """Minúsculas, sin acentos y sin signos. Para comparar frases fijas."""
    sin_acentos = unicodedata.normalize("NFD", texto.lower())
    sin_acentos = "".join(c for c in sin_acentos if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", "", sin_acentos).strip()


# Frases que siempre significan lo mismo, en el paso donde significan eso.
#
# POR QUÉ ESTÁ ESTA TABLA
# Cada llamada al modelo cuesta ~1.677 tokens de entrada, y el 72% de eso es el
# esquema de la salida estructurada, que viaja igual aunque la respuesta sea
# "sí". Mandar "dale" al modelo para que conteste `confirmar` es pagar 1.677
# tokens por algo que una comparación de texto resuelve exacto.
#
# La tabla es CERRADA y por paso a propósito. "Sí" solo se interpreta como
# confirmar cuando la pregunta fue "¿confirmo?"; en cualquier otro paso, al
# modelo, que tiene el contexto. Un atajo que adivina de más es peor que no
# tenerlo: se equivoca en silencio y sin forma de notarlo.
ATAJOS: dict[Estado | None, dict[frozenset[str], Intencion]] = {
    Estado.ESPERANDO_CONFIRMACION: {
        frozenset({"si", "sisi", "si si", "dale", "ok", "oka", "okey", "listo",
                   "confirmo", "confirma", "confirmalo", "confirmar", "perfecto",
                   "obvio", "claro", "de una", "vale", "correcto", "asi es",
                   "esta bien", "si por favor", "si porfa", "genial", "buenisimo"}):
            Intencion.CONFIRMAR,
        # El "no" tiene que ser tan barato y tan exacto como el "sí". La
        # plantilla del resumen dice literalmente «Respondé SÍ o NO», así que
        # es la respuesta más previsible del flujo: mandarla al modelo sería
        # pagar por la mitad de las respuestas a una pregunta de dos.
        frozenset({"no", "nop", "nope", "no no", "todavia no", "aun no",
                   "esta mal", "no esta bien", "no es asi", "asi no",
                   "no confirmes", "no reserves", "pera", "esperá", "espera",
                   "no gracias", "mejor no"}):
            Intencion.RECHAZAR,
        # Qué querés cambiar, después del "no". Van acá y no al modelo por lo
        # mismo: son las tres respuestas posibles a una pregunta que hace el
        # bot, y tienen que funcionar aunque el clasificador esté caído.
        frozenset({"el dia", "la fecha", "otro dia", "cambiar el dia",
                   "el día", "cambiar la fecha"}): Intencion.ELEGIR_DIA,
        frozenset({"el horario", "la hora", "otro horario", "otra hora",
                   "cambiar el horario", "cambiar la hora"}):
            Intencion.ELEGIR_HORARIO,
        frozenset({"el servicio", "otro servicio", "cambiar el servicio"}):
            Intencion.ELEGIR_SERVICIO,
    },
    Estado.ESPERANDO_STAFF: {
        frozenset({"me da igual", "da igual", "cualquiera", "el que sea",
                   "la que sea", "cualquier", "no tengo preferencia", "indistinto",
                   "cualquier persona", "me es indiferente", "no importa"}):
            Intencion.ELEGIR_STAFF,
    },
    # Sin paso: valen en cualquier momento de la conversación.
    None: {
        frozenset({"hola", "buenas", "buen dia", "buenas tardes", "buenas noches",
                   "hey", "holis", "que tal", "hola buenas", "buenass"}):
            Intencion.SALUDO,
        frozenset({"gracias", "muchas gracias", "mil gracias", "genial gracias",
                   "dale gracias", "perfecto gracias"}):
            Intencion.SALUDO,
        frozenset({"quiero hablar con una persona", "hablar con una persona",
                   "con una persona", "quiero hablar con alguien",
                   "hablar con alguien", "un humano", "una persona",
                   "pasame con alguien", "quiero un humano",
                   "atencion humana", "hablar con un humano"}):
            Intencion.HABLAR_CON_PERSONA,
        frozenset({"cancelar", "cancela", "cancelalo", "cancelar todo",
                   "dejalo", "no importa dejalo", "olvidalo"}):
            Intencion.CANCELAR,
        frozenset({"mas", "mas horarios", "otro horario", "otros horarios",
                   "mas tarde", "mostrame mas", "ver mas"}):
            Intencion.VER_MAS,
        frozenset({"el link", "link", "mandame el link", "pasame el link",
                   "la pagina", "por la pagina", "por la web", "desde la web",
                   "prefiero la web", "link de la pagina", "quiero el link"}):
            Intencion.PEDIR_LINK,
        frozenset({"volver al bot", "seguir con el bot", "bot", "seguir sola",
                   "seguir solo", "sigo yo", "dejalo al bot"}):
            Intencion.VOLVER_AL_BOT,
    },
}


def respuesta_fija(texto: str, estado: Estado) -> tuple[Intencion, dict] | None:
    """La intención, si el mensaje es una de las frases de siempre. Sin LLM.

    Devuelve también las entidades que la frase implica, para que el resto del
    flujo no tenga que saber que esto pasó por un atajo.
    """
    limpio = _normalizar(texto)
    if not limpio or len(limpio) > 40:      # una frase fija no es un párrafo
        return None

    for paso in (estado, None):
        for frases, intencion in ATAJOS.get(paso, {}).items():
            if limpio in frases:
                entidades = ({"profesional": "cualquiera"}
                             if intencion == Intencion.ELEGIR_STAFF else {})
                return intencion, entidades
    return None


# Pedir cambiar algo ya elegido: "mejor cambio el servicio", "otro día".
#
# No entra en la tabla `ATAJOS` porque esa compara frases EXACTAS, y acá la
# variedad es el problema: "mejor cambio de servicio", "quiero otro servicio",
# "cambiame el servicio" y "mejor otro servicio" son la misma cosa dicha de
# cuatro formas, y ninguna lista cerrada las junta a todas.
#
# La regla pide DOS cosas a la vez —qué cambiar y una palabra de cambio— y por
# eso no se dispara sola: "quiero el servicio de coloración" nombra el servicio
# pero no pide cambiarlo, y no matchea.
#
# Existe porque acá el modelo se equivoca de una forma cara: leyendo "mejor
# cambio de servicio" como un "volver" genérico, que retrocede UN paso. Quien
# pidió cambiar el servicio estando en el día termina eligiendo profesional, y
# tiene que volver a pedirlo. Medido con Gemini; con Claude salía bien, o sea
# que además el comportamiento dependía del proveedor.
_QUE_CAMBIAR = (
    (("servicio",), Intencion.ELEGIR_SERVICIO),
    (("horario", "hora"), Intencion.ELEGIR_HORARIO),
    (("dia", "fecha"), Intencion.ELEGIR_DIA),
    (("profesional", "persona que", "con quien"), Intencion.ELEGIR_STAFF),
)
_PALABRAS_DE_CAMBIO = ("cambio", "cambiar", "cambiame", "cambia", "otro", "otra",
                       "mejor", "distinto", "distinta")


def pedido_de_cambio(texto: str) -> Intencion | None:
    """¿Está pidiendo cambiar algo que ya eligió? Sin LLM.

    Devuelve la intención del paso que quiere rehacer, o `None`. El flujo ya
    sabe qué hacer con eso: si el paso es anterior al actual, retrocede y limpia
    lo que dejó de valer.

    El orden de `_QUE_CAMBIAR` importa: "horario" se prueba antes que "dia"
    porque "cambiar el horario del día" nombra los dos y lo que se quiere
    cambiar es el horario.
    """
    limpio = _normalizar(texto)
    if not limpio or len(limpio) > 40:
        return None
    if not any(p in limpio.split() for p in _PALABRAS_DE_CAMBIO):
        return None
    for palabras, intencion in _QUE_CAMBIAR:
        if any(p in limpio for p in palabras):
            return intencion
    return None


# Cómo se presenta la gente. El resto del mensaje es el nombre.
_PRESENTACIONES = (
    "mi nombre es", "me llamo", "me llamó", "soy el", "soy la", "yo soy",
    "soy", "a nombre de", "es para", "para", "anotame como", "anotá",
    "anota", "ponelo a nombre de", "ponelo como", "el nombre es",
    "nombre", "de parte de",
)

# Palabras que descartan que el mensaje sea un nombre, aunque tenga la forma.
#
# Sin esto, "no gracias" o "el jueves" pasarían por nombres de dos palabras y
# el turno quedaría a nombre de «No Gracias». El costo de equivocarse acá no es
# una llamada al modelo: es un turno mal escrito en la agenda del negocio.
_NO_ES_NOMBRE = frozenset({
    "si", "no", "dale", "ok", "gracias", "hola", "buenas", "hoy", "mañana",
    "manana", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado",
    "domingo", "cualquiera", "igual", "cuanto", "cuando", "donde", "que",
    "quien", "como", "porque", "turno", "corte", "hora", "horario", "dia",
    "precio", "sale", "cuesta", "quiero", "necesito", "puedo", "tenes",
    "tienen", "hay", "mas", "otro", "otra", "volver", "cancelar", "esperar",
    "persona", "humano", "alguien", "bot", "link", "pagina", "web",
})


def nombre_propio(texto: str) -> str | None:
    """El nombre que dijo la persona, o `None` si esto no parece un nombre.

    POR QUÉ EXISTE
    `ESPERANDO_NOMBRE` era el único paso del flujo sin ningún atajo, así que
    TODO cliente nuevo pagaba una llamada al modelo para extraer "Ana" de
    "soy Ana". En la conversación más común —la que toca sólo números— era la
    única llamada que quedaba: resolverla acá la deja en cero.

    CUÁNDO SE RINDE, QUE ES LO IMPORTANTE
    Devuelve `None` ante cualquier duda y el mensaje sigue al clasificador, que
    es el reparto que ya usan `respuesta_fija` y `opcion_por_nombre`. Un atajo
    que adivina de más escribe mal el nombre en la agenda del negocio, y eso no
    se arregla solo: la persona se presenta y el turno está a nombre de otro.

    Por eso se exige que TODAS las palabras parezcan nombre. Basta una del
    vocabulario del flujo para soltar el mensaje.
    """
    limpio = _normalizar(texto)
    if not limpio:
        return None

    for presentacion in _PRESENTACIONES:
        if limpio.startswith(presentacion + " "):
            limpio = limpio[len(presentacion) + 1:].strip()
            break

    palabras = limpio.split()
    # Tres es el techo: "Juan Carlos Pérez". Más que eso ya es una frase, y una
    # frase con forma de nombre es justo lo que no hay que adivinar.
    if not 1 <= len(palabras) <= 3:
        return None
    for palabra in palabras:
        if len(palabra) < 2 or not palabra.isalpha() or palabra in _NO_ES_NOMBRE:
            return None

    # Se capitaliza sobre el texto ORIGINAL, no sobre el normalizado: `_normalizar`
    # saca los acentos para poder comparar, y devolver "Matias" cuando la persona
    # escribió "Matías" es escribirle mal el nombre en su propio turno.
    crudo = texto.strip()
    for presentacion in _PRESENTACIONES:
        if _normalizar(crudo).startswith(presentacion + " "):
            crudo = crudo.split(maxsplit=len(presentacion.split()))[-1]
            break
    return " ".join(p.capitalize() for p in crudo.split() if p)[:60] or None


# Lo que dice alguien que ya pagó la seña y viene a avisarlo.
#
# Va como tabla y no como intención del modelo porque no necesita contexto: en
# el único paso donde se consulta —esperando la seña— no hay otra cosa que
# puedan significar estas frases. Y porque el clasificador se puede quedar sin
# crédito, que es cuando más falta hace que el bot entienda "ya pagué".
_YA_PAGUE = (
    "ya pague", "pague", "ya lo pague", "ya la pague", "ya esta pagado",
    "ya esta paga", "esta pagado", "lo pague", "la pague", "hice el pago",
    "ya hice el pago", "ya transferi", "listo pague", "pague la senia",
    "ya pague la senia", "pago hecho", "ya pagamos",
)


def dice_que_pago(texto: str) -> bool:
    """¿Está avisando que ya pagó? Sin LLM.

    Se compara la frase entera normalizada y no por "contiene": "todavía no
    pagué" contiene "pague", y leerlo como un aviso de pago sería contestarle
    lo contrario de lo que dijo.
    """
    limpio = _normalizar(texto)
    return limpio in _YA_PAGUE


def sin_contenido(texto: str) -> bool:
    """¿El mensaje no tiene ninguna letra ni número que se pueda interpretar?

    Un "👋", un "..." o tres espacios en blanco. Hasta acá iban al clasificador
    —cuestan una llamada entera, ~1.677 tokens de entrada— para que devolviera
    lo único que puede devolver ante algo sin palabras: DESCONOCIDO. Se paga
    por una respuesta que ya se conoce antes de preguntar.

    Es la misma regla que ya aplica el webhook con los audios y las fotos: si
    no hay nada que leer, se contesta fijo y no se gasta modelo. La diferencia
    con `_normalizar` es que acá alcanza con mirar si sobrevive UN carácter
    alfanumérico, sin importar cuál.
    """
    return not any(c.isalnum() for c in unicodedata.normalize("NFKD", texto or ""))


def _como_hora(texto: str) -> str | None:
    """'9:30', '930', '9.30' -> '09:30'. None si no parece una hora."""
    m = re.fullmatch(r"(\d{1,2})[:. ]?(\d{2})", texto.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


DIAS_SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def _dia_por_texto(texto: str, opciones: list[str]) -> int | None:
    """El índice del día que la persona nombró. Sin LLM.

    Las opciones del paso del día son fechas ISO, así que el match por texto
    no sirve: nadie escribe "2026-08-19". Escriben "el jueves", "mañana" o
    "el 19". Y encima la lista SALTEA los días sin lugar, así que la
    numeración tiene huecos y decir solo "respondé con el número" delante de
    una lista con saltos parece un error del bot.

    Ante dos "jueves" en la lista gana el primero: es el que la gente quiere
    decir cuando no aclara. Para el otro está el número.
    """
    try:
        fechas = [date.fromisoformat(o) for o in opciones]
    except ValueError:
        return None

    limpio = _normalizar(texto)
    if not limpio:
        return None

    hoy_ = hoy()
    relativos = {"hoy": 0, "mañana": 1, "manana": 1,
                 "pasado": 2, "pasado manana": 2, "pasado mañana": 2}
    if limpio in relativos:
        objetivo = hoy_ + timedelta(days=relativos[limpio])
        return fechas.index(objetivo) if objetivo in fechas else None

    # "el jueves", "jueves que viene", "este viernes"
    for i, nombre in enumerate(DIAS_SEMANA):
        if nombre in limpio:
            for j, f in enumerate(fechas):
                if f.weekday() == i:
                    return j
            return None

    # "el 19", "19/8", "19 de agosto"
    m = re.search(r"\b(\d{1,2})\b", limpio)
    if m:
        numero = int(m.group(1))
        coinciden = [j for j, f in enumerate(fechas) if f.day == numero]
        if len(coinciden) == 1:
            return coinciden[0]
    return None


AFIRMACIONES = frozenset({
    "si", "sisi", "si si", "dale", "ok", "oka", "okey", "listo", "obvio",
    "claro", "de una", "vale", "correcto", "esa", "esa si", "perfecto",
    "si porfa", "si por favor", "sirve", "me sirve", "esa misma",
})


def afirmacion_sobre_lo_unico(texto: str, opciones: list[str]) -> int | None:
    """Un "sí" cuando hay UNA sola opción en pantalla apunta a esa opción.

    Existe para el caso del tipeo: el bot pregunta "las 9:39 no las tengo,
    ¿te sirven las 9:30?" y la respuesta natural es "sí", no "1". Pedirle un
    número a alguien que ya contestó que sí es hacerlo contestar dos veces.

    La condición de UNA sola opción es lo que lo hace seguro: con ocho
    horarios en pantalla, "sí" no señala ninguno y esto no se activa.
    """
    if len(opciones) != 1:
        return None
    return 0 if _normalizar(texto) in AFIRMACIONES else None


def opcion_por_nombre(texto: str, opciones: list[str]) -> int | None:
    """El índice de la opción que la persona nombró, sin llamar al LLM.

    La lista dice "1. Juan Demo" y alguien contesta "Juan". Eso no es un número
    ni hace falta un modelo para resolverlo: está escrito en la pantalla que le
    acabamos de mandar. Antes iba al clasificador y costaba una llamada entera.

    Se exige que el match sea ÚNICO. Con "Juan Demo" y "Juana Pérez" en la
    misma lista, "Juan" es ambiguo, y ahí se prefiere volver a mostrar la lista
    antes que elegir por la persona. Elegir mal un profesional es peor que
    preguntar de nuevo.
    """
    limpio = _normalizar(texto)
    if not limpio or not opciones:
        return None

    # El paso del día habla en fechas, no en nombres de opción.
    por_dia = _dia_por_texto(texto, opciones)
    if por_dia is not None:
        return por_dia

    normalizadas = [_normalizar(o) for o in opciones]

    # Horarios: "9:30" tiene que encontrar "09:30".
    hora = _como_hora(texto)
    if hora:
        for i, o in enumerate(opciones):
            if o.strip() == hora:
                return i

    if limpio in normalizadas:
        return normalizadas.index(limpio)

    # Match parcial, solo si es inequívoco y la persona escribió lo suficiente
    # como para que no sea una coincidencia boba.
    if len(limpio) >= 3:
        empiezan = [i for i, o in enumerate(normalizadas) if o.startswith(limpio)]
        if len(empiezan) == 1:
            return empiezan[0]
    if len(limpio) >= 4:
        contienen = [i for i, o in enumerate(normalizadas) if limpio in o]
        if len(contienen) == 1:
            return contienen[0]
    return None


def es_numero_suelto(texto: str) -> bool:
    """¿El mensaje es sólo un número, sin nada más? ("3", "el 3", "opción 3")

    Se usa donde NO hay lista numerada en pantalla, y ahí un número no señala
    nada: es alguien contestando la pantalla anterior, que sí la tenía. En el
    resumen eso importa más que en ningún otro paso, porque el mensaje anterior
    fue una lista de horarios y el siguiente movimiento reserva.
    """
    return numero_elegido(texto, 10 ** 9) is not None


def numero_elegido(texto: str, cantidad: int) -> int | None:
    """¿La persona contestó con un número de la lista? Sin llamar al LLM.

    La mayoría de las respuestas de este flujo son "3" o "el 3". Resolverlas
    en código es gratis, instantáneo, y no puede equivocarse. El LLM se
    reserva para lo que de verdad necesita interpretación.

    Devuelve el índice base 0, o None si no es un número válido de la lista.
    """
    limpio = texto.strip().lower()
    for prefijo in ("el ", "la ", "opcion ", "opción ", "numero ", "número ", "#"):
        if limpio.startswith(prefijo):
            limpio = limpio[len(prefijo):].strip()

    if not limpio.isdigit():
        return None

    n = int(limpio)
    return n - 1 if 1 <= n <= cantidad else None
