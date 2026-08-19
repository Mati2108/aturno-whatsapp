"""
base.py — El contrato con el canal por donde entran y salen los mensajes.

Es la misma idea que `src/aturno/base.py`, y por el mismo motivo: el resto del
sistema habla con esta interfaz y no sabe si del otro lado hay Twilio o Meta.
Cambiar de proveedor pasa a ser una variable de entorno en vez de cirugía sobre
`webhook.py`.

    CANAL=twilio   el sandbox de hoy, un número compartido
    CANAL=meta     Cloud API, el número propio de cada negocio

POR QUÉ HACE FALTA AHORA
------------------------
Con Twilio no había alternativa que abstraer. Con Meta hay tres diferencias que
no son de detalle —firma distinta, cuerpo distinto, y una ventana de 24 horas
que Twilio no tiene— y meterlas con `if` adentro del webhook dejaría el borde
del sistema decidiendo cosas de negocio.

LO QUE ESTA CAPA NO HACE
------------------------
No entiende, no decide y no redacta. Recibe bytes y devuelve `MensajeEntrante`;
recibe texto y lo entrega. Todo lo demás —el grafo, las plantillas, la máquina
de estados— no se entera de que esto cambió.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime, timedelta

from src.fechas import ahora as ahora_del_negocio
from src.schemas import MensajeEntrante, Tenant


# Cuánto dura la ventana de servicio de Meta. No es configurable: es la regla
# de la plataforma. Se escribe una vez acá y no se repite en ningún lado.
VENTANA_HORAS = 24


class Eco:
    """Un mensaje que el DUEÑO mandó desde su celular, no el bot.

    Existe sólo en Meta, y sólo con Coexistencia: el negocio sigue usando la app
    de WhatsApp Business en su teléfono y Meta avisa por el webhook
    `smb_message_echoes` cada vez que contesta desde ahí.

    Es lo que permite que el bot se calle solo cuando el dueño está atendiendo.
    Con Twilio esto no llegaba nunca: si el dueño contestaba por afuera, el bot
    seguía hablando encima y los dos le escribían a la misma persona.
    """

    __slots__ = ("negocio", "cliente", "texto")

    def __init__(self, negocio: str, cliente: str, texto: str) -> None:
        self.negocio, self.cliente, self.texto = negocio, cliente, texto

    def __repr__(self) -> str:  # pragma: no cover — sólo para leer logs
        return f"Eco({self.negocio} → {self.cliente}: {self.texto[:24]!r})"


def ventana_abierta(ultimo_en: str | None, ahora: datetime | None = None) -> bool:
    """¿Se le puede mandar texto libre a esta persona, o hace falta plantilla?

    LA REGLA
    Meta abre una ventana de 24 horas cuando la persona escribe, y la reinicia
    con cada mensaje suyo. Adentro se puede mandar cualquier cosa; afuera, sólo
    plantillas aprobadas.

    POR QUÉ IMPORTA MÁS DE LO QUE PARECE
    El caso que rompe no es la conversación normal —ahí la persona acaba de
    escribir— sino los mensajes que salen SOLOS: el aviso de que entró la seña,
    el de que se venció, y sobre todo **el dueño contestando desde el panel al
    día siguiente**, que en una peluquería es lo más normal del mundo. Ese
    mensaje, sin esto, no sale y nadie se entera.

    EL DATO YA EXISTÍA
    `ultimo_en` es el sello del último mensaje entrante, que `entender` escribe
    en cada vuelta y que hoy se usa para vencer la sesión a los 30 minutos. La
    ventana de Meta se contesta con el mismo dato, así que no hay estado nuevo
    que mantener ni que se pueda desincronizar.

    Sin sello, se contesta que NO. Es la respuesta segura: mandar por plantilla
    cuando se podía texto libre cuesta una plantilla; al revés, el mensaje se
    pierde en silencio.
    """
    if not ultimo_en:
        return False
    try:
        cuando = datetime.fromisoformat(ultimo_en)
    except ValueError:
        return False
    return (ahora or ahora_del_negocio()) - cuando < timedelta(hours=VENTANA_HORAS)


class Canal(ABC):
    """Por dónde entran y salen los mensajes de WhatsApp."""

    #: Para logs y para `/salud`. No se usa para decidir nada.
    nombre: str = "?"

    @abstractmethod
    def firma_valida(self, cuerpo: bytes, cabeceras: Mapping[str, str], url: str) -> bool:
        """¿Este POST lo mandó el proveedor, o cualquiera que sepa la URL?

        El webhook es una URL pública: sin esto, quien la descubra puede
        inventar mensajes y turnos. Cada proveedor firma distinto, y esa
        diferencia es justamente lo que esta capa esconde.
        """

    @abstractmethod
    def leer(self, cuerpo: bytes, formulario: Mapping[str, str]) -> list[MensajeEntrante]:
        """Los mensajes de clientes que trae este POST, ya normalizados.

        Devuelve una LISTA y no un mensaje: Meta puede mandar varios en un solo
        webhook. Twilio manda uno, y devuelve una lista de uno — que quien
        llama trate los dos casos igual es el punto.

        Una lista vacía es normal y no es un error: un webhook de estado
        —entregado, leído— no trae ningún mensaje que contestar.
        """

    def leer_ecos(self, cuerpo: bytes, formulario: Mapping[str, str]) -> list[Eco]:
        """Lo que el dueño contestó desde su celular. Ver `Eco`.

        Vacío por defecto: es una función de Meta con Coexistencia y un canal
        que no la tenga sigue siendo válido. Hacerlo abstracto obligaría a
        Twilio a implementar algo que no puede saber.
        """
        return []

    @abstractmethod
    async def enviar(self, negocio: Tenant, destino: str, texto: str) -> bool:
        """Manda el texto. Devuelve si salió.

        Devuelve un bool y no lanza porque quien llama ya decidió qué decir: si
        el envío falla, lo que hay que hacer es dejarlo en el log y seguir
        atendiendo, nunca cortar la conversación.
        """
