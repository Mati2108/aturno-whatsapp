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
