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

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from twilio.request_validator import RequestValidator
from twilio.rest import Client

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from src.agentes import flujo
from src.agentes.estados import Estado
from src.api.conversaciones import (
    Enviado,
    EventoDeConversacion,
    MensajeDelPanel,
    _secreto_valido,
    avisar_a_aturno,
    evento,
)
from src.agentes.flujo import construir_flujo, hilo_de
from src.aturno.base import ClienteAturno
from src.aturno.doble import AturnoDoble
from src.config import TENANTS, config, tenant_por_numero
from src.fechas import ahora, calendario
from src.observabilidad import configurar_trazas, trazado_activo
from src.rag.indice import modelo_en_uso
from src.schemas import MensajeEntrante, Tenant

logger = logging.getLogger("pipeline.webhook")

# Cuánto puede tardar el procesamiento antes de abandonarlo. Un turno normal
# lleva ~2 segundos; treinta es holgado para un arranque en frío y sigue muy
# por debajo de la paciencia de una persona esperando en WhatsApp.
TECHO_SEGUNDOS = 30


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

def _cliente_aturno() -> ClienteAturno:
    """Contra qué habla el bot: el backend real o el doble en memoria.

    ATURNO_MODO=api    turnos reales, en la misma agenda que la web
    ATURNO_MODO=doble  en memoria, sin red — para tests y para desarrollar

    Esta función es el único lugar del proyecto que sabe cuál de las dos está
    en uso. Todo lo demás habla con `ClienteAturno` y no nota la diferencia:
    para eso existe el contrato de src/aturno/base.py.
    """
    cfg = config()
    if cfg.aturno_modo == "api":
        from src.aturno.api import AturnoAPI

        logger.info("aturno REAL en %s", cfg.aturno_api_url)
        return AturnoAPI(cfg.aturno_api_url)
    return AturnoDoble()


aturno: ClienteAturno = _cliente_aturno()

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
class EstadoCredencial(BaseModel):
    """Si una credencial sirve, sin revelar su valor."""

    valida: bool
    detalle: str = Field(description="Qué está mal, si algo lo está.")
    largo: int = Field(description="Cuántos caracteres tiene lo cargado.")
    empieza: str = Field(description="Primeros caracteres, para identificarla.")


async def _verificar_credenciales() -> dict[str, EstadoCredencial]:
    """Prueba cada credencial contra su proveedor y reporta cuál falla.

    Existe porque diagnosticar esto a ciegas costó tres despliegues. Los
    errores que devuelven los proveedores no dicen qué variable está mal:
    Twilio contesta "invalid username" cuando el Account SID está mal, y
    "invalid" no ayuda a saber si el valor se pegó cortado, cruzado con otro
    o con un espacio al final.

    Nunca devuelve el valor: solo el largo y el prefijo, que alcanzan para
    compararlo con el original sin exponerlo en una URL pública.
    """
    cfg = config()
    r: dict[str, EstadoCredencial] = {}

    def marca(valor: str) -> dict:
        return {"largo": len(valor), "empieza": valor[:7] + "…" if valor else "(vacía)"}

    # Anthropic
    v = cfg.anthropic_api_key
    try:
        import anthropic
        await anthropic.AsyncAnthropic(api_key=v).messages.create(
            model=cfg.anthropic_modelo, max_tokens=1,
            messages=[{"role": "user", "content": "."}])
        r["anthropic"] = EstadoCredencial(valida=True, detalle="ok", **marca(v))
    except Exception as e:
        pista = "clave inválida o incompleta" if "401" in str(e) else str(e)[:70]
        r["anthropic"] = EstadoCredencial(valida=False, detalle=pista, **marca(v))

    # Twilio
    sid, tok = cfg.twilio_account_sid, cfg.twilio_auth_token
    try:
        from twilio.rest import Client
        Client(sid, tok).api.accounts(sid).fetch()
        r["twilio"] = EstadoCredencial(valida=True, detalle="ok", **marca(sid))
    except Exception as e:
        s = str(e)
        if not sid.startswith("AC"):
            pista = "el SID no empieza con AC — ¿está cruzado con el auth token?"
        elif "username" in s:
            pista = "el Account SID no es válido"
        elif "401" in s:
            pista = "el auth token no es válido"
        else:
            pista = s[:70]
        r["twilio"] = EstadoCredencial(valida=False, detalle=pista, **marca(sid))

    # Gemini (embeddings)
    try:
        from src.rag.indice import _embeddings
        _embeddings().embed_query("ok")
        r["gemini"] = EstadoCredencial(valida=True, detalle="ok", **marca(cfg.gemini_api_key))
    except Exception as e:
        r["gemini"] = EstadoCredencial(valida=False, detalle=str(e)[:70],
                                       **marca(cfg.gemini_api_key))
    return r


@app.get("/diagnostico", response_model=dict[str, EstadoCredencial])
async def diagnostico() -> dict[str, EstadoCredencial]:
    """Cuál credencial está mal cargada, sin exponer ninguna."""
    return await _verificar_credenciales()


class Ajuste(BaseModel):
    """Si una variable está puesta y qué efecto tiene. Nunca su valor."""

    puesta: bool
    efecto: str = Field(description="Qué cambia según esté o no.")


@app.get("/configuracion", response_model=dict[str, Ajuste])
async def configuracion() -> dict[str, Ajuste]:
    """Qué variables de entorno LLEGARON al servicio.

    Existe porque diagnosticar esto desde afuera es adivinar. Un servicio con
    una variable sin cargar se comporta distinto sin dar ninguna señal, y la
    única forma de saberlo era deducirlo del comportamiento — que fue
    exactamente lo que pasó tres veces seguidas con las mismas cuatro.

    Solo dice SI está puesta, nunca el valor. Un booleano no sirve para
    entrar a ningún lado.
    """
    cfg = config()
    return {
        "ATURNO_MODO": Ajuste(
            puesta=cfg.aturno_modo == "api",
            efecto=("los turnos van a la agenda real de aturno"
                    if cfg.aturno_modo == "api"
                    else "SIN ESTO: el bot reserva en memoria y el turno no "
                         "llega a ninguna agenda"),
        ),
        "ATURNO_API_URL": Ajuste(
            puesta=cfg.aturno_api_url.startswith("https://"),
            efecto=("apunta a un backend real"
                    if cfg.aturno_api_url.startswith("https://")
                    else f"SIN ESTO: apunta a «{cfg.aturno_api_url}», que desde "
                         "el servidor no existe"),
        ),
        "PANEL_URL": Ajuste(
            puesta=bool(cfg.panel_url),
            efecto=("el panel recibe cada mensaje" if cfg.panel_url
                    else "SIN ESTO: las conversaciones no aparecen en el panel"),
        ),
        "PANEL_SECRETO": Ajuste(
            puesta=bool(cfg.panel_secreto),
            efecto=("el panel puede contestar" if cfg.panel_secreto
                    else "SIN ESTO: el panel no puede tomar el control"),
        ),
        "ATURNO_WEB_URL": Ajuste(
            puesta=bool(cfg.aturno_web_url),
            efecto=("puede mandar el link a la página" if cfg.aturno_web_url
                    else "sin esto no ofrece el link, y está bien: uno roto es peor"),
        ),
        "PUBLIC_URL": Ajuste(
            puesta=bool(cfg.public_url),
            efecto=("valida la firma de Twilio" if cfg.public_url
                    else "SIN ESTO: la firma no se puede validar"),
        ),
    }


class Cupo(BaseModel):
    """Cuántos mensajes quedan antes de que Twilio empiece a rechazar."""

    cuenta: str = Field(description="'Trial' o 'Full'. En Full no hay tope.")
    tope: int | None = Field(description="Mensajes por ventana; None si no hay.")
    enviados_24h: int = Field(description="Salientes en las últimas 24 horas.")
    restantes: int | None = Field(description="Cuántos quedan; None si no hay tope.")
    turnos_posibles: int | None = Field(
        description="Reservas completas que entran con lo que queda."
    )
    detalle: str


# Una reserva de punta a punta son ocho mensajes del bot: apertura, servicios,
# staff, días, horarios, nombre, resumen y confirmación. Sirve para traducir
# "quedan 12 mensajes" a algo accionable: "entra una demo, no dos".
MENSAJES_POR_TURNO = 8


@app.get("/cupo", response_model=Cupo)
async def cupo() -> Cupo:
    """Cuánto margen queda en Twilio antes de filmar o mostrarle esto a alguien.

    Existe porque el tope se agotó en medio de una prueba y el síntoma fue el
    peor posible: el bot procesó todo bien y la persona no recibió nada. Desde
    afuera es idéntico a un bot caído.

    El límite es una ventana MÓVIL de 24 horas, no un tope que se repone a
    medianoche: los mensajes se liberan de a uno a medida que cumplen 24 horas.
    Por eso se cuenta hacia atrás desde ahora y no desde el comienzo del día.
    """
    cfg = config()
    sid = cfg.twilio_account_sid
    try:
        cliente = Client(sid, cfg.twilio_auth_token)
        tipo = cliente.api.accounts(sid).fetch().type or "Trial"

        desde = datetime.now(timezone.utc) - timedelta(hours=24)
        # limit=200 corta la paginación: pasado el tope el número exacto no
        # cambia ninguna decisión, y sin límite esto pagina la cuenta entera.
        enviados = sum(
            1
            for m in cliente.messages.list(date_sent_after=desde, limit=200)
            if (m.direction or "").startswith("outbound")
        )

        if tipo != "Trial":
            return Cupo(cuenta=tipo, tope=None, enviados_24h=enviados,
                        restantes=None, turnos_posibles=None,
                        detalle="cuenta paga: sin tope diario")

        tope = 50
        quedan = max(0, tope - enviados)
        return Cupo(
            cuenta=tipo, tope=tope, enviados_24h=enviados, restantes=quedan,
            turnos_posibles=quedan // MENSAJES_POR_TURNO,
            detalle=(
                "sin margen: Twilio va a rechazar los envíos"
                if quedan == 0
                else f"alcanza para {quedan // MENSAJES_POR_TURNO} reserva(s) completa(s)"
            ),
        )
    except Exception as e:  # noqa: BLE001 — un diagnóstico que se cae no sirve
        return Cupo(cuenta="?", tope=None, enviados_24h=-1, restantes=None,
                    turnos_posibles=None, detalle=str(e)[:100])


class Salud(BaseModel):
    """La respuesta de /salud, tipada.

    El Capstone pide validar con Pydantic las entradas Y las salidas de la API.
    Un `-> dict` compila igual pero no documenta ni valida nada: con el modelo,
    FastAPI publica el esquema en /docs y falla si el endpoint devuelve algo
    que no encaja.
    """

    estado: str = Field(description="'ok' si el servicio responde.")
    proveedor_llm: str = Field(description="Proveedor de LLM configurado.")
    embeddings: str = Field(description="Modelo de embeddings en uso.")
    aturno_modo: str = Field(description="'doble' en memoria o 'api' real.")
    numero: str = Field(default="", description="El número al que le escriben.")
    sandbox: bool = Field(
        default=False,
        description="Si es el número compartido de prueba de Twilio, que obliga "
                    "a mandar 'join <código>' antes de poder escribirle.",
    )
    firma_validada: bool = Field(description="Si se verifica la firma de Twilio.")
    trazado: bool = Field(description="Si las trazas van a Phoenix.")


@app.post("/panel/responder", response_model=Enviado)
async def responder_desde_el_panel(
    mensaje: MensajeDelPanel,
    x_panel_secret: str = Header(default=""),
) -> Enviado:
    """El dueño escribió desde el panel: sale por WhatsApp con su número.

    Y además pone la conversación EN MANOS HUMANAS. Sin eso, el bot seguiría
    contestando en paralelo y la persona recibiría dos respuestas a la vez —
    una de quien la está atendiendo y otra de la máquina, encima.
    """
    if not _secreto_valido(x_panel_secret):
        # 404 y no 403: un 403 confirma que el endpoint existe y que lo único
        # que falta es el secreto, que es justo lo que no conviene confirmarle
        # a alguien que está probando.
        raise HTTPException(status_code=404, detail="No encontrado")

    negocio = next((t for t in TENANTS.values()
                    if t.business_id == mensaje.business_id), None)
    if negocio is None:
        raise HTTPException(status_code=400, detail="Ese negocio no atiende por acá")

    await _pasar_a_manos_humanas(negocio.business_id, mensaje.telefono)

    try:
        _enviar(mensaje.telefono, negocio, mensaje.texto)
    except Exception as e:  # noqa: BLE001
        logger.exception("el panel no pudo mandar el mensaje")
        return Enviado(enviado=False, detalle=str(e)[:120], momento=ahora().isoformat())

    await avisar_a_aturno(evento(
        mensaje.business_id, mensaje.telefono, mensaje.texto, de_quien="negocio"))
    return Enviado(enviado=True, detalle="ok", momento=ahora().isoformat())


@app.post("/panel/devolver", response_model=Enviado)
async def devolver_al_bot(
    mensaje: MensajeDelPanel,
    x_panel_secret: str = Header(default=""),
) -> Enviado:
    """El negocio le devuelve la conversación al asistente.

    `texto` no se usa: el mensaje lo arma el flujo, porque tiene que incluir el
    pedido del paso en el que había quedado. Se acepta igual para que el panel
    pueda usar el mismo cuerpo en los dos endpoints.
    """
    if not _secreto_valido(x_panel_secret):
        raise HTTPException(status_code=404, detail="No encontrado")

    negocio = next((t for t in TENANTS.values()
                    if t.business_id == mensaje.business_id), None)
    if negocio is None or _grafo is None:
        raise HTTPException(status_code=400, detail="Ese negocio no atiende por acá")

    texto = await flujo.retomar(_grafo, {"configurable": {
        "thread_id": hilo_de(negocio.business_id, mensaje.telefono),
        "business_id": negocio.business_id,
        "nombre_negocio": negocio.nombre,
        "telefono": mensaje.telefono,
        "nombre_cliente": None,
        "calendario": calendario(),
    }})
    if texto is None:
        # Ya la tenía el bot. No es un error: es apretar dos veces el botón.
        return Enviado(enviado=False, detalle="ya la tenía el asistente",
                       momento=ahora().isoformat())

    _enviar(mensaje.telefono, negocio, texto)
    await avisar_a_aturno(evento(mensaje.business_id, mensaje.telefono, texto,
                                 de_quien="bot"))
    return Enviado(enviado=True, detalle="ok", momento=ahora().isoformat())


async def _pasar_a_manos_humanas(business_id: str, telefono: str) -> None:
    """Marca la conversación como atendida por una persona, sin perder nada.

    Se escribe directo en el checkpointer y no se hace pasar un mensaje por el
    grafo: el dueño contestando no es un mensaje del cliente, y meterlo por el
    flujo lo haría avanzar de paso.
    """
    if _grafo is None:
        return
    cfg = {"configurable": {"thread_id": hilo_de(business_id, telefono)}}
    try:
        actual = await _grafo.aget_state(cfg)
        vals = actual.values or {}
        if vals.get("estado") == Estado.EN_MANOS_HUMANAS.value:
            return
        await _grafo.aupdate_state(cfg, {
            "estado": Estado.EN_MANOS_HUMANAS.value,
            "estado_previo": vals.get("estado") or Estado.APERTURA.value,
            "humano_desde": ahora().isoformat(),
        })
        logger.info("%s pasó a manos del negocio desde el panel", telefono)
    except Exception:  # noqa: BLE001 — que falle esto no puede frenar el envío
        logger.warning("no se pudo marcar la conversación", exc_info=True)


@app.get("/salud", response_model=Salud)
async def salud() -> Salud:
    """Chequeo rápido: ¿está vivo y con qué configuración?"""
    cfg = config()
    return Salud(
        estado="ok",
        proveedor_llm=cfg.provider,
        embeddings=modelo_en_uso().split("/")[-1],
        aturno_modo=cfg.aturno_modo,
        numero=cfg.twilio_whatsapp_number,
        # El sandbox de Twilio es siempre este número, compartido por todos.
        # Importa decirlo: con él, nadie puede escribirle al bot sin mandar
        # antes el "join", y un negocio que no lo sabe reparte un número que
        # a sus clientes no les va a contestar.
        sandbox=cfg.twilio_whatsapp_number == "+14155238886",
        firma_validada=cfg.validar_firma,
        trazado=trazado_activo(),
    )


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
        # Con techo de tiempo. Un except no alcanza: si el procesamiento se
        # CUELGA en vez de fallar —una conexión a la base que no responde, una
        # llamada al modelo que nunca vuelve— la excepción no llega nunca y la
        # persona se queda esperando sin enterarse de nada. Pasó en producción:
        # el webhook devolvía 200 y no salía ninguna respuesta, ni siquiera un
        # error.
        texto = await asyncio.wait_for(
            _componer_respuesta(mensaje, negocio), timeout=TECHO_SEGUNDOS
        )
    except asyncio.TimeoutError:
        logger.error(
            "El procesamiento de %s superó los %ds y se abandonó",
            mensaje.de, TECHO_SEGUNDOS,
        )
        texto = (
            "Perdón, estoy tardando más de lo normal. "
            "¿Me lo mandás de nuevo?"
        )
    except Exception:  # noqa: BLE001 — el usuario merece una respuesta igual
        logger.exception("Falló al procesar el mensaje de %s", mensaje.de)
        texto = (
            "Uy, tuve un problema para procesar tu mensaje. "
            "¿Probás de nuevo en un minuto?"
        )

    # El panel ve la conversación entera, no solo las escalaciones. Un dueño
    # que solo recibe el aviso "alguien pidió una persona" tiene que adivinar
    # qué venía pasando; con los mensajes a la vista contesta sabiendo.
    #
    # Se avisa DESPUÉS de procesar y antes de enviar, para que el orden en el
    # panel sea el mismo que en el chat.
    estado_ahora = None
    if _grafo is not None:
        try:
            st = await _grafo.aget_state(
                {"configurable": {"thread_id": hilo_de(negocio.business_id, mensaje.de)}})
            estado_ahora = (st.values or {}).get("estado")
        except Exception:  # noqa: BLE001
            pass

    await avisar_a_aturno(evento(
        negocio.business_id, mensaje.de, mensaje.texto, de_quien="cliente",
        necesita_humano=estado_ahora == Estado.EN_MANOS_HUMANAS.value,
        paso=estado_ahora,
    ))

    # Un texto vacío es el bot callándose a propósito: la conversación está en
    # manos del negocio y responder ahí sería hablarle encima a quien atiende.
    if not (texto or "").strip():
        logger.info("sin respuesta para %s (conversación escalada)", mensaje.de)
        return

    _enviar(mensaje.de, negocio, texto)
    await avisar_a_aturno(evento(
        negocio.business_id, mensaje.de, texto, de_quien="bot", paso=estado_ahora))


async def _componer_respuesta(mensaje: MensajeEntrante, negocio: Tenant) -> str:
    """Pasa el mensaje por la máquina de estados y devuelve el texto a enviar.

    Todo lo que sale de acá lo escribió una plantilla: el flujo nunca devuelve
    texto generado por el modelo.
    """
    if _grafo is None:
        raise RuntimeError("El flujo no está inicializado")

    hilo = hilo_de(negocio.business_id, mensaje.de)

    # El nombre sale de aturno; el del Tenant es el respaldo para cuando el
    # backend no contesta. Un saludo con el nombre viejo es de las cosas que
    # nadie reporta y todos notan.
    try:
        nombre_negocio = await aturno.nombre_visible(negocio.business_id) or negocio.nombre
    except Exception:  # noqa: BLE001
        nombre_negocio = negocio.nombre

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
                "nombre_negocio": nombre_negocio,
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
