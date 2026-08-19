"""
test_canal.py — El canal de Meta, probado sin credenciales y sin red.

QUÉ PRUEBA
----------
Las tres cosas que cambian al pasar de Twilio a Meta, que son justo las que no
se pueden descubrir leyendo el código:

  1. La firma va sobre el CUERPO CRUDO. Si alguien re-serializa el JSON antes
     de verificar, la firma deja de coincidir y el bot se queda mudo sin que
     ningún test lo note.
  2. El cuerpo viene anidado en `entry[].changes[].value` y puede traer varios
     mensajes, de varios negocios, en un solo POST.
  3. La ventana de 24 horas. Afuera de ella el texto libre NO sale, y el caso
     que rompe es el más normal de todos: el dueño contestando al día siguiente.

Todo con cuerpos armados a mano con la forma que documenta Meta. No hace falta
la app, ni el secreto real, ni un número conectado.

    python test_canal.py
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import timedelta

from src.canal.base import Eco, ventana_abierta
from src.canal.meta import CanalMeta
from src.fechas import ahora
from src.schemas import Tenant

VERDE, ROJO, GRIS, NEGRITA, FIN = "\033[32m", "\033[31m", "\033[90m", "\033[1m", "\033[0m"

SECRETO = "secreto-de-app-para-tests"
NEGOCIO = "+5491130032002"
CLIENTE = "+5491144556677"

ok = True


def chequear(nombre: str, cond: bool, detalle: str = "") -> None:
    global ok
    ok = ok and bool(cond)
    color = VERDE if cond else ROJO
    print(f"  {color}{'✓' if cond else '✗'}{FIN} {nombre}"
          + (f"{GRIS}  ({detalle}){FIN}" if detalle else ""))


def firmar(cuerpo: bytes) -> dict[str, str]:
    mac = hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()
    return {"x-hub-signature-256": f"sha256={mac}"}


def webhook(clave: str, mensajes: list[dict]) -> bytes:
    """Un POST con la forma que documenta Meta: entry → changes → value."""
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": NEGOCIO, "phone_number_id": "111"},
            clave: mensajes,
        }}]}],
    }).encode()


def texto(de: str, cuerpo: str) -> dict:
    return {"from": de, "id": "wamid.X", "timestamp": "1", "type": "text",
            "text": {"body": cuerpo}}


# ══════════════════════════════════════════════════════════════════

def t1_la_firma():
    print(f"\n{NEGRITA}[1] LA FIRMA VA SOBRE EL CUERPO CRUDO{FIN}")
    print(f"{GRIS}  Sin esto, cualquiera que sepa la URL inventa turnos.{FIN}")

    c = CanalMeta(app_secret=SECRETO)
    cuerpo = webhook("messages", [texto(CLIENTE, "hola")])

    chequear("un cuerpo bien firmado entra",
             c.firma_valida(cuerpo, firmar(cuerpo), ""))
    chequear("uno sin cabecera se rechaza",
             not c.firma_valida(cuerpo, {}, ""))
    chequear("una firma inventada se rechaza",
             not c.firma_valida(cuerpo, {"x-hub-signature-256": "sha256=00ff"}, ""))

    # El corazón del asunto: la firma es de ESTOS bytes. Un byte distinto
    # —re-serializar el JSON, por ejemplo— y ya no vale.
    cabeceras = firmar(cuerpo)
    reserializado = json.dumps(json.loads(cuerpo)).encode()
    chequear("un cuerpo re-serializado NO valida (por eso se pasan bytes)",
             not c.firma_valida(reserializado + b" ", cabeceras, ""))

    # Y sin secreto configurado se cierra, no se abre.
    chequear("sin META_APP_SECRET rechaza todo",
             not CanalMeta(app_secret="").firma_valida(cuerpo, cabeceras, ""))


def t2_leer_el_webhook():
    print(f"\n{NEGRITA}[2] LEER LO QUE MANDA META{FIN}")
    print(f"{GRIS}  Viene anidado, y puede traer varios mensajes en un POST.{FIN}")

    c = CanalMeta(app_secret=SECRETO)

    msjs = c.leer(webhook("messages", [texto(CLIENTE, "quiero un turno")]), {})
    chequear("saca el mensaje", len(msjs) == 1, f"{len(msjs)} mensajes")
    if msjs:
        chequear("con el teléfono de quien escribe", msjs[0].de == CLIENTE, msjs[0].de)
        chequear("y el número del negocio, que es lo que rutea el tenant",
                 msjs[0].para == NEGOCIO, msjs[0].para)
        chequear("y el texto", msjs[0].texto == "quiero un turno")

    varios = c.leer(webhook("messages", [
        texto(CLIENTE, "hola"), texto("+5491199887766", "buenas")]), {})
    chequear("dos mensajes en un POST salen los dos", len(varios) == 2, f"{len(varios)}")

    # Lo que NO tiene que romper: un webhook de estado de entrega no trae
    # ningún mensaje que contestar, y eso es normal, no un error.
    estados = c.leer(webhook("statuses", [{"status": "delivered"}]), {})
    chequear("un webhook de entrega no devuelve nada y no explota", estados == [])
    chequear("un cuerpo que no es JSON tampoco explota", c.leer(b"no soy json", {}) == [])

    # Un adjunto sin texto se descarta acá: el flujo ya tiene su plantilla para
    # eso y no necesita un mensaje vacío.
    sin_texto = c.leer(webhook("messages", [
        {"from": CLIENTE, "type": "image", "image": {"id": "1"}}]), {})
    chequear("un adjunto sin texto se descarta", sin_texto == [])


def t3_los_ecos():
    print(f"\n{NEGRITA}[3] EL DUEÑO CONTESTANDO DESDE SU CELULAR{FIN}")
    print(f"{GRIS}  Con Twilio esto no llegaba: el bot le hablaba encima.{FIN}")

    c = CanalMeta(app_secret=SECRETO)
    ecos = c.leer_ecos(webhook("message_echoes", [
        {"to": CLIENTE, "type": "text", "text": {"body": "ya te confirmo"}}]), {})

    chequear("llega el eco", len(ecos) == 1, f"{len(ecos)}")
    if ecos:
        chequear("con a quién le escribió", ecos[0].cliente == CLIENTE)
        chequear("y qué le dijo", "ya te confirmo" in ecos[0].texto)
    chequear("y un webhook normal no genera ecos",
             c.leer_ecos(webhook("messages", [texto(CLIENTE, "hola")]), {}) == [])


def t4_la_ventana():
    print(f"\n{NEGRITA}[4] LA VENTANA DE 24 HORAS{FIN}")
    print(f"{GRIS}  El caso que rompe: el dueño contesta del panel al otro día.{FIN}")

    ahora_ = ahora()
    hace = lambda h: (ahora_ - timedelta(hours=h)).isoformat()  # noqa: E731

    chequear("recién escribió → texto libre", ventana_abierta(hace(0.1), ahora_))
    chequear("hace 23 h → todavía se puede", ventana_abierta(hace(23), ahora_))
    chequear("hace 25 h → hace falta plantilla", not ventana_abierta(hace(25), ahora_))

    # Sin sello se contesta que NO, que es la respuesta segura: mandar plantilla
    # de más cuesta una plantilla; mandar texto libre de más pierde el mensaje.
    chequear("sin sello se asume cerrada", not ventana_abierta(None, ahora_))
    chequear("con un sello ilegible, también", not ventana_abierta("ayer", ahora_))


def t5_enviar():
    print(f"\n{NEGRITA}[5] EL ENVÍO{FIN}")

    llamadas = []

    class HttpFalso:
        async def post(self, url, json=None, headers=None):
            llamadas.append({"url": url, "cuerpo": json, "cabeceras": headers})
            class R:
                status_code = 200
                text = "{}"
            return R()

    c = CanalMeta(app_secret=SECRETO, cliente=HttpFalso())
    negocio = Tenant(business_id="peluqueria", slug="peluqueria", nombre="Demo",
                     numero_whatsapp=NEGOCIO, phone_number_id="111",
                     token_whatsapp="TOKEN-DEL-NEGOCIO")

    salio = asyncio.run(c.enviar(negocio, CLIENTE, "Tu turno quedó confirmado"))
    chequear("dice que salió", salio)
    if llamadas:
        l = llamadas[0]
        chequear("pega al phone_number_id del negocio", "/111/messages" in l["url"], l["url"])
        chequear("con el token DE ESE negocio",
                 l["cabeceras"]["Authorization"] == "Bearer TOKEN-DEL-NEGOCIO")
        chequear("manda el texto", l["cuerpo"]["text"]["body"].startswith("Tu turno"))
        # Meta documenta que sin el "+" le pega adelante el código de país del
        # negocio. Un turno confirmado a otro país no le llega a nadie.
        chequear("y el destino con «+»", l["cuerpo"]["to"].startswith("+"), l["cuerpo"]["to"])

    # Un negocio sin WhatsApp conectado no puede enviar, y eso se dice, no se
    # rompe: es el estado normal de todos hasta que hagan el Embedded Signup.
    sin_conectar = Tenant(business_id="x", slug="x", nombre="X", numero_whatsapp=NEGOCIO)
    chequear("un negocio sin conectar devuelve False, no explota",
             not asyncio.run(c.enviar(sin_conectar, CLIENTE, "hola")))

    # Y la salida para cuando la ventana está cerrada.
    llamadas.clear()
    asyncio.run(c.enviar_plantilla(negocio, CLIENTE, "reanudar", variables=["Ana"]))
    if llamadas:
        cu = llamadas[0]["cuerpo"]
        chequear("la plantilla va como type=template", cu["type"] == "template")
        chequear("con su nombre", cu["template"]["name"] == "reanudar")
        chequear("y sus variables",
                 cu["template"]["components"][0]["parameters"][0]["text"] == "Ana")


def main() -> int:
    print(f"\n{NEGRITA}EL CANAL DE META, SIN CREDENCIALES Y SIN RED{FIN}")
    t1_la_firma()
    t2_leer_el_webhook()
    t3_los_ecos()
    t4_la_ventana()
    t5_enviar()
    print(f"\n{'─' * 58}")
    print(f"{VERDE}El canal está listo para enchufar.{FIN}" if ok
          else f"{ROJO}Hay algo roto en el canal.{FIN}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
