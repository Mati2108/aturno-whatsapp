"""
meta.py — El canal de la Cloud API de Meta.

QUÉ CAMBIA RESPECTO DE TWILIO
-----------------------------
Tres cosas, y ninguna es cosmética:

1. **El número es del negocio, no nuestro.** Con Coexistencia, el negocio
   conecta el WhatsApp que ya usa —el que sus clientes tienen agendado— y sigue
   usándolo desde su celular. No hay que comprar ni repartir números.
2. **La firma va sobre el cuerpo crudo.** Meta manda `X-Hub-Signature-256` con
   un HMAC-SHA256 del body tal como viajó. Twilio firmaba sobre la URL más los
   campos del formulario. Si el cuerpo se re-serializa antes de verificar, la
   firma no coincide nunca — por eso `leer` y `firma_valida` reciben los BYTES.
3. **Existe la ventana de 24 horas.** Fuera de ella sólo se puede mandar
   plantilla aprobada. Ver `ventana_abierta` en `base.py`.

QUIÉN PAGA
----------
El negocio. Como Tech Provider, cada uno engancha su tarjeta a su propia cuenta
de WhatsApp y Meta le factura directo. Este servicio no paga mensajes: por eso
el costo por cliente deja de escalar con el volumen.

EL TOKEN ES POR NEGOCIO
-----------------------
No hay una credencial global como el auth token de Twilio: cada negocio tiene su
`phone_number_id` y su token, que salen del Embedded Signup y viven en aturno.
Por eso el token entra por parámetro y no por `config()`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping

import httpx

from src.canal.base import Canal, Eco
from src.config import config
from src.schemas import MensajeEntrante, Tenant

logger = logging.getLogger("pipeline.canal.meta")

# La versión de la Graph API contra la que está escrito esto. Fija y no "la
# última": Meta cambia formas entre versiones, y que una actualización silenciosa
# rompa el envío de turnos un martes a la mañana no es una hipótesis.
VERSION = "v26.0"
BASE = f"https://graph.facebook.com/{VERSION}"


class CanalMeta(Canal):
    """Cloud API. Un número por negocio, con su propio token."""

    nombre = "meta"

    def __init__(self, app_secret: str = "", cliente: httpx.AsyncClient | None = None) -> None:
        self._secreto = app_secret or config().meta_app_secret
        self._http = cliente or httpx.AsyncClient(timeout=20.0)

    # ---------- entrada ----------

    def firma_valida(self, cuerpo: bytes, cabeceras: Mapping[str, str], url: str) -> bool:
        """HMAC-SHA256 del cuerpo crudo, contra `X-Hub-Signature-256`.

        `url` se ignora a propósito —Meta no la firma— pero está en la firma del
        método porque Twilio sí la necesita. Es el precio de tener un contrato
        único, y es más barato que dos caminos en el webhook.

        Sin secreto configurado devuelve False y lo dice fuerte. La alternativa
        —dejar pasar todo mientras falta una variable de entorno— es exactamente
        cómo se ve un webhook abierto que nadie nota.
        """
        if not self._secreto:
            logger.error("META_APP_SECRET vacío: rechazo el webhook. "
                         "Sin secreto no se puede distinguir a Meta de cualquiera.")
            return False

        firma = cabeceras.get("x-hub-signature-256") or cabeceras.get("X-Hub-Signature-256")
        if not firma or not firma.startswith("sha256="):
            logger.warning("webhook sin X-Hub-Signature-256")
            return False

        esperada = hmac.new(self._secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
        # Comparación en tiempo constante: `==` corta en el primer byte distinto
        # y esa diferencia es medible. Mismo motivo que en `webhooks.js` de aturno.
        return hmac.compare_digest(esperada, firma.removeprefix("sha256="))

    def leer(self, cuerpo: bytes, formulario: Mapping[str, str]) -> list[MensajeEntrante]:
        """Los mensajes de texto de clientes que trae el webhook.

        Se descarta en silencio todo lo demás —estados de entrega, adjuntos sin
        texto, ecos— porque un webhook que no trae nada para contestar es lo
        normal, no un error. `formulario` se ignora: Meta manda JSON.
        """
        salida = []
        for valor in self._valores(cuerpo, "messages"):
            negocio = (valor.get("metadata") or {}).get("display_phone_number")
            for m in valor.get("messages") or []:
                texto = ((m.get("text") or {}).get("body") or "").strip()
                if not texto or not negocio:
                    continue
                try:
                    salida.append(MensajeEntrante(
                        de=m.get("from", ""), para=negocio, texto=texto))
                except Exception:  # noqa: BLE001 — un mensaje raro no tumba el lote
                    logger.warning("mensaje de Meta con forma inesperada", exc_info=True)
        return salida

    def leer_ecos(self, cuerpo: bytes, formulario: Mapping[str, str]) -> list[Eco]:
        """Lo que el dueño mandó desde su celular. Ver `Eco` en base.py."""
        salida = []
        for valor in self._valores(cuerpo, "message_echoes"):
            negocio = (valor.get("metadata") or {}).get("display_phone_number") or ""
            for m in valor.get("message_echoes") or []:
                texto = ((m.get("text") or {}).get("body") or "").strip()
                if texto:
                    salida.append(Eco(negocio=negocio, cliente=m.get("to", ""), texto=texto))
        return salida

    @staticmethod
    def _valores(cuerpo: bytes, clave: str) -> list[dict]:
        """Los `value` del webhook que contienen esa clave.

        Meta anida todo en `entry[].changes[].value`, y en un mismo POST pueden
        venir varios negocios y varios mensajes. Recorrerlo en un solo lugar
        evita repetir tres `for` anidados en cada lector.
        """
        try:
            datos = json.loads(cuerpo or b"{}")
        except Exception:  # noqa: BLE001
            logger.warning("cuerpo del webhook que no es JSON")
            return []
        salida = []
        for entrada in datos.get("entry") or []:
            for cambio in entrada.get("changes") or []:
                valor = cambio.get("value") or {}
                if valor.get(clave):
                    salida.append(valor)
        return salida

    # ---------- salida ----------

    async def enviar(self, negocio: Tenant, destino: str, texto: str) -> bool:
        """Manda un mensaje de servicio, o sea texto libre.

        Sólo vale dentro de la ventana de 24 h: quien llama tiene que haberlo
        chequeado con `ventana_abierta`. Afuera, Meta rechaza el envío y hay
        que usar `enviar_plantilla`.

        El `+` del destino se manda siempre. Meta documenta que sin él le pega
        adelante el código de país del NEGOCIO, y un turno confirmado a un
        número de otro país es un turno que no le llega a nadie.
        """
        if not negocio.phone_number_id or not negocio.token_whatsapp:
            logger.error("%s no tiene WhatsApp conectado: no puedo enviar",
                         negocio.business_id)
            return False

        cuerpo = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": destino if destino.startswith("+") else f"+{destino}",
            "type": "text",
            "text": {"body": texto},
        }
        return await self._postear(negocio, cuerpo, "texto")

    async def enviar_plantilla(self, negocio: Tenant, destino: str,
                               plantilla: str, idioma: str = "es_AR",
                               variables: list[str] | None = None) -> bool:
        """La salida cuando la ventana está cerrada.

        Es el único camino para hablarle a alguien que no escribió en las
        últimas 24 horas — el caso del dueño contestando desde el panel al día
        siguiente. La plantilla tiene que estar aprobada por Meta de antemano;
        pedirlas es trámite y tarda, así que va antes que este código.
        """
        cuerpo: dict = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": destino if destino.startswith("+") else f"+{destino}",
            "type": "template",
            "template": {"name": plantilla, "language": {"code": idioma}},
        }
        if variables:
            cuerpo["template"]["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": v} for v in variables],
            }]
        return await self._postear(negocio, cuerpo, f"plantilla {plantilla}")

    async def _postear(self, negocio: Tenant, cuerpo: dict, que: str) -> bool:
        try:
            r = await self._http.post(
                f"{BASE}/{negocio.phone_number_id}/messages",
                json=cuerpo,
                headers={"Authorization": f"Bearer {negocio.token_whatsapp}"},
            )
        except Exception:  # noqa: BLE001 — no poder enviar no puede cortar el flujo
            logger.exception("no se pudo enviar %s a %s", que, negocio.business_id)
            return False

        if r.status_code >= 300:
            # El detalle va al log porque los errores de Meta son accionables y
            # distintos entre sí: token vencido, ventana cerrada, número no
            # registrado. Sin el cuerpo, todos se ven igual.
            logger.error("Meta rechazó %s para %s: %d %s",
                         que, negocio.business_id, r.status_code, r.text[:200])
            return False
        logger.info("→ %s enviado por Meta (%s)", que, negocio.business_id)
        return True
