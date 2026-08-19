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
1. Salida estructurada más `Clasificacion.model_validate` a la vuelta: la
   respuesta llega validada o falla. No hay parseo de JSON a mano ni JSON roto
   que se cuele. (La validación es explícita y no de `with_structured_output`
   porque el esquema que viaja se escribe a mano — ver `ESQUEMA`.)
2. El enum de intenciones es cerrado. Una intención inventada no valida, y el
   fallback es DESCONOCIDO — que la máquina sabe manejar. El usuario nunca ve
   un error: ve la plantilla de reintento del paso en el que está.

Y UN TECHO
----------
Pasado `TOPE_DIARIO_USD`, acá se deja de llamar al modelo y todo cae en
DESCONOCIDO. El bot sigue atendiendo con los atajos —el 88% de los mensajes no
pasa por acá— en vez de vaciar la cuenta. Ver `tope_alcanzado`.
"""

from __future__ import annotations

import logging
import time

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.agentes.estados import Estado, Intencion
from src.config import config
from src.gasto import GASTO
from src.modelo import construir_modelo, hay_credencial

logger = logging.getLogger("pipeline.clasificador")


class Entidades(BaseModel):
    """Los datos que el mensaje pueda traer. Todos opcionales.

    Estas clases ya NO son lo que viaja: lo que se le manda al modelo es
    `ESQUEMA`, más abajo. Acá sólo se valida lo que vuelve, así que este
    docstring es gratis. No siempre lo fue —ver el comentario de `ESQUEMA`—.

    Sin `description` en los campos igual, y a propósito: lo que dirían está en
    INSTRUCCIONES, que se manda de todas formas. Describirlo dos veces sería
    pagarlo dos veces, y los nombres de los campos alcanzan.
    """

    servicio: str | None = None
    profesional: str | None = None
    fecha: str | None = None          # AAAA-MM-DD
    hora: str | None = None           # HH:MM
    nombre: str | None = None
    consulta: str | None = None


class Clasificacion(BaseModel):
    """Lo único que el modelo puede devolver.

    `needs_clarification` estaba acá y no lo leía nadie: el flujo ya sabe qué
    hacer con DESCONOCIDO. Un campo que no se usa igual se paga en tokens en
    cada llamada, así que se fue.
    """

    intent: Intencion
    entities: Entidades = Field(default_factory=Entidades)


# Los campos de `Entidades`, en el orden en que se declararon. Se derivan del
# modelo y no se escriben a mano: agregar un campo allá tiene que alcanzar.
CAMPOS = tuple(Entidades.model_fields)


# LO QUE SE LE MANDA AL MODELO. No es el esquema de Pydantic, y esa es la
# diferencia entre pagar 1.391 tokens por llamada y pagar 694.
#
# `with_structured_output(Clasificacion)` manda `model_json_schema()`, y ese
# esquema viaja ENTERO en cada llamada aunque la persona haya escrito "dale".
# Medido con `count_tokens` sobre claude-haiku-4-5, era el 70% de los 1.998
# tokens de entrada de cada clasificación. Dos cosas lo inflaban:
#
#   · Los docstrings de las clases. Pydantic los serializa como `description`.
#     El más caro era el de `Entidades` — el párrafo que explicaba por qué no
#     hay que pagar descripciones—: 460 tokens en cada mensaje de cada
#     conversación.
#   · `str | None = None`. Genera `anyOf: [{"type":"string"},{"type":"null"}]`
#     más un `title`, seis veces, más `$defs` con `$ref` para las clases
#     anidadas. Otros 237.
#
# Escrito a mano queda plano: un tipo por campo y el enum al lado. Las
# entidades suben al nivel de arriba porque anidarlas obliga a un `$def`, y un
# `$def` cuesta más que los seis campos.
#
# EL CANDADO NO SE PIERDE. Antes lo daba `with_structured_output`; ahora lo da
# `Clasificacion.model_validate` sobre lo que vuelve. Sigue siendo "llega
# validado o falla" —y falla hacia el respaldo, y después hacia DESCONOCIDO—,
# sólo que la validación es explícita y está tres líneas más abajo.
ESQUEMA = {
    "title": "clasificar",
    "description": "Qué quiso decir la persona y qué datos trae.",
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": [i.value for i in Intencion]},
        **{campo: {"type": "string"} for campo in CAMPOS},
    },
    "required": ["intent"],
}


def _a_clasificacion(bruto: dict) -> Clasificacion:
    """Del dict plano que devuelve el modelo al objeto validado que usa el flujo.

    Los vacíos se normalizan a `None`: con el esquema plano el modelo puede
    mandar `""` donde antes mandaba `null`, y el flujo distingue "no lo dijo"
    de "lo dijo vacío" en varios lados (`ent.get("consulta") or ...`).
    """
    bruto = bruto or {}
    return Clasificacion(
        intent=bruto.get("intent") or Intencion.DESCONOCIDO,
        entities=Entidades(**{c: (bruto.get(c) or None) for c in CAMPOS}),
    )


INSTRUCCIONES = """\
Clasificás mensajes de WhatsApp de un negocio de turnos. NO redactás respuestas.

Devolvés qué quiso decir la persona y qué datos trae. Nada más.

Paso actual de la conversación: {estado}
{opciones}
Hoy es {hoy} ({dia_semana}).
{calendario}

Reglas de extracción:
- Devolvé SOLO los campos que la persona dijo. Los que no dijo, omitilos.
- `consulta` va únicamente con consultar_info: es qué está preguntando.
- fecha en AAAA-MM-DD, hora en HH:MM de 24 horas.
- La hora es EXACTAMENTE la que escribió la persona, aunque no esté en las
  opciones. Si escribe 10:37, devolvés 10:37, nunca 10:30. Redondear al
  horario más cercano es una decisión que toma el sistema y se le pregunta a
  la persona; hacerlo acá la esconde y le reserva otra hora sin avisarle.
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


def construir_clasificador(proveedor: str | None = None):
    """Devuelve la cadena. Se arma una vez y se reusa."""
    prompt = ChatPromptTemplate.from_messages(
        [("system", INSTRUCCIONES), ("human", "{mensaje}")]
    )
    # temperature=0 y salida estructurada: la misma frase clasifica igual
    # siempre. En un flujo de turnos, la variabilidad no aporta nada.
    #
    # El esquema va como dict y la validación al final: ver el comentario de
    # `ESQUEMA`. `max_tokens=256` porque lo que vuelve es un objeto de siete
    # campos cortos; el default de 1024 nunca se usaba.
    modelo = construir_modelo(proveedor, max_tokens=256, motivo="clasificar")
    return prompt | modelo.with_structured_output(ESQUEMA) | _a_clasificacion


def construir_respaldos() -> list[tuple[str, object]]:
    """Las cadenas de respaldo, en orden, para cuando el principal no conteste.

    Se arman AL ARRANCAR y no en el momento de la falla: armar una cadena
    importa una librería y construye un cliente, y el momento en que el
    proveedor principal se cayó es el peor para descubrir que falta un paquete.

    Un proveedor declarado sin credencial se saltea acá, con un aviso. Es lo que
    evita que la cadena de respaldo sea una lista de nombres incapaz de disparar
    ninguno — que es exactamente cómo se ve la cobertura falsa.
    """
    salida = []
    for nombre in config().respaldos():
        if not hay_credencial(nombre):
            logger.warning("respaldo %s declarado pero sin credencial: lo salteo", nombre)
            continue
        try:
            salida.append((nombre, construir_clasificador(nombre)))
        except Exception as e:  # noqa: BLE001 — un respaldo roto no tumba el arranque
            logger.error("no se pudo armar el respaldo %s: %s", nombre, e)
    if salida:
        logger.info("respaldo del clasificador: %s", ", ".join(n for n, _ in salida))
    return salida


# Cuánto se deja de intentar con el principal después de que falle.
#
# Sin esto, con el proveedor caído CADA mensaje paga la llamada fallida antes de
# ir al respaldo: esa latencia se le suma a todas las conversaciones y el mismo
# error se repite en el log tantas veces como mensajes haya. Con esto se paga
# una vez y se vuelve a probar recién pasado el rato — porque la caída también
# se termina, y quedarse en el respaldo para siempre sería la otra forma de
# equivocarse.
DESCANSO_TRAS_FALLA = 300  # segundos


def tope_alcanzado() -> bool:
    """¿Ya se gastó lo del día? Con el tope en 0, nunca."""
    tope = config().tope_diario_usd
    return tope > 0 and GASTO.usd_hoy() >= tope

_principal_caido_hasta = 0.0


def principal_disponible(ahora: float | None = None) -> bool:
    """¿Se puede volver a intentar con el proveedor principal?"""
    return (ahora if ahora is not None else time.monotonic()) >= _principal_caido_hasta


def _anotar_falla(ahora: float | None = None) -> None:
    global _principal_caido_hasta
    _principal_caido_hasta = (
        ahora if ahora is not None else time.monotonic()) + DESCANSO_TRAS_FALLA


def _anotar_exito() -> None:
    global _principal_caido_hasta
    _principal_caido_hasta = 0.0


async def clasificar(
    cadena,
    mensaje: str,
    estado: Estado,
    opciones: list[str] | None,
    hoy_iso: str,
    dia_semana: str,
    calendario: str,
    respaldos: list[tuple[str, object]] | None = None,
) -> Clasificacion:
    """Clasifica. Si el principal no contesta prueba el respaldo, y si no, DESCONOCIDO.

    Nunca propaga la excepción: un modelo caído o una respuesta rara no pueden
    dejar sin contestar a la persona. La máquina de estados sabe qué hacer con
    DESCONOCIDO —repetir el pedido del paso actual— y eso siempre es mejor que
    un error.

    Pero DESCONOCIDO para TODO tampoco es funcionar. Con el proveedor caído el
    bot recibe y no entiende nada, y como el paso del nombre sale únicamente de
    acá, un cliente nuevo no puede sacar turno: se traba y termina derivado a
    una persona. Pasó, y por lo más tonto —se acabó el crédito de la cuenta—,
    que además ningún chequeo de credenciales agarra, porque la clave es válida.

    Por eso el respaldo es OTRO proveedor y no un reintento: reintentar contra
    una cuenta sin crédito falla igual las tres veces.
    """
    datos = {
        "mensaje": mensaje,
        "estado": estado.value,
        "opciones": ("Opciones válidas ahora: " + "; ".join(opciones) + "\n")
                    if opciones else "",
        "hoy": hoy_iso,
        "dia_semana": dia_semana,
        "calendario": calendario,
    }

    # El techo del día. Antes de cualquier proveedor, porque el respaldo también
    # se paga: un tope que sólo mira al principal se saltea solo.
    #
    # DESCONOCIDO y no una excepción: la máquina de estados ya sabe manejarlo
    # —repite el pedido del paso— así que el bot sigue atendiendo con los
    # atajos, que hoy resuelven el 88% de los mensajes. Ver `tope_diario_usd`.
    if tope_alcanzado():
        logger.error(
            "TECHO DE GASTO ALCANZADO (%.2f USD hoy, tope %.2f): no llamo al "
            "modelo. El bot sigue con los atajos; el texto libre cae en "
            "DESCONOCIDO. Subí TOPE_DIARIO_USD o esperá al día siguiente.",
            GASTO.usd_hoy(), config().tope_diario_usd)
        return Clasificacion(intent=Intencion.DESCONOCIDO)

    # Al principal se le pregunta salvo que acabe de fallar. Ver el comentario
    # de DESCANSO_TRAS_FALLA: con el proveedor caído, insistir en cada mensaje
    # le suma su timeout a todas las conversaciones.
    if principal_disponible():
        try:
            resultado = await cadena.ainvoke(datos)
            _anotar_exito()
            logger.info("clasificado [%s] '%s' → %s",
                        estado.value, mensaje[:32], resultado.intent.value)
            return resultado
        except Exception as e:  # noqa: BLE001 — el flujo nunca se corta por esto
            _anotar_falla()
            logger.warning("El clasificador principal falló (%s)", e)

    for nombre, respaldo in (respaldos or []):
        try:
            resultado = await respaldo.ainvoke(datos)
            # WARNING y no INFO: que el bot esté andando con el respaldo es algo
            # que alguien tiene que ver en los logs. Funciona, pero no está bien.
            logger.warning("clasificado POR RESPALDO (%s) [%s] '%s' → %s",
                           nombre, estado.value, mensaje[:32], resultado.intent.value)
            return resultado
        except Exception as e:  # noqa: BLE001 — se prueba el siguiente
            logger.error("el respaldo %s también falló (%s)", nombre, e)

    logger.warning("no contestó ningún proveedor; sigo con DESCONOCIDO")
    return Clasificacion(intent=Intencion.DESCONOCIDO)
