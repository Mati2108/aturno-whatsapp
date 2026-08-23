"""
config.py — Toda la configuración, validada y en un solo lugar.

El Capstone pide cero hardcoding: nombres de modelo, URLs y credenciales salen
de variables de entorno. Pydantic las valida al arrancar, así un `.env` mal
puesto falla en el import y no tres capas adentro, en medio de un turno.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from src.schemas import Tenant

RAIZ = Path(__file__).resolve().parent.parent


class Config(BaseSettings):
    """Se completa desde el .env; los defaults son para desarrollo."""

    model_config = SettingsConfigDict(
        env_file=RAIZ / ".env",  # ruta absoluta: no depende de desde dónde corras
        extra="ignore",
    )

    # ---- LLM ----
    # Ningún nombre de modelo hardcodeado en el código: todos salen de acá.
    provider: str = "ollama"
    ollama_modelo: str = "qwen3:8b"
    anthropic_modelo: str = "claude-haiku-4-5"
    openai_modelo: str = "gpt-4o-mini"
    gemini_modelo: str = "gemini-3.1-flash-lite"

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""

    # A quién recurrir si el proveedor principal no contesta. Lista separada por
    # comas, en orden; vacío apaga el respaldo.
    #
    # POR QUÉ EXISTE
    # Pasó: se acabó el crédito de la cuenta de Anthropic y el bot siguió
    # respondiendo HTTP, pero el clasificador fallaba en CADA mensaje y caía en
    # DESCONOCIDO. O sea que el bot recibía y no entendía nada, y como el paso
    # del nombre depende del modelo, ningún cliente nuevo podía sacar un turno.
    # No es un error de credencial —la clave es válida— así que ningún chequeo
    # de "¿está la API key?" lo agarra.
    #
    # POR QUÉ GEMINI Y NO OTRO
    # Lo que hay que cubrir es que se corte la facturación, y dos proveedores
    # pagados con la misma tarjeta se caen juntos: el respaldo tiene que ser
    # otra cuenta. Gemini además ya es obligatorio para los embeddings, así que
    # su clave está cargada por construcción y el respaldo no depende de que
    # alguien se acuerde de configurar algo.
    #
    # Un proveedor sin clave se saltea en silencio: declararlo acá no alcanza
    # para que exista, y un respaldo que no puede dispararse es peor que
    # ninguno, porque parece cobertura.
    provider_respaldo: str = "gemini"

    def respaldos(self) -> list[str]:
        """Los proveedores de respaldo, en orden y sin el principal."""
        pedidos = [p.strip() for p in self.provider_respaldo.split(",") if p.strip()]
        return [p for p in pedidos if p != self.provider]

    # Cuánto se puede gastar por día en el modelo antes de dejar de llamarlo.
    #
    # POR QUÉ EXISTE
    # Nada impedía que un bug, un bucle o un endpoint mal pensado se comieran el
    # crédito entero en una noche. Pasó en chico y costó plata descubrirlo:
    # `/salud` llamaba al modelo y Render lo pincha cada 5 a 10 segundos, o sea
    # hasta 17.280 veces por día, corriendo aunque no escriba nadie.
    #
    # QUÉ PASA CUANDO SE TOCA, QUE ES LO IMPORTANTE
    # El bot NO se cae: el clasificador devuelve DESCONOCIDO y el resto del flujo
    # sigue igual. Hoy el 88% de los mensajes se resuelve sin modelo, así que
    # quien contesta con números saca su turno lo mismo. Lo que se pierde es
    # entender el texto libre.
    #
    # Degradar es la falla correcta acá. La alternativa —seguir gastando— ya se
    # probó sola: cuando se acabó el crédito, el clasificador falló en TODOS los
    # mensajes y ningún cliente nuevo pudo sacar turno, porque el paso del nombre
    # dependía del modelo. Un bot que entiende menos atiende; uno sin crédito, no.
    #
    # Tres dólares es holgado a propósito: el gasto normal medido es de centavos
    # por día. Este número no está para ahorrar, está para que una fuga tenga
    # techo. En 0 se apaga el tope.
    tope_diario_usd: float = 3.0

    # ---- Persistencia ----
    # El checkpointer de LangGraph. Postgres y no SQLite desde el arranque:
    # SQLite bloquea con escrituras concurrentes y no sirve con más de una
    # instancia, que es exactamente lo que pasa al desplegar.
    database_url: str = "postgresql://localhost:5432/aturno_whatsapp"

    # ---- Twilio ----
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = "+14155238886"

    # La URL pública del túnel. Se necesita para validar la firma de Twilio:
    # la firma se calcula sobre la URL EXACTA que Twilio llamó, y detrás de un
    # túnel el server ve "localhost", no la URL real.
    public_url: str = ""

    # Cuándo avisar que está tardando, y cuándo darse por vencido. Van acá y no
    # como constantes fijas para poder bajarlos al probar: con el aviso en 1
    # segundo se ve el mensaje de demora en cada turno, sin tener que esperar a
    # que el backend arranque en frío para reproducirlo.
    #
    # El default de 10 sale de los límites de respuesta de Nielsen: es el techo
    # para que alguien mantenga la atención puesta en un diálogo.
    # Cuánto tiene que durar, como mínimo, el "escribiendo…" antes de que salga
    # la respuesta. No es una demora artificial por capricho: el 88% de los
    # mensajes se resuelven sin modelo y contestan al instante, así que el
    # indicador aparece y desaparece antes de que nadie lo vea — y desde afuera
    # se lee como que el bot "a veces no avisa".
    #
    # En WhatsApp esto no cuesta nada: es un canal asincrónico y la tolerancia a
    # la demora es altísima. Lo que sí cuesta es contestar en cero: una
    # respuesta instantánea a una frase escrita a mano se lee como una máquina.
    #
    # En 0 se apaga.
    escribiendo_segundos: float = 1.2

    aviso_segundos: int = 10
    techo_segundos: int = 30

    # Apagar la validación de firma solo para probar con curl desde la máquina.
    # En producción va siempre en True: el webhook es una URL pública y sin
    # firma cualquiera puede postear turnos falsos.
    validar_firma: bool = True

    # ---- Entrega de los mensajes ----
    # "api" (Twilio de verdad) | "consola" (los imprime y no manda nada).
    #
    # Mismo patrón que `aturno_modo` de más abajo, y por el mismo motivo: se
    # puede probar el producto ENTERO sin depender del servicio externo. Acá
    # además importa la plata: la cuenta trial de Twilio tiene un tope de 50
    # mensajes salientes cada 24 horas, y probar a mano lo quema en una tarde
    # —después el bot recibe, piensa y no puede contestar, que se ve igual que
    # estar roto—.
    #
    # En "consola" se prueba todo lo que pasa ANTES de Twilio, que es todo lo
    # que este producto hace: entender, decidir, consultar aturno, redactar, y
    # el orden y el tiempo en que salen los mensajes. Lo único que no se prueba
    # es que Twilio entregue.
    twilio_modo: str = "api"

    # ---- Canal de WhatsApp ----
    # "twilio" (el sandbox de hoy) | "meta" (Cloud API, un número por negocio)
    #
    # Mismo patrón que `aturno_modo` y `twilio_modo`: una variable decide con
    # quién se habla y el resto del sistema no se entera. Ver src/canal/base.py.
    canal: str = "twilio"

    # El secreto de la app de Meta, con el que se firma cada webhook. NO es el
    # token de un negocio: ese es por negocio y sale del Embedded Signup.
    #
    # Sin esto, el canal de Meta rechaza todos los webhooks a propósito: un
    # webhook público sin firma es una puerta para que cualquiera invente
    # turnos, y fallar cerrado es la única forma de que se note.
    meta_app_secret: str = ""

    # Lo que Meta manda en el `hub.verify_token` al dar de alta el webhook. Lo
    # elegís vos: sólo tiene que coincidir entre la consola de Meta y esto.
    meta_verify_token: str = ""

    # La plantilla aprobada para hablarle a alguien fuera de la ventana de 24 h
    # —el dueño contestando desde el panel al día siguiente, típicamente—.
    # Vacía = no se intenta, y ese mensaje queda en el log en vez de perderse
    # en silencio creyendo que salió.
    plantilla_reanudar: str = ""

    # ---- Embeddings ----
    # "api" (Gemini, sin memoria) | "local" (fastembed, +805 MB, sin red)
    embeddings_modo: str = "api"

    # ---- Observabilidad ----
    phoenix_habilitado: bool = False
    phoenix_endpoint: str = "http://localhost:6006/v1/traces"

    # ---- Backend de aturno ----
    aturno_modo: str = "doble"  # "doble" (en memoria) | "api" (backend real)
    aturno_api_url: str = "http://localhost:3001"

    # La web pública de aturno, para poder mandarle el link a quien prefiera
    # reservar desde ahí. La página de un negocio es <web>/<slug>.
    # Vacío a propósito: sin esto configurado el bot NO ofrece el link, en vez
    # de mandar uno inventado que lleva a un 404.
    aturno_web_url: str = ""

    # A dónde avisar cuando alguien pide hablar con una persona. Lo implementa
    # aturno; mientras no exista, el aviso queda en el log del servicio.
    escalacion_webhook: str = ""

    # El panel de aturno: adónde mandarle cada mensaje para que lo muestre, y
    # el secreto compartido que autentica los dos caminos. Vacío = el bot no
    # avisa nada y el panel no puede contestar; todo lo demás sigue igual.
    panel_url: str = ""
    panel_secreto: str = ""

    # Cuánto espera el bot, callado, después de escalar. Pasado ese rato sin
    # que nadie del negocio conteste, vuelve a tomar la conversación en vez de
    # dejar a la persona hablándole a un chat mudo.
    escalacion_minutos: int = 45

    # Cuánto vale lo que la persona eligió antes de que haya que preguntarlo de
    # nuevo. Sin esto la conversación no vencía nunca: quien llegó al resumen,
    # se fue, y volvió a la semana con un "dale" estaba confirmando la fecha de
    # entonces — un día que ya pasó.
    #
    # Media hora es lo que dura una interrupción normal: te atienden, cortás,
    # volvés. Más largo empieza a guardar decisiones que la persona ya no
    # recuerda haber tomado; más corto le hace repetir todo a alguien que sólo
    # se distrajo un rato.
    sesion_minutos: int = 30


@lru_cache
def config() -> Config:
    """Una sola instancia, cacheada. Importar esto, no construir Config()."""
    return Config()


# ---------- Registro de negocios ----------
# En producción esto sale de Firestore: cada negocio de aturno tiene su propio
# número de WhatsApp y el webhook rutea por el campo `To`. Con el Sandbox de
# Twilio hay un solo número, así que por ahora el mapa tiene una entrada.
#
# `business_id` es el SLUG de aturno: el mismo que va en la URL pública del
# negocio y el mismo con el que se llama su archivo en `datos/`. Un solo
# identificador para las tres cosas —la API, el RAG y el ruteo— en vez de una
# tabla de equivalencias que después hay que mantener sincronizada.
TENANTS: dict[str, Tenant] = {
    "+14155238886": Tenant(
        business_id="aturno",
        slug="aturno",
        # Solo el respaldo: el nombre real sale de aturno en cada mensaje
        # (`ClienteAturno.nombre_visible`). Este valor se usa únicamente
        # si el backend no contesta.
        nombre="Aturno",
        numero_whatsapp="+14155238886",
    ),
}


def tenant_por_numero(numero: str) -> Tenant | None:
    """Resuelve a qué negocio le escribieron, según el número que recibió.

    Devolver None y no explotar es a propósito: un mensaje a un número que no
    administramos es un caso normal, no un error del sistema.
    """
    return TENANTS.get(numero)
