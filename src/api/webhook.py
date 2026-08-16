"""
webhook.py — La puerta de entrada: WhatsApp (vía Twilio) → el sistema.

POR QUÉ RESPONDE VACÍO Y MANDA LA RESPUESTA APARTE
--------------------------------------------------
Twilio espera que el webhook conteste rápido; si tardás, corta la conexión y
reintenta, y el cliente recibe el mensaje dos veces. Un turno del agente
—LLM + RAG + consulta a aturno— puede tardar varios segundos, así que la
respuesta NO puede viajar en el cuerpo del webhook.

El patrón es de dos tiempos:

    1. El webhook valida, encola el trabajo y devuelve 200 vacío al instante.
    2. Una tarea en segundo plano procesa y manda la respuesta por la API REST
       de Twilio, como un mensaje nuevo.

Es más código que devolver TwiML, pero es la única forma que aguanta la
latencia real de un agente. Cambiarlo después sería rehacer esta capa entera.
"""

from __future__ import annotations

import logging

from fastapi import BackgroundTasks, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from twilio.request_validator import RequestValidator
from twilio.rest import Client

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from src.agentes import flujo
from src.agentes.flujo import construir_flujo, hilo_de
from src.aturno.doble import AturnoDoble
from src.config import TENANTS, config, tenant_por_numero
from src.fechas import calendario
from src.observabilidad import configurar_trazas, trazado_activo
from src.rag.indice import modelo_en_uso
from src.schemas import MensajeEntrante, Tenant

logger = logging.getLogger("pipeline.webhook")


def _configurar_logs() -> None:
    """Engancha nuestros logs al handler de uvicorn.

    Sin esto los mensajes de nivel INFO no aparecen: uvicorn configura el
    logging antes de importar la app, y el logger raíz queda en WARNING. Se
    veían los rechazos de firma pero no el flujo normal de mensajes — que es
    justamente lo que hay que poder observar.
    """
    de_uvicorn = logging.getLogger("uvicorn").handlers
    propio = logging.getLogger("pipeline")
    propio.handlers = de_uvicorn or propio.handlers
    propio.setLevel(logging.INFO)
    propio.propagate = False


app = FastAPI(
    title="aturno WhatsApp",
    description="Capa conversacional de aturno: sacar turnos por WhatsApp.",
    version="0.1.0",
)

# Por ahora el doble en memoria. Cuando conectemos la API real de aturno se
# cambia por AturnoAPI acá y nada más en todo el archivo cambia — para eso
# existe el contrato de src/aturno/base.py.
aturno = AturnoDoble()

# El grafo y la conexión a Postgres viven todo el proceso. Se arman una sola
# vez: abrir el checkpointer por mensaje agregaría latencia a cada respuesta.
_grafo = None
_saver_ctx = None


@app.on_event("startup")
async def _al_arrancar() -> None:
    global _grafo, _saver_ctx
    _configurar_logs()
    cfg = config()

    # Antes de armar el grafo: la instrumentación tiene que estar puesta
    # cuando se construyan los runnables, si no los spans salen sueltos.
    configurar_trazas()

    flujo.configurar(aturno)

    _saver_ctx = AsyncPostgresSaver.from_conn_string(cfg.database_url)
    saver = await _saver_ctx.__aenter__()
    await saver.setup()  # crea las tablas del checkpointer si faltan
    _grafo = construir_flujo(saver)

    logger.info(
        "aturno-whatsapp listo · LLM=%s · aturno=%s · firma=%s · negocios=%d · RAG=%s · trazas=%s",
        cfg.provider, cfg.aturno_modo, cfg.validar_firma, len(TENANTS),
        modelo_en_uso().split('/')[-1], "on" if trazado_activo() else "off",
    )


@app.on_event("shutdown")
async def _al_apagar() -> None:
    if _saver_ctx is not None:
        await _saver_ctx.__aexit__(None, None, None)


def _twilio() -> Client:
    cfg = config()
    return Client(cfg.twilio_account_sid, cfg.twilio_auth_token)


# ---------- Salud ----------
@app.get("/salud")
async def salud() -> dict:
    """Chequeo rápido: ¿está vivo y con qué configuración?"""
    cfg = config()
    return {
        "estado": "ok",
        "proveedor_llm": cfg.provider,
        "aturno_modo": cfg.aturno_modo,
        "firma_validada": cfg.validar_firma,
        "trazado": trazado_activo(),
    }


# ---------- El webhook ----------
@app.post("/webhook/whatsapp")
async def whatsapp(
    tareas: BackgroundTasks,
    request: Request,
    From: str = Form(...),  # noqa: N803 — Twilio manda estos nombres, en mayúscula
    To: str = Form(...),  # noqa: N803
    Body: str = Form(...),  # noqa: N803
    MessageSid: str = Form(default=""),  # noqa: N803 — lo pide el indicador
    x_twilio_signature: str = Header(default=""),
) -> PlainTextResponse:
    """Recibe un mensaje de WhatsApp, lo encola y contesta 200 al instante."""
    cfg = config()

    if cfg.validar_firma:
        await _verificar_firma(request, x_twilio_signature)

    # Validar antes de encolar: si el payload viene raro, que falle acá y no
    # dentro de una tarea en segundo plano, donde el error no se ve.
    try:
        mensaje = MensajeEntrante(de=From, para=To, texto=Body)
    except ValueError as e:
        logger.warning("Payload inesperado de Twilio: %s", e)
        raise HTTPException(status_code=400, detail="Mensaje mal formado") from e

    negocio = tenant_por_numero(mensaje.para)
    if negocio is None:
        # Alguien escribió a un número que no administramos. No es un error
        # nuestro; contestamos 200 para que Twilio no reintente.
        logger.warning("Mensaje a un número sin negocio asignado: %s", mensaje.para)
        return PlainTextResponse("", status_code=200)

    logger.info("← %s (%s): %s", mensaje.de, negocio.nombre, mensaje.texto[:60])
    tareas.add_task(_procesar_y_responder, mensaje, negocio, MessageSid)

    # Cuerpo vacío = "recibido, no contesto por acá". La respuesta sale después.
    return PlainTextResponse("", status_code=200)


async def _verificar_firma(request: Request, firma: str) -> None:
    """Comprueba que el POST venga de Twilio y no de cualquiera.

    El webhook es una URL pública: sin esto, cualquiera que la descubra puede
    inventar mensajes y, más adelante, turnos. Twilio firma cada request con el
    Auth Token, y la firma se calcula sobre la URL EXACTA que llamó — por eso
    usamos PUBLIC_URL del .env y no request.url, que detrás del túnel dice
    "localhost" y nunca coincidiría.
    """
    cfg = config()
    if not cfg.public_url:
        raise HTTPException(
            status_code=500,
            detail="Falta PUBLIC_URL en el .env; sin ella no se puede validar la firma.",
        )

    url = cfg.public_url.rstrip("/") + request.url.path
    formulario = await request.form()
    parametros = {k: str(v) for k, v in formulario.items()}

    if not RequestValidator(cfg.twilio_auth_token).validate(url, parametros, firma):
        logger.warning("Firma inválida para %s — request rechazado", url)
        raise HTTPException(status_code=403, detail="Firma de Twilio inválida")


# ---------- El trabajo de fondo ----------
def _mostrar_escribiendo(message_sid: str) -> None:
    """Prende el "escribiendo…" nativo de WhatsApp mientras el agente piensa.

    Un turno del agente puede tardar varios segundos y del otro lado no pasa
    nada: la persona no sabe si el bot la escuchó. El indicador es la señal
    barata de "te leí, dame un segundo".

    Va colgado del SID del mensaje entrante porque así identifica Twilio la
    conversación. Si falla, no pasa nada: es cosmético y nunca debe impedir
    que la respuesta salga.
    """
    if not message_sid:
        return
    try:
        _twilio().messaging.v2.typing_indicators.create(
            channel="whatsapp", message_id=message_sid
        )
    except Exception as e:  # noqa: BLE001 — cosmético, no rompe el flujo
        logger.debug("No se pudo mostrar el indicador: %s", e)


async def _procesar_y_responder(
    mensaje: MensajeEntrante, negocio: Tenant, message_sid: str = ""
) -> None:
    """Arma la respuesta y la manda. Nunca debe lanzar una excepción.

    Corre fuera del ciclo del request, así que si explota nadie se entera y la
    persona se queda esperando para siempre. Por eso el try/except amplio y el
    mensaje de disculpa: siempre es mejor contestar algo que no contestar.
    """
    _mostrar_escribiendo(message_sid)

    try:
        texto = await _componer_respuesta(mensaje, negocio)
    except Exception:  # noqa: BLE001 — el usuario merece una respuesta igual
        logger.exception("Falló al procesar el mensaje de %s", mensaje.de)
        texto = (
            "Uy, tuve un problema para procesar tu mensaje. "
            "¿Probás de nuevo en un minuto?"
        )

    _enviar(mensaje.de, negocio, texto)


async def _componer_respuesta(mensaje: MensajeEntrante, negocio: Tenant) -> str:
    """Pasa el mensaje por la máquina de estados y devuelve el texto a enviar.

    Todo lo que sale de acá lo escribió una plantilla: el flujo nunca devuelve
    texto generado por el modelo.
    """
    if _grafo is None:
        raise RuntimeError("El flujo no está inicializado")

    hilo = hilo_de(negocio.business_id, mensaje.de)

    # Si esta persona ya dio su nombre antes, el flujo saltea ese paso. El dato
    # sale del estado guardado, no de volver a preguntarlo.
    previo = await _grafo.aget_state({"configurable": {"thread_id": hilo}})
    conocido = (previo.values or {}).get("nombre")

    salida = await _grafo.ainvoke(
        {"mensaje": mensaje.texto},
        {
            "configurable": {
                "thread_id": hilo,
                "business_id": negocio.business_id,
                "nombre_negocio": negocio.nombre,
                "telefono": mensaje.de,
                "nombre_cliente": conocido,
                "calendario": calendario(),
            }
        },
    )
    return salida["respuesta"]


def _enviar(destino: str, negocio: Tenant, texto: str) -> None:
    """Manda el mensaje por la API REST de Twilio."""
    try:
        enviado = _twilio().messages.create(
            from_=f"whatsapp:{negocio.numero_whatsapp}",
            to=f"whatsapp:{destino}",
            body=texto,
        )
        logger.info("→ %s enviado (sid=%s)", destino, enviado.sid)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo enviar la respuesta a %s", destino)
