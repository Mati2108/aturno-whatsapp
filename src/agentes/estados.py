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
from enum import Enum


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
