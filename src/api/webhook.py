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
import datetime as _dt
import logging
import time
from time import monotonic as _monotonic

import httpx
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
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
    PedidoDeReindexado,
    _secreto_valido,
    avisar_a_aturno,
    evento,
)
from src.agentes.flujo import construir_flujo, hilo_de
from src.aturno.base import ClienteAturno
from src.aturno.doble import AturnoDoble
from src.config import TENANTS, config, tenant_por_numero
from src.fechas import ahora, calendario
from src import plantillas as P
from src.observabilidad import configurar_trazas, trazado_activo
from src.gasto import GASTO
from src import metricas
from src.modelo import construir_modelo, hay_credencial
from src.rag.indice import CARPETA_DATOS, modelo_en_uso, reindexar_negocio
from src.schemas import MensajeEntrante, Tenant

logger = logging.getLogger("pipeline.webhook")

# Cuánto puede tardar el procesamiento antes de abandonarlo sale de la config
# (`techo_segundos`): un turno normal lleva ~2 segundos y treinta es holgado
# para un arranque en frío, sin pasarse de la paciencia de alguien esperando.

# Hasta dónde se lee un mensaje entrante. WhatsApp deja mandar hasta 4096
# caracteres y el esquema los rechazaba pasados de ahí, o sea que escribir de
# más dejaba a la persona sin ninguna respuesta. Se recorta y se sigue: el
# flujo vuelve a recortar a 400 antes del prompt, así que el costo ya está
# acotado y esto sólo garantiza que el mensaje entre.
LARGO_MAXIMO = 4096


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

    # La tabla de métricas, en la misma base. Si falla, se avisa y se sigue: un
    # bot que no cuenta sus conversaciones sirve; uno que no arranca, no.
    try:
        await metricas.preparar()
    except Exception as e:  # noqa: BLE001
        logger.warning("sin tabla de métricas (%s): %s", type(e).__name__, e)

    await _traer_el_conocimiento()

    # El primer chequeo del modelo, acá y no en el primer `/salud`. Cuesta una
    # llamada por despliegue y a cambio la caché nunca está vacía: todo `/salud`
    # posterior contesta al instante, sin que ninguno tenga que ser el que
    # espera. Ver `CACHE_SALUD_SEGUNDOS`.
    await _llm_responde(forzar=True)

    logger.info(
        "aturno-whatsapp listo · LLM=%s · aturno=%s · firma=%s · negocios=%d · RAG=%s · trazas=%s",
        cfg.provider, cfg.aturno_modo, cfg.validar_firma, len(TENANTS),
        modelo_en_uso().split('/')[-1], "on" if trazado_activo() else "off",
    )


async def _traer_el_conocimiento() -> None:
    """Al arrancar, vuelve a leer de aturno lo que cada negocio cargó.

    POR QUÉ HACE FALTA
    El panel empuja: cuando el negocio guarda una respuesta, aturno llama a
    `/panel/reindexar` y el bot la indexa. Eso funciona. El problema es dónde
    queda: `reindexar_negocio` escribe en `datos/` y **en Render el disco es
    efímero**. En el deploy siguiente, `arranque.sh` reconstruye el índice desde
    los `.md` que viajan en la imagen —una foto vieja, del repo— y todo lo que
    se cargó por el panel desaparece.

    El síntoma es el peor de todos, porque no parece un error: el negocio cargó
    cómo llegar, con qué colectivos y qué medios de pago acepta, lo probó, andaba
    — y una semana después el bot contesta "ese dato no lo tengo cargado" sin que
    nadie haya tocado nada.

    Con esto, aturno es la única fuente de verdad —que es lo que el proyecto ya
    dice en todos lados— y los `.md` del repo quedan como semilla de desarrollo.

    FALLA BLANDO, COMO TODO EL RESTO DEL ÍNDICE
    Si aturno no contesta, el bot arranca igual con lo que tenga: va a poder
    sacar turnos, que es lo que el negocio vende, y a las preguntas va a
    contestar que no tiene el dato. Es la misma regla que explica `arranque.sh`.
    """
    for negocio in TENANTS.values():
        try:
            markdown = await aturno.conocimiento(negocio.business_id)
        except Exception:  # noqa: BLE001 — sin conocimiento se arranca igual
            logger.warning("no se pudo traer el conocimiento de %s; sigo con lo que haya",
                           negocio.business_id, exc_info=True)
            continue

        # Nada que traer: NO se borra lo que ya está. Un negocio que todavía no
        # contestó el formulario no tiene por qué perder lo que trajo la imagen,
        # y un `conocimiento()` vacío por un error del otro lado se ve igual.
        if not (markdown or "").strip():
            logger.info("%s no tiene conocimiento cargado en aturno", negocio.business_id)
            continue

        # Si es idéntico a lo que ya está en disco, no se recalculan embeddings.
        # No es micro-optimización: el plan gratuito da 1.000 por día para TODO
        # el proyecto, y reindexar en cada arranque se los come sin necesidad.
        archivo = CARPETA_DATOS / f"{negocio.business_id}.md"
        try:
            igual = archivo.read_text(encoding="utf-8") == markdown
        except OSError:
            igual = False
        if igual and _hay_indice():
            logger.info("%s ya está indexado y sin cambios", negocio.business_id)
            continue

        try:
            cuantos = await asyncio.to_thread(
                reindexar_negocio, negocio.business_id, markdown)
            logger.info("%s indexado desde aturno: %d fragmentos",
                        negocio.business_id, cuantos)
        except Exception:  # noqa: BLE001
            logger.warning("no se pudo indexar el conocimiento de %s",
                           negocio.business_id, exc_info=True)


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

    estado: str = Field(
        description="'ok' si el servicio responde Y puede pensar; "
                    "'degradado' si está vivo pero el LLM no contesta.")
    puede_responder: bool = Field(
        default=True,
        description="Si el LLM está accesible. En false el bot recibe y no entiende.")
    detalle: str = Field(default="", description="Qué le pasa, cuando no está ok.")
    proveedor_llm: str = Field(description="Proveedor de LLM configurado.")
    embeddings: str = Field(description="Modelo de embeddings en uso.")
    aturno_modo: str = Field(description="'doble' en memoria o 'api' real.")
    numero: str = Field(default="", description="El número al que le escriben.")
    busqueda: bool = Field(
        default=False,
        description="Si el índice de preguntas está disponible. En false el bot "
                    "saca turnos igual, pero a las preguntas contesta que no "
                    "tiene el dato cargado.",
    )
    sandbox: bool = Field(
        default=False,
        description="Si es el número compartido de prueba de Twilio, que obliga "
                    "a mandar 'join <código>' antes de poder escribirle.",
    )
    firma_validada: bool = Field(description="Si se verifica la firma de Twilio.")
    trazado: bool = Field(description="Si las trazas van a Phoenix.")
    respondiendo: str = Field(
        default="",
        description="Qué proveedor contestó recién. Distinto de `proveedor_llm` "
                    "significa que el principal está caído y atiende el respaldo.")
    respaldos: str = Field(
        default="", description="A quién se recurre si el principal no contesta.")


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
        await _enviar(mensaje.telefono, negocio, mensaje.texto)
    except Exception as e:  # noqa: BLE001
        logger.exception("el panel no pudo mandar el mensaje")
        return Enviado(enviado=False, detalle=str(e)[:120], momento=ahora().isoformat())

    # El negocio contestó: deja de estar esperando, pero la conversación SIGUE
    # siendo suya. Sin esta distinción el botón de devolvérsela al bot
    # desaparecía justo después de contestar, que es cuando hace falta.
    await avisar_a_aturno(evento(
        mensaje.business_id, mensaje.telefono, mensaje.texto,
        de_quien="negocio", en_manos_humanas=True))
    return Enviado(enviado=True, detalle="ok", momento=ahora().isoformat())


@app.post("/panel/tomar", response_model=Enviado)
async def tomar_desde_el_panel(
    mensaje: MensajeDelPanel,
    x_panel_secret: str = Header(default=""),
) -> Enviado:
    """El negocio se hace cargo de la conversación, sin escribir nada todavía.

    Hasta acá la única forma de que el bot se callara era contestarle a la
    persona, y eso obliga a tener algo para decir. El caso que faltaba es el
    que más apura: el dueño ve que el asistente está por meter la pata y
    necesita frenarlo AHORA, no cuando termine de redactar. Cada segundo de esa
    demora es un mensaje más del bot.

    No sale nada por WhatsApp. Tomar el control no es un mensaje para el
    cliente: es un cambio de quién atiende, y el cliente se entera por lo que
    venga después.
    """
    if not _secreto_valido(x_panel_secret):
        raise HTTPException(status_code=404, detail="No encontrado")

    negocio = next((t for t in TENANTS.values()
                    if t.business_id == mensaje.business_id), None)
    if negocio is None:
        raise HTTPException(status_code=400, detail="Ese negocio no atiende por acá")

    await _pasar_a_manos_humanas(negocio.business_id, mensaje.telefono)

    # Queda escrito en el hilo. Sin esto, el panel mostraría al bot contestando
    # y de golpe al dueño, sin nada que explique el corte; el que lo lee tres
    # días después no tiene cómo saber quién agarró la conversación ni cuándo.
    await avisar_a_aturno(evento(
        mensaje.business_id, mensaje.telefono,
        "Tomaste el control. El asistente no responde hasta que se lo devuelvas.",
        de_quien="sistema", en_manos_humanas=True))
    return Enviado(enviado=True, detalle="ok", momento=ahora().isoformat())


@app.post("/panel/reindexar", response_model=Enviado)
async def reindexar_desde_el_panel(
    mensaje: PedidoDeReindexado,
    x_panel_secret: str = Header(default=""),
) -> Enviado:
    """El negocio contestó el formulario: el bot vuelve a leer lo que sabe.

    Hasta ahora el conocimiento vivía en un `.md` de este repo, así que cargar
    una respuesta era editar código y deployar. Ahora se carga desde el panel y
    esto es lo que lo pone en uso.

    Se reindexa SOLO a este negocio, no todo. Los embeddings del plan gratuito
    son 1.000 por día para todo el proyecto: reconstruir el índice entero cada
    vez que alguien contesta una pregunta se comería la cuota de los demás, y
    con la cuota agotada el bot no puede contestar nada de nadie.
    """
    if not _secreto_valido(x_panel_secret):
        raise HTTPException(status_code=404, detail="No encontrado")

    negocio = next((t for t in TENANTS.values()
                    if t.business_id == mensaje.business_id), None)
    if negocio is None:
        raise HTTPException(status_code=400, detail="Ese negocio no atiende por acá")

    try:
        markdown = await aturno.conocimiento(negocio.business_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("no se pudo leer el conocimiento de %s", negocio.business_id)
        return Enviado(enviado=False, detalle=str(e)[:120], momento=ahora().isoformat())

    try:
        # Bloqueante: calcula embeddings. Va a un hilo aparte para no frenar el
        # bucle mientras hay gente escribiéndole al bot.
        cuantos = await asyncio.to_thread(
            reindexar_negocio, negocio.business_id, markdown)
    except Exception as e:  # noqa: BLE001
        logger.exception("falló el reindexado de %s", negocio.business_id)
        return Enviado(enviado=False, detalle=str(e)[:120], momento=ahora().isoformat())

    # El recuperador cacheado tiene el índice abierto desde que se creó. Sin
    # tirarlo, el negocio guarda el formulario, ve que se guardó, y el bot le
    # sigue diciendo "ese dato no lo tengo cargado" hasta que se reinicie.
    flujo.olvidar_recuperador(negocio.business_id)

    logger.info("%s reindexado desde el panel: %d fragmentos",
                negocio.business_id, cuantos)
    return Enviado(enviado=True, detalle=f"{cuantos} fragmentos",
                   momento=ahora().isoformat())


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

    await _enviar(mensaje.telefono, negocio, texto)
    # en_manos_humanas=False: el bot la retomó. Es lo que apaga el botón.
    await avisar_a_aturno(evento(mensaje.business_id, mensaje.telefono, texto,
                                 de_quien="bot", en_manos_humanas=False))
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


def _hay_indice() -> bool:
    """¿Se puede buscar en el conocimiento del negocio?

    Se pregunta acá y no se asume: el índice se construye al arrancar con una
    API externa, y ese paso puede fallar sin que el resto del servicio se
    entere. Antes eso tumbaba el contenedor entero; ahora arranca igual, y
    esto es lo que hace visible que arrancó a medias.
    """
    try:
        from src.rag.indice import abrir_indice

        abrir_indice()
        return True
    except Exception:  # noqa: BLE001
        return False


# Cuánto vale una respuesta del chequeo antes de volver a preguntar.
#
# POR QUÉ HAY CACHÉ
# `/salud` no lo consulta una persona: lo pinchan dos automatismos. El cron de
# GitHub cada 10 minutos, para que Render no duerma el contenedor, y el propio
# Render, que lo tiene como `healthCheckPath` y usa la cadencia que quiere. Cada
# uno de esos pings pagaba una llamada al modelo.
#
# Medido: 0,000203 USD por llamada. Cada 10 minutos son 0,88 USD por mes; si
# Render pincha cada 30 segundos, 17,54. Contra eso, mil turnos reservados
# cuestan 4,32. O sea que el chequeo podía costar más que atender gente.
#
# Cinco minutos porque lo que esto detecta —una cuenta sin crédito, una clave
# revocada— no cambia en segundos, y quien necesite el dato fresco tiene
# `?profundo=1`. Con esto, el techo son 8.640 llamadas al mes hagan lo que hagan
# los que pinchan.
CACHE_SALUD_SEGUNDOS = 300

_salud_llm: tuple[float, tuple[bool, str, str]] | None = None


async def _llm_responde(forzar: bool = False) -> tuple[bool, str, str]:
    """¿El LLM contesta? Una llamada mínima, de un token.

    Existe porque /salud decía "ok" con el modelo caído. El servicio estaba
    vivo —contestaba HTTP— pero el clasificador fallaba en cada mensaje y caía
    en DESCONOCIDO: el bot recibía y no entendía nada. El panel del negocio
    leía ese "ok" y mostraba "conectado", así que el dueño veía todo en verde
    mientras sus clientes no recibían respuesta.

    Pasó de verdad, y por lo más tonto: se acabó el crédito de la cuenta. No es
    un error de credencial —la clave es válida— así que ningún chequeo de
    "¿está la API key?" lo agarra. Sólo lo agarra intentar.

    Un chequeo de salud que no mira lo que hace falta para funcionar es peor
    que ninguno: da confianza falsa justo cuando hay que ir a mirar.

    Prueba el proveedor CONFIGURADO y, si no contesta, los de respaldo. Antes
    preguntaba siempre por Anthropic, con el cliente de Anthropic importado a
    mano, sin importar qué dijera PROVIDER: con el bot corriendo por Gemini
    —que es lo que pasa mientras la cuenta principal no tenga crédito— este
    chequeo habría informado la salud de un proveedor que no se está usando.

    Devuelve QUIÉN contestó, porque no es lo mismo estar en pie que estar en
    pie por el respaldo: lo segundo funciona pero hay que ir a arreglarlo.
    """
    global _salud_llm

    if not forzar and _salud_llm is not None:
        cuando, respuesta = _salud_llm
        if _monotonic() - cuando < CACHE_SALUD_SEGUNDOS:
            return respuesta
        # Vencida: se devuelve lo último que se supo y se pregunta APARTE.
        #
        # Quien pide `/salud` casi siempre es Render, que corta a los 15
        # segundos y reinicia la instancia si falla 60 seguidos. Bloquear ese
        # request contra un tercero es acoplarle el liveness del servicio a que
        # Anthropic conteste rápido: un mal minuto del proveedor pasaría a
        # reiniciar el bot, que es peor que el problema que este chequeo
        # detecta.
        asyncio.create_task(_llm_responde(forzar=True))
        return respuesta

    def recordar(r: tuple[bool, str, str]) -> tuple[bool, str, str]:
        global _salud_llm
        _salud_llm = (_monotonic(), r)
        return r

    for nombre in [config().provider, *config().respaldos()]:
        if not hay_credencial(nombre):
            continue
        try:
            # `max_tokens=1`: lo único que se mira es si CONTESTA. Sin el tope,
            # el modelo respondía un párrafo entero al punto —"Hello! It seems
            # like you've sent just a period…"— y esa respuesta que nadie lee
            # era 39 de los 47 tokens del chequeo, o sea el 83% de lo que
            # costaba. Medido: 0,000203 USD por llamada contra 0,000013.
            await construir_modelo(nombre, max_tokens=1, motivo="salud").ainvoke(".")
            return recordar((True, nombre, ""))
        except Exception as e:  # noqa: BLE001
            texto = str(e)
            if "credit balance" in texto or "billing" in texto.lower():
                detalle = "sin crédito en la cuenta del modelo"
            elif "401" in texto or "authentication" in texto.lower():
                detalle = "la clave del modelo no es válida"
            elif "429" in texto:
                detalle = "el modelo está limitando por cantidad de pedidos"
            else:
                detalle = texto[:80]
            logger.warning("el proveedor %s no contesta: %s", nombre, detalle)
            ultimo = f"{nombre}: {detalle}"
    return recordar(
        (False, "", locals().get("ultimo", "no hay ningún proveedor con credencial")))


@app.get("/gasto")
async def gasto() -> dict:
    """Cuánto se le pagó hoy al proveedor del modelo, y en qué se fue.

    Existe porque un día aparecieron 5 dólares gastados sin volumen que los
    explicara, y contestar de dónde salían costó leer el código y hacer cuentas
    a mano. La causa —`/salud` llamando al modelo en cada uno de los chequeos
    que Render manda cada 5 a 10 segundos— se veía enseguida con este desglose,
    y sin él no se veía en ningún lado: Phoenix está apagado en producción.

    Público a propósito, como `/salud` y `/configuracion`: no expone ninguna
    credencial ni ningún dato de ninguna persona, y tener que autenticarse para
    mirar cuánto se gasta es la clase de fricción que hace que nadie mire.
    """
    return GASTO.resumen()


@app.get("/metricas")
async def metricas_() -> dict:
    """Cuántas conversaciones resuelve el bot solo, y dónde se cae la gente.

    La otra mitad de `/gasto`: ahí está lo que sale, acá lo que rinde. Juntos
    dan el número que va en un presupuesto —costo por turno RESUELTO— que es
    distinto del costo por conversación y siempre más alto.

    `containment` es el número que mira un negocio antes de pagar. Para un bot
    angosto y transaccional como éste, la referencia de industria es 65–85%.
    `abandono_por_paso` es el que dice QUÉ arreglar: no que algo anda mal, sino
    dónde.

    Público como `/gasto` y por lo mismo: acá no hay ningún teléfono ni ningún
    dato de ninguna persona —el hilo se guarda hasheado— y pedir credenciales
    para ver si el bot funciona es la fricción que hace que nadie mire.
    """
    siempre = await metricas.resumen()

    # El costo por turno resuelto se calcula SÓLO con la ventana de hoy, porque
    # es la única que lleva `gasto.py`. Dividir el gasto del día por las
    # conversaciones de siempre da un número que parece un costo y no lo es —y
    # que además va bajando solo, lo cual lo hace peor: parece una mejora.
    hoy = await metricas.resumen(solo_hoy=True)
    usd_hoy = GASTO.resumen().get("usd") or 0.0
    reservadas_hoy = hoy["reservadas"]

    return {
        **siempre,
        "referencia_containment": "0.65 a 0.85 para un bot transaccional",
        "hoy": {
            "conversaciones": hoy["cerradas"] + hoy["en_curso"],
            "reservadas": reservadas_hoy,
            "usd": round(usd_hoy, 5),
            # Sin turnos reservados no se inventa una división. Un costo por
            # turno calculado sobre cero turnos no es un número grande: no es
            # un número.
            "usd_por_turno_resuelto": (round(usd_hoy / reservadas_hoy, 5)
                                       if reservadas_hoy else None),
        },
    }


@app.get("/senales")
async def senales_(negocio: str | None = None) -> dict:
    """Lo que el bot no supo hacer, agrupado. El JSON; la vista es `/tablero`."""
    return await metricas.senales(negocio)


def _esc(t) -> str:
    return (str(t if t is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


_PASO_LEGIBLE = {
    "apertura": "el saludo", "esperando_servicio": "elegir el servicio",
    "esperando_staff": "elegir con quién", "esperando_dia": "elegir el día",
    "esperando_horario": "elegir el horario", "esperando_nombre": "dar el nombre",
    "esperando_confirmacion": "confirmar", "esperando_senia": "pagar la seña",
    "confirmado": "turno confirmado", "en_manos_humanas": "con una persona",
}


def _cuando(iso: str | None) -> str:
    """«hace 3 h». Un ISO no le dice nada a nadie de un vistazo."""
    if not iso:
        return ""
    try:
        falta = (ahora() - _dt.datetime.fromisoformat(iso)).total_seconds()
    except Exception:  # noqa: BLE001
        return ""
    if falta < 3600:
        return f"hace {int(falta // 60)} min"
    if falta < 86400:
        return f"hace {int(falta // 3600)} h"
    return f"hace {int(falta // 86400)} d"


# Cuántas veces tiene que pasar algo para que valga la pena mostrarlo.
#
# UNO NO ES UN DATO. «hacen depilación láser ×1» en una peluquería no es un
# agujero: es una persona que se equivocó de local, y nunca se va a arreglar
# porque no hay nada que arreglar. Mostrarlo al lado de algo que pasó nueve
# veces le roba la atención a lo que sí importa.
#
# La repetición ES la señal de relevancia. Lo que pasa una vez se guarda igual
# —está en `/senales` para quien lo quiera— pero no ocupa la pantalla.
_MINIMO_PARA_MOSTRAR = 2


def _bloque(titulo: str, ayuda: str, accion: str, filas: list[dict], vacio: str,
            tono: str = "", colapsar_desde: int = 6) -> str:
    """Un bloque del tablero: qué pasó, qué significa, y QUÉ HACER.

    Los tres, siempre. Un dato sin acción atrás es ruido con formato: quien lo
    mira asiente y no hace nada, y a la tercera vez deja de mirar el tablero.

    `colapsar_desde` existe porque estas listas crecen sin techo. Veinte frases
    que el bot no entendió empujan todo lo demás fuera de la pantalla, y lo que
    importa son las primeras — están ordenadas por frecuencia justamente para
    eso. El resto queda a un clic.
    """
    relevantes = [f for f in filas if f["veces"] >= _MINIMO_PARA_MOSTRAR]
    sueltas = len(filas) - len(relevantes)

    pie = (f'<p class="sueltas">Y {sueltas} que pasaron una sola vez. '
           f'No se muestran: lo que pasa una vez no se arregla.</p>'
           if sueltas else "")

    if not relevantes:
        return (f'<section class="bloque"><h2>{titulo}</h2>'
                f'<p class="ayuda">{ayuda}</p>'
                f'<div class="tarjeta vacia">{vacio}</div>{pie}</section>')

    top = max(f["veces"] for f in relevantes)

    def renglon(f):
        # Dos renglones y no cuatro columnas. Una fila de cuatro columnas con
        # texto libre adentro se ensancha sola hasta pasarse de la pantalla, y
        # cuando eso pasa se lleva puesto TODO el layout de la página — las
        # tarjetas de arriba quedan cortadas contra el borde. Pasó así.
        ctx = " · ".join(x for x in (
            _PASO_LEGIBLE.get(f.get("paso") or "", "") or (f.get("detalle") or ""),
            _cuando(f.get("ultima"))) if x)
        return (f'<li><div class="fila1">'
                f'<span class="veces {tono}">{f["veces"]}</span>'
                f'<span class="que">{_esc(f["texto"] or "—")}</span></div>'
                f'<div class="fila2 {tono}">'
                f'<span class="barra"><i style="width:{f["veces"] / top * 100:.0f}%"></i></span>'
                f'<span class="ctx">{_esc(ctx)}</span></div></li>')

    visibles = "".join(renglon(f) for f in relevantes[:colapsar_desde])
    ocultas = relevantes[colapsar_desde:]
    extra = (f'<details><summary>ver las otras {len(ocultas)}</summary>'
             f'<ol class="senales">{"".join(renglon(f) for f in ocultas)}</ol></details>'
             if ocultas else "")

    return (f'<section class="bloque"><h2>{titulo}</h2>'
            f'<p class="ayuda">{ayuda}</p>'
            f'<div class="tarjeta"><ol class="senales">{visibles}</ol>{extra}</div>'
            f'<p class="accion">{accion}</p>{pie}</section>')


_ESTILO = """
:root{
  --naranja:#ff5722; --naranja-claro:#ff7043;
  --violeta:#6a1b9a;
  --texto:#171717; --tenue:#737373; --borde:#e5e5e5; --papel:#fff; --fondo:#fafafa;
  --radio:1rem; --sombra:0 1px 2px rgba(0,0,0,.04),0 2px 8px rgba(0,0,0,.04);
}
*{margin:0;padding:0;box-sizing:border-box;min-width:0}
/* `min-width:0` en el reset, y no es cosmético: por defecto un item de grid o
   de flex NO se achica por debajo de su contenido, así que UNA celda con texto
   largo ensancha su fila, la fila ensancha la página, y de golpe las tarjetas
   de arriba quedan cortadas contra el borde derecho. Pasó exactamente así. */
html,body{max-width:100%;overflow-x:hidden}
body{background:var(--fondo);color:var(--texto);font-family:Inter,system-ui,sans-serif;
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
a{color:inherit}
.marco{max-width:720px;margin:0 auto;padding:26px 16px 64px}

h1{font-family:Poppins,sans-serif;font-size:24px;font-weight:700;letter-spacing:-.02em;
  overflow-wrap:anywhere}
h1 span{color:var(--naranja)}
.sub{color:var(--tenue);font-size:13px;margin-top:2px}
.volver{display:inline-block;color:var(--tenue);text-decoration:none;font-size:13px;
  font-weight:500;margin-bottom:12px}
.volver:hover{color:var(--naranja)}

/* ── El titular: un solo número, el que importa ── */
.titular{background:var(--papel);border:1px solid var(--borde);border-radius:var(--radio);
  padding:20px;margin-top:20px;box-shadow:var(--sombra)}
.titular b{font-family:Poppins,sans-serif;font-size:40px;font-weight:700;line-height:1;
  letter-spacing:-.03em;color:var(--naranja);font-variant-numeric:tabular-nums}
.titular p{color:var(--tenue);font-size:13px;margin-top:6px}
.titular .detalle{color:var(--texto);font-size:13.5px;margin-top:12px;
  padding-top:12px;border-top:1px solid var(--borde)}

/* ── Las cifras de apoyo ── */
/* El `min(...)` no es adorno: `minmax(96px,1fr)` NO se achica por debajo de
   96px, así que en una pantalla angosta cuatro columnas exigen 411px, la
   grilla desborda su caja y arrastra a toda la página — el contenido queda
   cortado contra el borde derecho. Con `min(96px,100%)` la columna cede
   primero y la grilla se reacomoda sola. */
.tarjetas{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(96px,100%),1fr));
  gap:9px;margin-top:9px}
.t{background:var(--papel);border:1px solid var(--borde);border-radius:.75rem;
  padding:12px 13px;box-shadow:var(--sombra)}
.t b{display:block;font-family:Poppins,sans-serif;font-size:20px;font-weight:600;
  line-height:1.2;letter-spacing:-.02em;font-variant-numeric:tabular-nums;
  overflow-wrap:anywhere}
.t span{color:var(--tenue);font-size:11px;display:block;margin-top:2px;line-height:1.35}

/* ── Índice de negocios ── */
.negocios{display:grid;grid-template-columns:1fr;gap:10px;margin-top:22px;list-style:none}
.negocio{display:block;background:var(--papel);border:1px solid var(--borde);
  border-radius:var(--radio);padding:16px 18px;text-decoration:none;
  box-shadow:var(--sombra);transition:border-color .16s,transform .16s}
.negocio:hover{border-color:var(--naranja-claro);transform:translateY(-1px)}
.negocio .nombre{font-family:Poppins,sans-serif;font-weight:600;font-size:16px;
  display:flex;align-items:center;justify-content:space-between;gap:12px}
.negocio .flecha{color:var(--tenue);font-weight:400}
.negocio .cifras{display:flex;flex-wrap:wrap;gap:16px 22px;margin-top:11px}
.negocio .cifras div{font-size:11.5px;color:var(--tenue)}
.negocio .cifras b{display:block;font-family:Poppins,sans-serif;font-size:18px;
  font-weight:600;color:var(--texto);font-variant-numeric:tabular-nums;line-height:1.2}
.negocio .cifras .clave b{color:var(--naranja)}
.sin-datos{color:var(--tenue);font-size:12.5px;margin-top:8px}

/* ── Secciones ── */
.rotulo{display:flex;align-items:center;gap:10px;margin:36px 0 2px;
  font-family:Poppins,sans-serif;font-size:11px;font-weight:600;letter-spacing:.1em;
  text-transform:uppercase}
.rotulo::after{content:"";flex:1;height:1px;background:var(--borde)}
.rotulo.negocio-r{color:var(--violeta)}
.rotulo.bot{color:var(--naranja)}
.bloque{margin-top:22px}
h2{font-family:Poppins,sans-serif;font-size:15px;font-weight:600;overflow-wrap:anywhere}
.ayuda{color:var(--tenue);font-size:12.5px;margin:2px 0 10px}
.accion{color:var(--texto);font-size:12.5px;margin-top:9px}
.sueltas{color:var(--tenue);font-size:11.5px;margin-top:5px}

/* ── Las listas ── */
.tarjeta{background:var(--papel);border:1px solid var(--borde);
  border-radius:var(--radio);box-shadow:var(--sombra);overflow:hidden;list-style:none}
.vacia{padding:15px;color:var(--tenue);font-size:13px}
.senales{list-style:none}
.senales li{padding:11px 15px;border-top:1px solid var(--borde)}
.senales li:first-child{border-top:0}
/* Una fila = dos renglones, y ninguno puede ensanchar la página.
   Arriba: el número y qué pasó. Abajo: la barra y el contexto. */
.fila1{display:flex;align-items:baseline;gap:10px}
.veces{font-family:Poppins,sans-serif;font-weight:700;font-size:15px;
  font-variant-numeric:tabular-nums;color:var(--violeta);flex:0 0 auto}
.veces.alerta{color:var(--naranja)}
.que{flex:1;font-size:14.5px;overflow-wrap:anywhere}
.pct{flex:0 0 auto;color:var(--tenue);font-size:12px;font-variant-numeric:tabular-nums}
.fila2{display:flex;align-items:center;gap:10px;margin:6px 0 0 calc(1.4em + 10px)}
.barra{flex:1 1 40px;height:6px;background:#f0f0f0;border-radius:999px;overflow:hidden}
.barra i{display:block;height:100%;background:var(--violeta);border-radius:999px}
.alerta ~ .fila2 .barra i,.fila2.alerta .barra i{background:var(--naranja)}
.ctx{flex:0 1 auto;color:var(--tenue);font-size:11px;overflow-wrap:anywhere;
  max-width:55%;text-align:right}
.dijo{display:block;color:var(--tenue);font-size:12.5px;margin-top:3px;
  overflow-wrap:anywhere}
.ctx b{font-weight:600}
.mal{color:var(--naranja)}
.ojo{color:var(--violeta)}
.bien{color:#3f9e6b}

details{border-top:1px solid var(--borde)}
details summary{cursor:pointer;padding:10px 15px;color:var(--tenue);font-size:12.5px;
  font-weight:500;list-style:none}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"▸ ";display:inline-block}
details[open] summary::before{content:"▾ "}
details summary:hover{color:var(--naranja)}

/* En un celular angosto las cifras van de a dos, y punto.
   `auto-fit` con `minmax` decide sola cuántas columnas entran, y por debajo de
   ~440px decidía mal: dejaba las cuatro en fila, la grilla se pasaba de la caja
   y arrastraba a toda la página — el texto quedaba cortado contra el borde.
   Medido con capturas a 360, 390, 500 y 720: a 500 entra bien, abajo no.
   Acá no hace falta que decida nada. */
@media screen and (max-width:520px){
  .tarjetas{grid-template-columns:1fr 1fr}
  .titular b{font-size:34px}
  .ctx{max-width:60%}
}
.avanzado{border:0;border-top:1px solid var(--borde);margin-top:34px;padding-top:4px}
.avanzado > summary{padding-left:0;font-family:Poppins,sans-serif;font-size:11px;
  letter-spacing:.1em;text-transform:uppercase;font-weight:600}
"""

_CABEZA = """<!doctype html><html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titulo}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Poppins:wght@500;600;700&display=swap" rel="stylesheet">
<style>{estilo}</style></head><body><div class="marco">"""


def _resto(m: dict) -> str:
    """La otra mitad del titular: qué pasó con las que NO terminaron en turno.

    Un porcentaje solo no dice nada para hacer. «67% terminaron en turno» se lee
    y se sigue de largo; «el 33% restante: 2 pasaron a una persona y 1 se fue a
    mitad» manda a mirar abajo, que es donde está el motivo.
    """
    if not m["cerradas"]:
        return "Todavía no terminó ninguna conversación."
    partes = []
    if m["escaladas"]:
        n = m["escaladas"]
        partes.append(f'{n} pasó a una persona' if n == 1 else f'{n} pasaron a una persona')
    if m["abandonadas"]:
        n = m["abandonadas"]
        partes.append(f'{n} se fue a mitad' if n == 1 else f'{n} se fueron a mitad')
    if not partes:
        return "Ninguna se perdió por el camino."
    return "De las otras, " + " y ".join(partes) + ". Abajo está por qué."


def _pct(x) -> str:
    return f"{x:.0%}" if isinstance(x, (int, float)) else "—"


async def _todos_los_negocios() -> list[dict]:
    """Los negocios configurados MÁS los que tienen datos, con sus números.

    Los configurados van aunque no tengan una sola conversación, y eso es el
    punto: un negocio que no aparece no se distingue de un negocio sin datos.
    Pasó —«¿por qué no veo el otro negocio?»— y la respuesta era que existía
    pero estaba mudo. Un tablero que esconde eso hace perder media hora.
    """
    con_datos = {n["business_id"]: n for n in await metricas.negocios()}
    nombres = {t.business_id: t.nombre for t in TENANTS.values()}

    filas = []
    for bid in {*con_datos, *nombres}:
        m = await metricas.resumen(bid)
        sen = await metricas.senales(bid)
        filas.append({
            "id": bid,
            "nombre": nombres.get(bid) or bid,
            "conversaciones": m["cerradas"] + m["en_curso"],
            "containment": m["containment"],
            "reservadas": m["reservadas"],
            "a_mirar": sum(len(v) for v in sen.values()),
        })
    # Los que tienen algo que mirar primero: es a lo que se entra.
    return sorted(filas, key=lambda f: (-f["conversaciones"], f["nombre"]))


async def _tablero_indice() -> str:
    """Todos los negocios de un vistazo. Se entra a uno para el detalle.

    Antes esto eran pestañas y sólo mostraba los que tenían datos. Un negocio
    que existía pero no había hablado con nadie simplemente no aparecía, y desde
    afuera se lee como «falta el negocio» y no como «falta el dato». Son dos
    problemas distintos y hay que poder distinguirlos sin abrir la base.
    """
    negocios = await _todos_los_negocios()
    g = GASTO.resumen()

    if not negocios:
        cuerpo = ('<div class="tarjeta vacia">Todavía no hay ningún negocio '
                  'configurado.</div>')
    else:
        cuerpo = '<div class="negocios">' + "".join(
            f'<a class="negocio" href="/tablero?negocio={_esc(n["id"])}">'
            f'<div class="nombre">{_esc(n["nombre"])}<span class="flecha">→</span></div>'
            + (f'<div class="cifras">'
               f'<div class="clave"><b>{_pct(n["containment"])}</b>resueltas sin humano</div>'
               f'<div><b>{n["reservadas"]}</b>turnos</div>'
               f'<div><b>{n["conversaciones"]}</b>conversaciones</div>'
               f'<div class="ojo"><b>{n["a_mirar"]}</b>cosas para mirar</div>'
               f'</div>'
               if n["conversaciones"] or n["a_mirar"]
               else '<p class="sin-datos">Todavía nadie le escribió. '
                    'Está configurado y esperando.</p>')
            + '</a>' for n in negocios) + '</div>'

    return (_CABEZA.format(titulo="Tablero · aturno", estilo=_ESTILO) + f"""
<h1>Tablero <span>·</span> aturno</h1>
<p class="sub">{len(negocios)} negocio(s) · US$&nbsp;{g.get("usd", 0):.3f} de modelo hoy</p>
{cuerpo}
</div></body></html>""")


@app.get("/tablero", response_class=HTMLResponse)
async def tablero(negocio: str | None = None) -> str:
    """Sin `?negocio=`, la lista de todos. Con él, el detalle de uno.

    Una sola ruta y no dos, porque son la misma pregunta a distinta altura:
    «cómo va todo» y «cómo va éste». Separarlas en dos URLs obliga a acordarse
    de cuál es cuál.

    Los datos, para mirarlos. Con la estética del panel de aturno.

    Los números existían desde `metricas.py`, pero un endpoint que devuelve JSON
    no lo mira nadie — y un dato que nadie mira es lo mismo que un dato que no
    existe. Por eso esto es una página y no una API más.

    DOS MITADES, Y LA DIVISIÓN ES EL PUNTO
    · PARA EL NEGOCIO — eso es producto. «14 personas quisieron sábado y no
      tenías lugar» es plata que se perdió, contada, y ningún competidor se la
      está dando al dueño.
    · PARA EL BOT — para quien lo mantiene. Cada línea es una tarea concreta:
      una frase que se repite es una fila que falta en una tabla de atajos.

    Público como `/gasto` y `/salud`, y por el mismo motivo: no hay acá ningún
    teléfono ni ningún dato de ninguna persona. Son conteos y frases sueltas, y
    del paso del nombre no se guarda ni el texto.
    """
    if negocio is None:
        return await _tablero_indice()

    m = await metricas.resumen(negocio)
    sen = await metricas.senales(negocio)
    g = GASTO.resumen()

    def pct(x):
        return f"{x:.0%}" if isinstance(x, (int, float)) else "—"

    # ---- El embudo ----
    #
    # Se muestra en el ORDEN DEL FLUJO, no por frecuencia. Un embudo desordenado
    # deja de ser un embudo: lo que se lee es la caída de un escalón al
    # siguiente, y eso sólo se ve si los escalones están en orden.
    pasos = await metricas.embudo(negocio)
    if pasos:
        base = max(p["llegaron"] for p in pasos) or 1

        def _fila_paso(p):
            # Dos fallas distintas, y se marcan distinto porque se arreglan
            # distinto: la CAÍDA dice que el paso está mal planteado; los
            # MENSAJES POR CONVERSACIÓN dicen que se entiende mal pero la gente
            # insiste — no se ve de ninguna otra forma y suele salir más barato.
            notas = []
            if p["caida"]:
                notas.append(f'<b class="mal">{p["caida"]:.0%} se fue acá</b>')
            if (p["mensajes_por_conversacion"] or 0) > 1.3:
                notas.append(f'<b class="ojo">{p["mensajes_por_conversacion"]} '
                             f'mensajes cada uno</b>')
            if not notas:
                notas.append('<b class="bien">limpio</b>')
            return (f'<li><div class="fila1">'
                    f'<span class="que">{_esc(_PASO_LEGIBLE.get(p["paso"], p["paso"]))}</span>'
                    f'<span class="pct">{p["llegaron"]} → {p["pasaron"]}</span></div>'
                    f'<div class="fila2">'
                    f'<span class="barra"><i style="width:'
                    f'{p["llegaron"] / base * 100:.0f}%"></i></span>'
                    f'<span class="ctx">{" · ".join(notas)}</span></div></li>')

        embudo_html = ('<ol class="senales tarjeta">'
                       + "".join(_fila_paso(p) for p in pasos) + '</ol>')
    else:
        embudo_html = ('<div class="tarjeta vacia">Todavía no hay conversaciones '
                       'para dibujar el recorrido.</div>')

    caidas = m["abandono_por_paso"] or {}
    frases = m.get("abandono_frases") or {}
    if caidas:
        top = max(caidas.values())
        filas_caidas = "".join(
            f'<li><div class="fila1"><span class="veces alerta">{n}</span>'
            f'<span class="que">{_esc(_PASO_LEGIBLE.get(p, p))}'
            # Lo último que escribieron antes de irse. El paso dice DÓNDE; esto
            # dice por qué, y el por qué es lo único que se puede arreglar.
            + ("".join(f'<em class="dijo">«{_esc(x)}»</em>' for x in frases.get(p, []))
               if frases.get(p) else "")
            + f'</span></div></li>'
            for p, n in caidas.items())
        caidas_html = f'<ol class="senales tarjeta">{filas_caidas}</ol>'
    else:
        caidas_html = '<div class="tarjeta vacia">Todavía nadie dejó una conversación a mitad.</div>'

    return (_CABEZA.format(titulo=f"Tablero · {_esc(negocio)}", estilo=_ESTILO) + f"""

<a class="volver" href="/tablero">← todos los negocios</a>
<h1>{_esc(negocio)}</h1>
<p class="sub">{m["cerradas"]} conversaciones terminadas · {m["en_curso"]} en curso</p>

<div class="titular">
  <b>{pct(m["containment"])}</b>
  <p>de las conversaciones terminaron en un turno, sin que nadie del local
  tuviera que meterse.</p>
  <p class="detalle">{_resto(m)}</p>
</div>
<div class="tarjetas">
  <div class="t"><b>{m["reservadas"]}</b><span>turnos sacados</span></div>
  <div class="t"><b>{pct(m["escalacion"])}</b><span>pasaron a una persona</span></div>
  <div class="t"><b>{pct(m["abandono"])}</b><span>se fueron a mitad</span></div>
  <div class="t"><b>{m["turnos_hasta_reservar"] or "—"}</b><span>mensajes por turno</span></div>
</div>

<p class="rotulo negocio-r">Para el negocio</p>
{_bloque("Turnos que no pudiste dar",
         "Quisieron reservar y no tenías lugar. Por día de la semana y hora, no por "
         "fecha: un negocio no abre el sábado 29, abre los sábados a las 10.",
         "→ Si un día se repite, ahí conviene abrir agenda o sumar a alguien.",
         sen["demanda_perdida"],
         "Todo lo que te pidieron estaba disponible.")}
{_bloque("Preguntas que te repiten y no tenés cargadas",
         "El bot contestó «eso no lo tengo» y no inventó nada. Pero si la misma "
         "pregunta vuelve, es una respuesta que te falta.",
         "→ Cargalas desde el panel, en Asistente de WhatsApp → Qué contesta. "
         "Se carga una vez y de ahí en más las contesta solo.",
         sen["sin_respuesta"],
         "Supo contestar todo lo que le preguntaron más de una vez.")}

<p class="rotulo bot">Dónde se traba la gente</p>
<section class="bloque"><h2>El recorrido, paso por paso</h2>
<p class="ayuda">Cuántos llegaron a cada paso y cuántos lo pasaron. La barra es
cuánta gente llegó: donde se angosta, ahí se está perdiendo.</p>
{embudo_html}
<p class="accion">→ <b class="mal">Se fue acá</b> significa que el paso está mal
planteado. <b class="ojo">Muchos mensajes</b> significa que se entiende mal pero
la gente insiste — eso suele arreglarse enseñándole dos frases.</p></section>

<section class="bloque"><h2>Dónde dejan la conversación</h2>
<p class="ayuda">En qué paso se fueron sin reservar, y lo último que escribieron
antes de irse. El paso dice dónde; la frase dice por qué.</p>
{caidas_html}</section>

{_bloque("Frases que el bot no entendió",
         "Escribieron esto y no supo qué querían. Ordenado por cuántas veces pasó.",
         "→ Las de arriba son dos líneas de código, y después las entiende gratis, "
         "sin consultarle a la IA.",
         sen["no_entendio"],
         "Entendió todo lo que le escribieron más de una vez.", tono="alerta")}

{_bloque("Intentos de romperlo",
         "Mensajes desmedidos: nadie escribe 800 caracteres para pedir un turno. "
         "El bot los recorta antes de leerlos, así que no llegan a ningún lado.",
         "→ Si se repite el mismo tipo de intento, avisale al negocio y bloqueá el "
         "número desde Twilio.",
         sen["abuso"],
         "Nadie intentó nada raro.", tono="alerta")}

<details class="avanzado"><summary>Detalle técnico</summary>
{_bloque("Respuestas que el bot frenó solo",
         "Iba a contestar algo que no estaba en lo que cargó el negocio y se frenó: "
         "salió el texto tal cual. Es la red que impide que invente.",
         "→ Sirve para una sola cosa: si la MISMA palabra frena muchas respuestas y "
         "es un sinónimo inofensivo, la red está demasiado apretada. Si no se "
         "repite, está haciendo su trabajo y no hay nada que hacer.",
         sen["guardian"],
         "No hubo que frenar ninguna respuesta.")}
</details>

</div></body></html>""")


@app.get("/salud", response_model=Salud)
async def salud(profundo: bool = False) -> Salud:
    """Chequeo rápido: ¿está vivo, y además puede hacer su trabajo?

    `?profundo=1` fuerza preguntarle al modelo aunque haya respuesta reciente.
    Sin eso, la respuesta puede venir de la caché de `_llm_responde`.
    """
    cfg = config()
    piensa, quien, detalle = await _llm_responde(forzar=profundo)
    por_respaldo = piensa and quien != cfg.provider
    return Salud(
        # "degradado" también cuando contesta el respaldo: el bot atiende, pero
        # el proveedor que el negocio eligió está caído y alguien tiene que ir a
        # verlo. Un verde acá sería el mismo silencio que ya escondió una caída.
        estado="ok" if (piensa and not por_respaldo) else "degradado",
        puede_responder=piensa,
        detalle=(f"contestando por el respaldo ({quien})" if por_respaldo else detalle),
        proveedor_llm=cfg.provider,
        respondiendo=quien,
        respaldos=", ".join(cfg.respaldos()) or "ninguno",
        embeddings=modelo_en_uso().split("/")[-1],
        aturno_modo=cfg.aturno_modo,
        numero=cfg.twilio_whatsapp_number,
        busqueda=_hay_indice(),
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
    # `Body` con default y NO obligatorio, y esto no es un detalle de tipos.
    #
    # Un audio, una foto o un sticker llegan con `Body` VACÍO. Siendo
    # obligatorio, FastAPI lo rechazaba con 422 antes de entrar acá: el webhook
    # contestaba error, no se procesaba nada, y la persona que mandó un audio
    # pidiendo turno no recibía absolutamente nada. Ni una respuesta, ni un
    # aviso, ni un rastro más allá de un 422 en los logs. Medido: tres mensajes
    # de prueba, cero respuestas.
    #
    # En Argentina una parte enorme de WhatsApp son notas de voz, así que ese
    # silencio no era un caso raro: era media clientela hablándole a una pared.
    Body: str = Form(default=""),  # noqa: N803
    NumMedia: str = Form(default="0"),  # noqa: N803 — cuántos adjuntos vinieron
    MediaContentType0: str = Form(default=""),  # noqa: N803 — de qué tipo es el primero
    MessageSid: str = Form(default=""),  # noqa: N803 — lo pide el indicador
    # Una ubicación compartida NO es un adjunto: llega con `NumMedia=0` y los
    # datos en estos dos campos. Sin leerlos, el mensaje quedaba sin texto y
    # sin adjunto, `MensajeEntrante` lo rechazaba y salía un 400 — o sea, el
    # mismo silencio absoluto que ya había pasado con las notas de voz, en el
    # gesto que hace mucha gente cuando quiere saber dónde queda el local.
    Latitude: str = Form(default=""),  # noqa: N803
    Longitude: str = Form(default=""),  # noqa: N803
    x_twilio_signature: str = Header(default=""),
) -> PlainTextResponse:
    """Recibe un mensaje de WhatsApp, lo encola y contesta 200 al instante."""
    cfg = config()

    if cfg.validar_firma:
        await _verificar_firma(request, x_twilio_signature)

    if not Body.strip():
        # No vino nada que leer. Puede ser un audio, una foto, un sticker, una
        # ubicación o algo que Twilio ni siquiera nos manda como texto — y en
        # los tres casos hay que CONTESTAR: el silencio se lee como "no me
        # dieron bola", nunca como "no me entendió".
        #
        # La rama cubre todo lo que llegue sin texto, y no sólo lo que sabemos
        # nombrar, justamente porque el modo de falla es el peor posible y no
        # deja rastro: un 400 en los logs y una persona esperando.
        #
        # Se contesta acá y no en el flujo porque no hay nada que clasificar ni
        # ningún paso que avanzar: no gasta LLM y no ensucia el estado de la
        # conversación, que sigue donde estaba.
        if _tiene_adjunto(NumMedia):
            respuesta, que = P.solo_adjunto(MediaContentType0), MediaContentType0 or "?"
        elif Latitude or Longitude:
            respuesta, que = P.solo_ubicacion(), "ubicación"
        else:
            respuesta, que = P.sin_texto(), "vacío"
        logger.info("mensaje sin texto de %s (%s)", From, que)
        try:
            # Se arma el mensaje PRIMERO y recién después se busca el negocio.
            # `From`/`To` vienen como "whatsapp:+549…" y el tenant se busca por
            # el número pelado: con el valor crudo no encontraba a nadie y el
            # adjunto volvía a quedar sin respuesta, que es justo lo que este
            # bloque vino a arreglar.
            quien = MensajeEntrante(de=From, para=To, texto="(sin texto)")
        except ValueError:
            return PlainTextResponse("", status_code=200)
        destino = tenant_por_numero(quien.para)
        if destino is not None:
            await _enviar(quien.de, destino, respuesta)
        return PlainTextResponse("", status_code=200)

    # Se recorta en el borde en vez de rechazar. El tope del esquema son 4096
    # caracteres, y superarlo devolvía un 400: otra vez silencio, ahora por
    # escribir de más. El flujo ya recorta a 400 antes del prompt, así que el
    # tope no protege de ningún costo — sólo de que el mensaje llegue.
    if len(Body) > LARGO_MAXIMO:
        logger.info("mensaje de %d caracteres recortado a %d", len(Body), LARGO_MAXIMO)
        Body = Body[:LARGO_MAXIMO]

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

    # El mismo mensaje dos veces es el mismo mensaje. Twilio reintenta cuando
    # no le contestamos rápido, y aunque acá el 200 sale al instante, un corte
    # de red en el medio alcanza para que reintente algo que ya se procesó: dos
    # llamadas al modelo y dos respuestas idénticas a la misma persona.
    if _ya_procesado(MessageSid):
        logger.info("mensaje repetido de Twilio (sid=%s): lo ignoro", MessageSid)
        return PlainTextResponse("", status_code=200)

    # Y nadie puede hacerle gastar el saldo al negocio a fuerza de escribir.
    atender, avisar = _pasa_el_limite(mensaje.de, _dt.datetime.now().timestamp())
    if not atender:
        logger.warning("%s pasó el tope de %d mensajes por minuto",
                       mensaje.de, TOPE_POR_MINUTO)
        if avisar:
            await _enviar(mensaje.de, negocio, P.demasiados_mensajes())
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


async def _paso_de(hilo: str) -> str | None:
    """En qué paso está una conversación, ahora. `None` si es nueva o falla.

    Lee del checkpointer, que es el único que lo sabe. Nunca levanta: esto
    existe para medir, y una medición que rompe una conversación es peor que no
    medir nada.
    """
    if _grafo is None:
        return None
    try:
        st = await _grafo.aget_state({"configurable": {"thread_id": hilo}})
        return (st.values or {}).get("estado")
    except Exception:  # noqa: BLE001
        return None


# ---------- El trabajo de fondo ----------
def _falta_para_que_se_vea(tardo: float, piso: float) -> float:
    """Cuánto falta esperar para que el «escribiendo…» alcance a verse.

    WhatsApp apaga el indicador en cuanto llega la respuesta. Cuando el mensaje
    se resuelve sin modelo —un número, un «sí», el 88% de los casos— la
    respuesta sale casi en cero y el indicador aparece y se va en el mismo
    instante. Visto de afuera: "el bot a veces no avisa que está escribiendo".

    Separado en una función propia para poder probar la cuenta sin dormir de
    verdad: un test que espera un segundo tarda un segundo y falla el día que la
    máquina va lenta.
    """
    return max(0.0, (piso or 0.0) - tardo)


async def _mostrar_escribiendo(message_sid: str) -> None:
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
    cfg = config()
    try:
        # Por HTTP y no por el SDK: `messaging.v2.typing_indicators` no existe
        # en la librería (probado con twilio 9.11) y la llamada tiraba
        # AttributeError. Como el error se tragaba en un `except` amplio y se
        # logueaba en `debug` —que no se muestra—, el indicador NUNCA funcionó
        # y nada lo delataba. El endpoint real es v3.
        #
        # Cliente ASÍNCRONO: la versión sincrónica bloqueaba el bucle hasta 4
        # segundos, y con él a todas las demás conversaciones en curso. Un
        # indicador es cosmético; frenar el servicio entero para dibujarlo, no.
        async with httpx.AsyncClient(timeout=4) as http:
            r = await http.post(
                "https://messaging.twilio.com/v3/Indicators/Typing.json",
                auth=(cfg.twilio_account_sid, cfg.twilio_auth_token),
                json={"channel": "WHATSAPP", "messageId": message_sid},
            )
        if r.status_code >= 400:
            # INFO y no debug: un indicador que no sale es cosmético, pero
            # tiene que poder verse en los logs. La versión anterior fallaba
            # en silencio desde el primer día.
            logger.info("el indicador no salió (%d): %s", r.status_code, r.text[:100])
    except Exception as e:  # noqa: BLE001 — cosmético, no rompe el flujo
        logger.info("no se pudo mostrar el indicador: %s", e)


def _tiene_adjunto(num_media: str) -> bool:
    """Twilio manda NumMedia como texto, y a veces no lo manda."""
    try:
        return int(num_media or "0") > 0
    except ValueError:
        return False


# Los últimos mensajes que ya se procesaron, para no contestarlos dos veces.
#
# En memoria y acotado: alcanza para lo que tiene que cubrir —un reintento de
# Twilio llega en segundos— y no necesita ni base ni limpieza. Con varias
# instancias cada una tiene el suyo, así que esto NO garantiza exactamente una
# vez a nivel sistema; achica una ventana, no la cierra. La garantía de verdad
# sería idempotencia por `MessageSid` en el checkpointer, y hoy no hace falta.
_VISTOS_MAXIMO = 500
_vistos: dict[str, None] = {}


def _ya_procesado(message_sid: str) -> bool:
    """¿Este mensaje ya se atendió? Lo marca de paso.

    Sin SID no se puede saber, y ahí se prefiere contestar de más: perder un
    mensaje es peor que mandar uno repetido.
    """
    if not message_sid:
        return False
    if message_sid in _vistos:
        return True
    _vistos[message_sid] = None
    while len(_vistos) > _VISTOS_MAXIMO:
        # El más viejo primero: los dict de Python conservan el orden de
        # inserción, así que esto es una cola sin tener que importar una.
        _vistos.pop(next(iter(_vistos)))
    return False


# Cuántos mensajes se le atienden a un mismo teléfono por minuto.
#
# Nada impedía que alguien mandara cien mensajes seguidos, y cada uno cuesta una
# llamada al modelo y un mensaje saliente de Twilio: o sea que un desconocido
# podía gastarle el saldo al negocio y, de paso, el cupo diario con el que sus
# clientes reales tendrían que ser atendidos.
#
# Doce por minuto es holgado para una persona escribiendo —nadie manda uno cada
# cinco segundos sosteniéndolo un minuto— y corta en seco a un script.
TOPE_POR_MINUTO = 12
VENTANA_SEGUNDOS = 60

_recientes: dict[str, list[float]] = {}


def _pasa_el_limite(telefono: str, ahora_seg: float) -> tuple[bool, bool]:
    """¿Se le atiende este mensaje? Y si no, ¿hay que avisarle?

    Devuelve `(atender, avisar)`. El aviso sale UNA sola vez por ventana: si
    cada mensaje rechazado contestara, el límite no serviría de nada —seguiría
    saliendo un mensaje de Twilio por cada uno— y quien esté golpeando la
    puerta recibiría cien respuestas en vez de cien turnos.

    Callarse del todo tampoco sirve: alguien que escribió rápido de nervioso
    tiene que enterarse de por qué no le contestan, o cree que el bot murió.
    """
    marcas = [t for t in _recientes.get(telefono, []) if ahora_seg - t < VENTANA_SEGUNDOS]
    _recientes[telefono] = marcas
    if len(marcas) < TOPE_POR_MINUTO:
        marcas.append(ahora_seg)
        return True, False
    # Se registra igual para que la ventana corra desde el último intento: el
    # que sigue insistiendo no se gana un lugar por esperar a que expire el
    # primero de la tanda.
    marcas.append(ahora_seg)
    return False, len(marcas) == TOPE_POR_MINUTO + 1


# Un candado por conversación, para que dos mensajes de la misma persona no se
# procesen encima.
#
# Pasa todo el tiempo en WhatsApp: alguien manda "hola" y "quiero un turno" con
# un segundo de diferencia. Son dos tareas de fondo sobre el MISMO hilo del
# checkpointer, las dos leen el estado anterior y las dos escriben — así que el
# segundo mensaje se contesta como si el primero no hubiera existido, y el
# estado que queda guardado es el de la que terminó última.
#
# Serializar por hilo y no globalmente: dos personas distintas no se estorban.
_candados: dict[str, asyncio.Lock] = {}


def _candado_de(hilo: str) -> asyncio.Lock:
    """El candado de esta conversación. Se crea la primera vez y se reusa.

    No se limpian, igual que en `aturno/api.py`: son objetos chicos, uno por
    conversación viva en este proceso, y borrarlos mientras alguien espera es
    justo la forma de que dos coroutines terminen con candados distintos.
    """
    return _candados.setdefault(hilo, asyncio.Lock())


def _salidas_de_emergencia(negocio: Tenant) -> str | None:
    """El link a la página del negocio, o None si no hay con qué armarlo.

    Devuelve None y no una cadena vacía a propósito: las plantillas deciden qué
    decir según haya link o no, y un link roto es peor que ninguno —la persona
    lo abre, ve un 404 y concluye que el negocio no funciona—. Es la misma
    regla que ya aplica `link_web`.

    OJO: hoy en producción `ATURNO_WEB_URL` no está puesta, así que esto
    devuelve None y los mensajes de demora ofrecen sólo la salida humana. Se ve
    en GET /configuracion.
    """
    # `slug` y NO `business_id`: la página pública de aturno es /<slug>. El
    # business_id es el uid de Firebase y ahí no hay ninguna página — el link
    # daría 404, que es justo lo que esta función existe para evitar.
    base = (config().aturno_web_url or "").rstrip("/")
    return f"{base}/{negocio.slug}" if base else None


async def _procesar_y_responder(
    mensaje: MensajeEntrante, negocio: Tenant, message_sid: str = ""
) -> None:
    """Arma la respuesta y la manda. Nunca debe lanzar una excepción.

    Corre fuera del ciclo del request, así que si explota nadie se entera y la
    persona se queda esperando para siempre. Por eso el try/except amplio y el
    mensaje de disculpa: siempre es mejor contestar algo que no contestar.

    Todo el cuerpo va bajo el candado de ESTA conversación. Dos mensajes
    seguidos de la misma persona —lo más común del mundo en WhatsApp: "hola" y
    después "quiero un turno"— llegaban como dos tareas de fondo en paralelo
    sobre el mismo hilo del checkpointer: las dos leían el estado anterior, las
    dos escribían, y la segunda se contestaba como si la primera no hubiera
    existido. El candado no demora nada cuando no hay concurrencia y ordena la
    conversación cuando la hay.
    """
    async with _candado_de(hilo_de(negocio.business_id, mensaje.de)):
        await _procesar_bajo_candado(mensaje, negocio, message_sid)


async def _procesar_bajo_candado(
    mensaje: MensajeEntrante, negocio: Tenant, message_sid: str
) -> None:
    """El trabajo de verdad. Separado sólo para que el candado se lea de un vistazo."""
    cfg = config()
    codigo_senia = None
    datos_senia: dict = {}
    empezo = time.monotonic()

    # El paso ANTES de tocar nada. Es la mitad del embudo, y es la mitad que se
    # pierde para siempre si no se lee ahora: dentro de dos líneas la
    # conversación ya avanzó y no hay forma de saber de dónde venía.
    hilo_actual = hilo_de(negocio.business_id, mensaje.de)
    paso_antes = await _paso_de(hilo_actual)

    await _mostrar_escribiendo(message_sid)
    salidas = _salidas_de_emergencia(negocio)

    async def _avisar_demora() -> None:
        """Manda la señal de vida a los `aviso_segundos` y no toca nada más.

        Va como tarea aparte y NO como un `wait_for` más corto, y esa es toda
        la diferencia: cortar a los diez segundos tiraría a la basura trabajo
        que iba a terminar bien a los veinte —el backend de aturno arranca en
        frío y tarda eso— y la persona se quedaría sin la respuesta que ya casi
        estaba. Acá se avisa y se sigue: el mensaje real llega igual, después.
        """
        try:
            await asyncio.sleep(cfg.aviso_segundos)
            logger.info("aviso de demora a %s (%ds)", mensaje.de, cfg.aviso_segundos)
            await _enviar(mensaje.de, negocio, P.demorado(negocio.nombre, salidas))
        except asyncio.CancelledError:
            pass                      # llegó a tiempo: no hay nada que avisar
        except Exception:  # noqa: BLE001 — un aviso que falla no rompe la respuesta
            logger.warning("no se pudo avisar la demora a %s", mensaje.de, exc_info=True)

    aviso = asyncio.create_task(_avisar_demora())
    try:
        # Con techo de tiempo. Un except no alcanza: si el procesamiento se
        # CUELGA en vez de fallar —una conexión a la base que no responde, una
        # llamada al modelo que nunca vuelve— la excepción no llega nunca y la
        # persona se queda esperando sin enterarse de nada. Pasó en producción:
        # el webhook devolvía 200 y no salía ninguna respuesta, ni siquiera un
        # error.
        texto = await asyncio.wait_for(
            _componer_respuesta(mensaje, negocio), timeout=cfg.techo_segundos
        )
    except asyncio.TimeoutError:
        logger.error(
            "El procesamiento de %s superó los %ds y se abandonó",
            mensaje.de, cfg.techo_segundos,
        )
        # Antes decía "¿me lo mandás de nuevo?", que le devuelve el trabajo a
        # la persona: reintentar contra algo que acaba de fallar es lo último
        # que quiere hacer, y probablemente falle otra vez.
        texto = P.no_pudo_contestar(negocio.nombre, salidas)
    except Exception:  # noqa: BLE001 — el usuario merece una respuesta igual
        logger.exception("Falló al procesar el mensaje de %s", mensaje.de)
        texto = P.no_pudo_contestar(negocio.nombre, salidas)
    finally:
        # Se cancela SIEMPRE, incluso cuando hubo error: si la respuesta ya
        # salió —aunque sea la de disculpa— avisar después que "está tardando"
        # es peor que no avisar nada.
        aviso.cancel()

    # El panel ve la conversación entera, no solo las escalaciones. Un dueño
    # que solo recibe el aviso "alguien pidió una persona" tiene que adivinar
    # qué venía pasando; con los mensajes a la vista contesta sabiendo.
    #
    # Se avisa DESPUÉS de procesar y antes de enviar, para que el orden en el
    # panel sea el mismo que en el chat.
    estado_ahora = None
    nombre_dado = None
    if _grafo is not None:
        try:
            st = await _grafo.aget_state(
                {"configurable": {"thread_id": hilo_de(negocio.business_id, mensaje.de)}})
            estado_ahora = (st.values or {}).get("estado")
            # Sale de la misma lectura que ya se hacía: pedirlo aparte sería
            # una consulta más por mensaje para un dato que ya está en la mano.
            nombre_dado = (st.values or {}).get("nombre")
            datos_senia = (st.values or {}).get("_datos") or {}
            codigo_senia = (st.values or {}).get("codigo_pendiente")
        except Exception:  # noqa: BLE001
            pass

    en_manos = estado_ahora == Estado.EN_MANOS_HUMANAS.value
    await avisar_a_aturno(evento(
        negocio.business_id, mensaje.de, mensaje.texto, de_quien="cliente",
        necesita_humano=en_manos, en_manos_humanas=en_manos, paso=estado_ahora,
        nombre=nombre_dado,
    ))

    # Un texto vacío es el bot callándose a propósito: la conversación está en
    # manos del negocio y responder ahí sería hablarle encima a quien atiende.
    if not (texto or "").strip():
        logger.info("sin respuesta para %s (conversación escalada)", mensaje.de)
        return

    # El evento del mensaje: de qué paso venía, a cuál fue, y si avanzó.
    #
    # Va acá y no antes porque recién ahora se sabe en qué paso quedó. Con estos
    # dos datos el embudo sale de una consulta —cuántos llegaron a cada paso y
    # cuántos lo pasaron— y sin ellos no sale de ninguna.
    #
    # En tarea de fondo: escribir una métrica no puede demorar una respuesta ni
    # tumbarla. `evento` se traga sus propios errores.
    asyncio.create_task(metricas.evento(
        hilo_de(negocio.business_id, mensaje.de), negocio.business_id,
        paso_antes=paso_antes or "apertura",
        paso_despues=estado_ahora or paso_antes or "apertura",
        avanzo=bool(estado_ahora and estado_ahora != paso_antes),
        demoro_ms=int((time.monotonic() - empezo) * 1000),
        # El texto SÓLO cuando no avanzó: lo que el bot entendió bien no hay
        # que arreglarlo, y son mensajes de personas.
        texto=None if (estado_ahora and estado_ahora != paso_antes) else mensaje.texto))

    # Que el "escribiendo…" alcance a verse. Ver `_falta_para_que_se_vea`: sin
    # esto, los mensajes que se resuelven sin modelo contestan tan rápido que el
    # indicador nunca llega a dibujarse.
    espera = _falta_para_que_se_vea(time.monotonic() - empezo, cfg.escribiendo_segundos)
    if espera:
        await asyncio.sleep(espera)

    await _enviar(mensaje.de, negocio, texto)
    await avisar_a_aturno(evento(
        negocio.business_id, mensaje.de, texto, de_quien="bot", paso=estado_ahora,
        nombre=nombre_dado))

    # Recién acá se larga la vigilancia de la seña: DESPUÉS de que el link haya
    # salido de verdad. Lanzarla antes dejaría corriendo un vigilante para un
    # mensaje que no llegó, y la persona recibiría "se venció el tiempo para
    # pagar" sin haber recibido nunca dónde pagar.
    #
    # Va como tarea suelta porque dura minutos y esto tiene que devolver ya: el
    # candado de la conversación se libera al salir, así que la persona puede
    # seguir escribiendo mientras el vigilante espera.
    if estado_ahora == Estado.ESPERANDO_SENIA.value and codigo_senia:
        asyncio.create_task(_vigilar_senia(
            negocio, mensaje.de, codigo_senia,
            datos_senia.get("minutos") or 15, datos_senia))


# Cada cuánto se le pregunta a aturno si entró la seña.
#
# Veinte segundos: lo suficientemente seguido como para que el "listo,
# confirmado" llegue mientras la persona todavía tiene el teléfono en la mano
# —acaba de pagar— y lo suficientemente espaciado como para no golpear el
# endpoint público, que además tiene su propio limitador por código.
ESPERA_ENTRE_CONSULTAS = 20


async def _vigilar_senia(negocio: Tenant, telefono: str, codigo: str,
                         minutos: int, datos: dict) -> None:
    """Espera a que entre el pago y avisa. O avisa que se venció.

    POR QUÉ HACE FALTA
    Mercado Pago le confirma el pago a aturno por webhook, no al bot. Del lado
    del chat, lo último que la persona leyó fue "te falta pagar la seña": paga,
    cierra la pestaña, y no vuelve a saber nada. Quien duda si el turno salió
    llama al local o reserva de nuevo — las dos cosas que el bot vino a evitar.

    POR QUÉ CONSULTANDO Y NO ESPERANDO UN AVISO
    Lo correcto a la larga es que aturno avise cuando confirma el pago, y para
    eso hay que agregar un endpoint acá y una llamada allá. Consultar alcanza
    para lo que dura esto —quince minutos, un puñado de consultas— y no pide
    tocar los dos repos a la vez. Si el volumen crece, el reemplazo es este
    archivo y nada más.

    NUNCA LANZA, y el silencio tiene una regla: sólo se avisa el vencimiento
    cuando aturno CONTESTÓ que sigue esperando. Si la consulta falla no se dice
    nada, porque "se venció y solté el horario" es una frase que no se le puede
    decir por error a alguien que pagó.
    """
    limite = minutos or 15
    intentos = max(1, (limite * 60) // ESPERA_ENTRE_CONSULTAS)
    ultima = None
    try:
        for _ in range(intentos):
            await asyncio.sleep(ESPERA_ENTRE_CONSULTAS)
            ultima = await aturno.senia_pagada(negocio.business_id, codigo)
            if ultima is True:
                logger.info("entró la seña de %s (%s)", telefono, codigo)
                texto = P.senia_confirmada(
                    datos.get("servicio") or "Tu turno", datos.get("profesional"),
                    _dt.date.fromisoformat(datos["fecha"]),
                    _dt.datetime.strptime(datos["hora"], "%H:%M").time(),
                    codigo,
                )
                await _enviar(telefono, negocio, texto)
                await avisar_a_aturno(evento(negocio.business_id, telefono, texto,
                                             de_quien="bot"))
                await _marcar_confirmado(negocio.business_id, telefono)
                return

        if ultima is False:
            # Contestó, y contestó que sigue esperando: recién ahí se puede
            # decir que se venció. Con `None` no se dice nada.
            logger.info("venció la seña de %s (%s)", telefono, codigo)
            texto = P.senia_vencida()
            await _enviar(telefono, negocio, texto)
            await avisar_a_aturno(evento(negocio.business_id, telefono, texto,
                                         de_quien="bot"))
            await _marcar_confirmado(negocio.business_id, telefono, pagada=False)
        else:
            logger.warning("no se pudo saber si %s pagó la seña %s: no aviso nada",
                           telefono, codigo)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — vigilar no puede tumbar nada
        logger.exception("falló la vigilancia de la seña de %s", telefono)


async def _marcar_confirmado(business_id: str, telefono: str, pagada: bool = True) -> None:
    """Saca la conversación de `esperando_senia` una vez resuelta.

    Se escribe directo en el checkpointer y no se hace pasar un mensaje por el
    grafo, por lo mismo que `_pasar_a_manos_humanas`: acá no habló nadie. Sin
    esto, la conversación queda esperando un pago que ya entró, y el próximo
    "hola" recibe "sigo esperando el pago".
    """
    if _grafo is None:
        return
    cfg = {"configurable": {"thread_id": hilo_de(business_id, telefono)}}
    try:
        await _grafo.aupdate_state(cfg, {
            "estado": (Estado.CONFIRMADO if pagada else Estado.APERTURA).value,
            "codigo_pendiente": None,
            # También el turno guardado: si quedara, el mensaje siguiente
            # volvería a consultar una seña que ya se resolvió.
            "turno_pendiente": None,
        })
    except Exception:  # noqa: BLE001
        logger.warning("no se pudo cerrar la espera de la seña", exc_info=True)


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
    # La conversación queda contada. Va DESPUÉS del `ainvoke` y antes del
    # `return` porque acá está todo junto: el hilo, el negocio y el estado en
    # que quedó. `registrar` se traga sus propios errores —regla del repo— así
    # que esta línea no puede impedir que la persona reciba su respuesta.
    estado = salida.get("estado") or ""
    await metricas.registrar(
        hilo, negocio.business_id, estado,
        desenlace=_DESENLACE.get(estado), mensaje=mensaje.texto)
    # El denominador: cuántos mensajes pasó cada paso. Sin esto, «24 tropiezos
    # eligiendo el servicio» no se puede leer — no se sabe si son 24 de 30 o de
    # 900. Se cuenta el paso en el que QUEDÓ, que es el que se le está pidiendo.
    await metricas.contar_paso(negocio.business_id, estado)

    return salida["respuesta"]


# Los dos únicos finales que cuentan como conversación cerrada por el bot. El
# resto —incluido el abandono— no se escribe: se deduce al leer, mirando cuánto
# hace que la conversación no dice nada. Ver `metricas.resumen`.
_DESENLACE = {
    Estado.CONFIRMADO.value: "reservado",
    Estado.EN_MANOS_HUMANAS.value: "escalado",
}


async def _enviar(destino: str, negocio: Tenant, texto: str) -> None:
    """Manda el mensaje por la API REST de Twilio, o lo imprime.

    Con `TWILIO_MODO=consola` no sale nada hacia afuera: se imprime lo que se
    habría mandado, con la hora, y se sigue. Sirve para probar el producto
    entero sin gastar del tope de 50 mensajes diarios de la cuenta trial —y
    para VER el orden y el tiempo en que salen, que es justo lo que no se puede
    revisar leyendo el código.

    El envío va a un hilo aparte porque el SDK de Twilio es sincrónico: llamarlo
    derecho desde acá frenaba el bucle de eventos lo que tardara la llamada, y
    con varias conversaciones a la vez las respuestas se hacían fila detrás de
    una sola. Es el mismo motivo por el que el reindexado usa `to_thread`.
    """
    if config().twilio_modo == "consola":
        marca = _dt.datetime.now().strftime("%H:%M:%S")
        print(f"\n──── [{marca}] → {destino} ──────────────────────────")
        print(texto)
        print("─" * 58, flush=True)
        logger.info("→ %s (modo consola, no se envió)", destino)
        return
    try:
        enviado = await asyncio.to_thread(
            lambda: _twilio().messages.create(
                from_=f"whatsapp:{negocio.numero_whatsapp}",
                to=f"whatsapp:{destino}",
                body=texto,
            )
        )
        logger.info("→ %s enviado (sid=%s)", destino, enviado.sid)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo enviar la respuesta a %s", destino)
