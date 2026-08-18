"""
conversaciones.py — Los dos hilos que unen el bot con el panel de aturno.

EL PROBLEMA QUE RESUELVE
------------------------
El bot ya sabe callarse cuando alguien pide una persona, pero esa persona no
tiene forma de enterarse ni de contestar. Faltan dos caminos, uno en cada
dirección:

    bot → aturno    cada mensaje, para que el panel muestre la conversación
    aturno → bot    lo que escribe el dueño, para que salga por WhatsApp

Este módulo es el lado del bot de esos dos caminos.

POR QUÉ UN SECRETO COMPARTIDO Y NO FIREBASE
-------------------------------------------
Este servicio no tiene credenciales de Firebase y no las quiere: todo lo que
hace contra aturno pasa por endpoints públicos. Para estos dos caminos hace
falta algo más que público —cualquiera podría escribirle a un cliente en
nombre del negocio— pero no hace falta una identidad completa.

Un secreto compartido alcanza y mantiene la propiedad: si el bot se ve
comprometido, el atacante puede escribirle a los clientes de ese bot, y nada
más. No puede leer Firestore, ni tocar turnos, ni ver otros negocios.

QUÉ NO HACE
-----------
No guarda nada. El bot ya tiene el estado de la conversación en su
checkpointer; duplicar los mensajes acá sería una segunda fuente de verdad que
tarde o temprano se contradice con la primera. aturno guarda lo que muestra.
"""

from __future__ import annotations

import hmac
import logging

import httpx
from pydantic import BaseModel, Field

from src.config import config
from src.fechas import ahora

logger = logging.getLogger("pipeline.conversaciones")


class MensajeDelPanel(BaseModel):
    """Lo que el dueño escribió en el panel y hay que mandar por WhatsApp."""

    telefono: str = Field(min_length=8, description="A quién, en formato +54...")
    texto: str = Field(min_length=1, max_length=1500)
    business_id: str = Field(min_length=1)
    autor: str | None = Field(default=None, description="Quién del negocio contestó.")


class PedidoDeReindexado(BaseModel):
    """Solo el negocio. Nada más hace falta para volver a leer su conocimiento.

    Modelo propio y no `MensajeDelPanel`: ese exige un teléfono de ocho
    caracteres porque manda un mensaje a alguien, y acá no hay nadie a quien
    mandarle nada. Reusarlo obligaba al backend a inventar un teléfono falso
    para pasar la validación — y como no lo inventaba, el reindexado devolvía
    422 y no se reindexaba nunca. Un endpoint que exige un dato que no usa es
    un endpoint que va a fallar el día que alguien lo llame bien.
    """

    business_id: str = Field(min_length=1)


class Enviado(BaseModel):
    """La respuesta del bot al panel."""

    enviado: bool
    detalle: str
    momento: str


class EventoDeConversacion(BaseModel):
    """Un mensaje que pasó, en cualquiera de las dos direcciones.

    Se manda de a uno y en el momento, no en lote: el panel tiene que poder
    mostrar la conversación mientras está pasando, que es cuando el dueño
    puede hacer algo al respecto.
    """

    business_id: str
    telefono: str
    texto: str
    direccion: str = Field(description="'entrante' o 'saliente'")
    autor: str = Field(description="'cliente', 'bot', 'negocio' o 'sistema'")
    momento: str
    # Cuando esto es true, el panel tiene que hacer ruido.
    necesita_humano: bool = False
    # Distinto de lo de arriba, y confundirlos rompió el panel: `necesita_humano`
    # es "el cliente está esperando" y se apaga apenas el negocio contesta;
    # esto es "la conversación es del negocio" y sigue prendido hasta que el
    # bot la retoma. El botón para devolverla depende de ESTE.
    en_manos_humanas: bool = False
    paso: str | None = None
    # El nombre que la persona dio en la conversación, si llegó a darlo. El
    # panel lista teléfonos, y un teléfono no le dice nada a nadie: el dueño
    # tiene que abrir la conversación para saber a quién está por contestarle.
    nombre: str | None = None


def _secreto_valido(recibido: str | None) -> bool:
    """Compara en tiempo constante. Sin secreto configurado, nadie entra.

    `compare_digest` y no `==` porque la comparación normal corta en el primer
    byte distinto, y eso mide: con suficientes intentos se puede adivinar el
    secreto carácter por carácter. Es barato hacerlo bien.
    """
    esperado = config().panel_secreto
    if not esperado or not recibido:
        return False
    return hmac.compare_digest(esperado, recibido)


async def avisar_a_aturno(evento: EventoDeConversacion) -> bool:
    """Le manda el mensaje a aturno para que el panel lo muestre.

    Nunca lanza y nunca demora la respuesta al cliente: se llama en segundo
    plano. Que el panel no vea un mensaje es un problema; que la persona del
    otro lado se quede esperando porque el panel no contestó es peor.
    """
    cfg = config()
    if not cfg.panel_url or not cfg.panel_secreto:
        return False
    try:
        async with httpx.AsyncClient(timeout=8) as http:
            r = await http.post(
                f"{cfg.panel_url.rstrip('/')}/api/whatsapp/bot/evento",
                json=evento.model_dump(mode="json"),
                headers={"x-bot-secret": cfg.panel_secreto},
            )
        if r.status_code >= 400:
            logger.warning("aturno rechazó el evento (%d): %s",
                           r.status_code, r.text[:120])
            return False
        return True
    except Exception:  # noqa: BLE001
        logger.warning("no se pudo avisar la conversación a aturno", exc_info=True)
        return False


async def avisar_sin_respuesta(business_id: str, texto: str) -> bool:
    """Le cuenta a aturno que el bot no supo contestar esto.

    Antes esa pregunta se perdía: el bot decía "ese dato no lo tengo cargado" y
    ahí terminaba, así que el negocio nunca se enteraba de qué le estaban
    preguntando y nunca podía cargarlo. Esta llamada es la diferencia entre un
    bot que se queda como está y uno que mejora con el uso.

    Como todo lo que va al panel: en segundo plano y sin lanzar nunca. Que se
    pierda una pregunta de la lista es una lástima; que la persona se quede
    esperando porque el panel no contestó es peor.
    """
    cfg = config()
    if not cfg.panel_url or not cfg.panel_secreto:
        return False
    try:
        async with httpx.AsyncClient(timeout=8) as http:
            r = await http.post(
                f"{cfg.panel_url.rstrip('/')}/api/whatsapp/bot/sin-respuesta",
                json={"business_id": business_id, "texto": texto[:300]},
                headers={"x-bot-secret": cfg.panel_secreto},
            )
        return r.status_code < 400
    except Exception:  # noqa: BLE001
        logger.warning("no se pudo avisar la pregunta sin respuesta", exc_info=True)
        return False


def evento(business_id: str, telefono: str, texto: str, *, de_quien: str,
           necesita_humano: bool = False, en_manos_humanas: bool = False,
           paso: str | None = None,
           nombre: str | None = None) -> EventoDeConversacion:
    """Arma el evento.

    `de_quien` es 'cliente', 'bot', 'negocio' o 'sistema'. Los tres primeros
    son quien habló; 'sistema' es lo que PASÓ —el negocio tomó el control, el
    asistente la retomó— y el panel lo dibuja centrado, sin burbuja, porque no
    es de nadie.
    """
    return EventoDeConversacion(
        business_id=business_id,
        telefono=telefono,
        texto=texto,
        direccion="entrante" if de_quien == "cliente" else "saliente",
        autor=de_quien,
        momento=ahora().isoformat(),
        necesita_humano=necesita_humano,
        en_manos_humanas=en_manos_humanas,
        paso=paso,
        nombre=nombre,
    )
