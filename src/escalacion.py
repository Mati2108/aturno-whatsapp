"""
escalacion.py — Cuando la conversación deja de ser del bot y pasa a una persona.

QUÉ PASA CUANDO ALGUIEN PIDE HABLAR CON ALGUIEN
-----------------------------------------------
Hasta acá el bot contestaba con el teléfono del negocio y seguía como si nada.
Eso descarga el problema en el cliente: le da un número para que llame él,
desde un chat que ya estaba abierto. Es la versión perezosa de atender.

Lo que tiene que pasar es al revés: **el negocio se entera y toma el control**.
La conversación queda marcada, el bot se calla, y quien atiende contesta por el
mismo hilo. Para la persona no cambió nada — sigue escribiendo al mismo número.

QUÉ HACE ESTE MÓDULO Y QUÉ NO
-----------------------------
Acá está la mitad que vive en el bot: detectar, marcar, avisar y callarse.

La otra mitad vive en aturno y HOY YA EXISTE, pero por otro camino: el panel
recibe todos los mensajes por `/api/whatsapp/bot/evento`, y el del pedido viaja
con `necesita_humano` en true. Por eso `_escalar` (flujo.py) cuenta el panel
configurado como aviso entregado aunque este webhook no esté puesto.

`ESCALACION_WEBHOOK` queda para quien no use el panel —un negocio que quiera el
aviso en Slack o en su propio sistema— y el contrato sigue siendo este
`Escalacion`. Sin ninguno de los dos, el aviso queda en el log con todo lo
necesario para atenderlo a mano.

POR QUÉ UN WEBHOOK Y NO ESCRIBIR EN FIRESTORE
---------------------------------------------
Este servicio no tiene credenciales de Firebase y no las quiere: todo lo que
hace contra aturno pasa por endpoints públicos. Un webhook mantiene esa
propiedad — aturno decide qué hacer con el aviso, y el bot no puede tocar nada
más que eso.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx
from pydantic import BaseModel, Field

from src.fechas import ahora

logger = logging.getLogger("pipeline.escalacion")


class Escalacion(BaseModel):
    """El aviso que recibe el negocio. Es el contrato con aturno."""

    business_id: str = Field(description="Slug del negocio en aturno.")
    telefono: str = Field(description="Quién escribe, en formato +54...")
    nombre: str | None = Field(default=None, description="Si ya lo había dado.")
    motivo: str = Field(description="'pedido' si lo pidió, 'trabado' si se trabó.")
    paso: str = Field(description="En qué paso del flujo estaba.")
    ultimo_mensaje: str = Field(description="Lo último que escribió la persona.")
    momento: datetime = Field(default_factory=ahora)

    def resumen(self) -> str:
        """Una línea para el log, legible por alguien que atiende."""
        quien = self.nombre or self.telefono
        return (f"[{self.business_id}] {quien} pide una persona "
                f"({self.motivo}, en {self.paso}): «{self.ultimo_mensaje[:60]}»")


async def notificar(aviso: Escalacion, webhook: str | None) -> bool:
    """Avisa al negocio. Devuelve si el aviso llegó a algún lado.

    Nunca lanza. Un aviso que no se pudo entregar no puede además romper la
    conversación: la persona ya está pidiendo ayuda, y quedarse sin respuesta
    ahí es el peor momento posible.

    Sin webhook configurado, el aviso queda en el log. No es una solución, pero
    es visible: alguien que mire los logs del servicio ve el pedido con todo lo
    que necesita para contestar.
    """
    logger.warning("ESCALACIÓN · %s", aviso.resumen())

    if not webhook:
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.post(webhook, json=aviso.model_dump(mode="json"))
        if r.status_code >= 400:
            logger.error("el webhook de escalación devolvió %d", r.status_code)
            return False
        return True
    except Exception:  # noqa: BLE001 — avisar nunca puede tumbar la respuesta
        logger.exception("no se pudo avisar al negocio")
        return False
