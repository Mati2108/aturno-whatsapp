"""
clasificador.py — El único lugar donde interviene el LLM.

QUÉ HACE Y QUÉ NO
-----------------
Recibe el mensaje y el paso en el que está la conversación, y devuelve un
objeto: qué quiso decir la persona y qué datos trae. Nada más. No redacta, no
decide el próximo paso, no llama herramientas.

El prompt bajó de ~1.145 tokens a ~150 porque todo lo que se fue era control
de flujo y redacción, y eso ahora vive en código.

DOS CANDADOS
------------
1. `with_structured_output` con un modelo Pydantic: la respuesta ya llega
   validada o falla. No hay parseo de JSON a mano ni JSON roto que se cuele.
2. El enum de intenciones es cerrado. Una intención inventada no valida, y el
   fallback es DESCONOCIDO — que la máquina sabe manejar. El usuario nunca ve
   un error: ve la plantilla de reintento del paso en el que está.
"""

from __future__ import annotations

import logging

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.agentes.estados import Estado, Intencion
from src.modelo import construir_modelo

logger = logging.getLogger("pipeline.clasificador")


class Entidades(BaseModel):
    """Los datos que el mensaje pueda traer. Todos opcionales."""

    servicio: str | None = Field(default=None, description="Nombre del servicio mencionado.")
    profesional: str | None = Field(
        default=None, description="Nombre de la persona. 'cualquiera' si le da igual."
    )
    fecha: str | None = Field(default=None, description="Fecha en AAAA-MM-DD.")
    hora: str | None = Field(default=None, description="Hora en HH:MM, 24 horas.")
    nombre: str | None = Field(default=None, description="Nombre de la persona que escribe.")
    consulta: str | None = Field(
        default=None, description="Si pregunta algo del negocio, qué preguntó."
    )


class Clasificacion(BaseModel):
    """Lo único que el modelo puede devolver."""

    intent: Intencion
    entities: Entidades = Field(default_factory=Entidades)
    needs_clarification: bool = Field(
        default=False, description="True si el mensaje es demasiado ambiguo."
    )


INSTRUCCIONES = """\
Clasificás mensajes de WhatsApp de un negocio de turnos. NO redactás respuestas.

Devolvés qué quiso decir la persona y qué datos trae. Nada más.

Paso actual de la conversación: {estado}
{opciones}
Hoy es {hoy} ({dia_semana}).
{calendario}

Reglas de extracción:
- "mañana" sola es el DÍA DE MAÑANA. "a la mañana" es la franja horaria.
- Convertí los días a AAAA-MM-DD usando la tabla de arriba, no calcules.
- "cualquiera", "me da igual", "el que sea" -> profesional: "cualquiera".
- Si pregunta precio, horario, dirección o formas de pago -> consultar_info.
- "más", "otro horario", "más tarde", "no me sirve ninguno" -> ver_mas.
- Pedir una persona, un humano, "quiero hablar con alguien", "pasame con
  alguien", "esto no me sirve", quejarse de hablar con un bot ->
  hablar_con_persona.
- Si el mensaje no encaja en ninguna intención, usá desconocido.
"""


def construir_clasificador():
    """Devuelve la cadena. Se arma una vez y se reusa."""
    prompt = ChatPromptTemplate.from_messages(
        [("system", INSTRUCCIONES), ("human", "{mensaje}")]
    )
    # temperature=0 y salida estructurada: la misma frase clasifica igual
    # siempre. En un flujo de turnos, la variabilidad no aporta nada.
    modelo = construir_modelo().with_structured_output(Clasificacion)
    return prompt | modelo


async def clasificar(
    cadena,
    mensaje: str,
    estado: Estado,
    opciones: list[str] | None,
    hoy_iso: str,
    dia_semana: str,
    calendario: str,
) -> Clasificacion:
    """Clasifica, y ante cualquier falla devuelve DESCONOCIDO.

    Nunca propaga la excepción: un modelo caído o una respuesta rara no pueden
    dejar sin contestar a la persona. La máquina de estados sabe qué hacer con
    DESCONOCIDO — repetir el pedido del paso actual — y eso siempre es mejor
    que un error.
    """
    texto_opciones = ""
    if opciones:
        texto_opciones = "Opciones válidas ahora: " + "; ".join(opciones) + "\n"

    try:
        resultado = await cadena.ainvoke({
            "mensaje": mensaje,
            "estado": estado.value,
            "opciones": texto_opciones,
            "hoy": hoy_iso,
            "dia_semana": dia_semana,
            "calendario": calendario,
        })
        logger.info(
            "clasificado [%s] '%s' → %s",
            estado.value, mensaje[:32], resultado.intent.value,
        )
        return resultado
    except Exception as e:  # noqa: BLE001 — el flujo nunca se corta por esto
        logger.warning("El clasificador falló (%s); sigo con DESCONOCIDO", e)
        return Clasificacion(intent=Intencion.DESCONOCIDO)
