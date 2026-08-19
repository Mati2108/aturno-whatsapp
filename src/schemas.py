"""
schemas.py — Todos los contratos de datos del sistema.

El Capstone pide validar con Pydantic "todas las entradas y salidas de la API y
de las herramientas de los agentes". Este archivo es ese contrato único: lo que
entra por el webhook, lo que las tools le pasan al agente, y lo que el agente
devuelve.

Por qué importa acá más que en otros lados: un turno mal parseado no es un bug
silencioso, es una persona que se presenta el día equivocado. La validación es
la que convierte "el modelo dijo algo raro" en un error atrapable.
"""

from __future__ import annotations

import re
from datetime import date, time
from enum import Enum

from pydantic import BaseModel, Field, field_validator


# ---------- Identidad y multi-tenant ----------

class Tenant(BaseModel):
    """Un negocio de aturno. Todo en el sistema cuelga de acá.

    `business_id` es el SLUG del negocio en aturno —el mismo que va en su URL
    pública y el mismo con el que se llama su archivo en `datos/`—, no el uid
    de Firebase. Un solo identificador para las tres cosas (la API, el RAG y el
    ruteo) en vez de una tabla de equivalencias que hay que mantener; el uid lo
    resuelve `AturnoAPI._uid()` cuando hace falta. Decía "el uid de Firebase" y
    era falso: de que sea el slug depende que el link a la página no dé 404.

    Ningún dato puede cruzarse entre tenants: es el requisito de seguridad que
    define el diseño del RAG y del checkpointer.
    """

    business_id: str = Field(min_length=1)
    slug: str = Field(min_length=1, description="El /<slug> de su página pública")
    nombre: str = Field(min_length=1)
    # El número de WhatsApp por el que atiende este negocio. En producción cada
    # negocio tiene el suyo y es lo que permite rutear el webhook al tenant
    # correcto; con el Sandbox de Twilio hay uno solo para todos.
    numero_whatsapp: str = Field(min_length=1)


# ---------- Lo que entra por el webhook ----------

class MensajeEntrante(BaseModel):
    """Un mensaje de WhatsApp ya normalizado, sin la forma cruda de Twilio.

    Se valida antes de tocar nada: si Twilio cambia su payload o alguien postea
    basura al webhook, el error aparece en el borde y no tres capas adentro.
    """

    de: str = Field(min_length=1, description="Teléfono del cliente, formato E.164")
    para: str = Field(min_length=1, description="Número del negocio; rutea el tenant")
    texto: str = Field(min_length=1, max_length=4096)

    @field_validator("de", "para")
    @classmethod
    def _limpiar_telefono(cls, valor: str) -> str:
        """Twilio manda 'whatsapp:+5491122334455'; guardamos solo el número.

        Normalizar acá y no en cada uso es lo que evita que el mismo cliente
        tenga dos hilos de conversación por diferencias de formato.
        """
        limpio = valor.replace("whatsapp:", "").strip()
        if not re.fullmatch(r"\+?\d{8,15}", limpio):
            raise ValueError(f"Teléfono con formato inesperado: {valor!r}")
        return limpio if limpio.startswith("+") else f"+{limpio}"


# ---------- Lo que el bot lee de aturno ----------

_ETIQUETAS = re.compile(r"<[^>]*>")
_LINK_MD = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_ESPACIOS = re.compile(r"\s+")


def limpiar_nombre(valor: str) -> str:
    """Deja un nombre de persona, no lo que haya escrito.

    Existe por dónde TERMINA este dato. En aturno, el nombre del cliente se
    interpola sin escapar dentro del mail HTML que recibe el dueño del negocio
    (`emailService.js`, en el cuerpo y en el asunto). O sea que quien escribe
    por WhatsApp puede meterle formato y links a un mail que el dueño abre
    confiado porque lo estaba esperando.

    El arreglo de fondo va allá: escapar al escribir el mail protege también al
    formulario de la web, que tiene el mismo agujero desde antes que este bot
    existiera. Esto es la otra mitad — no dejar entrar por acá lo que no
    debería llegar nunca. Las dos cosas, no una.

    Se sacan etiquetas, links de markdown y URLs; se conserva todo lo que
    aparece en un nombre real: acentos, apóstrofos, guiones y puntos.
    """
    limpio = _LINK_MD.sub(r"\1", valor or "")
    limpio = _ETIQUETAS.sub(" ", limpio)
    limpio = _URL.sub(" ", limpio)
    return _ESPACIOS.sub(" ", limpio).strip()


class Contacto(BaseModel):
    """Cómo se llega a una persona de carne y hueso en este negocio.

    Todos los campos son opcionales porque un negocio puede no haber cargado
    ninguno, y en ese caso el bot tiene que decir eso y no un teléfono
    inventado. `hay_algo()` es lo que decide si se puede derivar.
    """

    telefono: str | None = None
    whatsapp: str | None = None
    email: str | None = None
    direccion: str | None = None

    def hay_algo(self) -> bool:
        return bool(self.telefono or self.whatsapp or self.email)


class Servicio(BaseModel):
    """Un servicio que ofrece el negocio. Espejo de lo que devuelve aturno."""

    id: str = Field(min_length=1)
    nombre: str = Field(min_length=1)
    duracion_minutos: int = Field(gt=0, le=8 * 60)
    precio: float = Field(ge=0)

    # Si este servicio se seña, y cuánto. Viaja con el servicio y no se pide
    # aparte porque hace falta ANTES de reservar: el resumen tiene que decir
    # que hay una seña, igual que la web abre su modal antes de crear nada.
    # Enterarse recién cuando llega el link de pago es enterarse tarde.
    #
    # `senia` es una estimación para mostrar; el monto que se cobra lo calcula
    # el servidor de aturno desde el `depositConfig` del servicio real.
    requiere_senia: bool = False
    senia: int = Field(default=0, ge=0)

    @field_validator("nombre")
    @classmethod
    def _sin_espacios_de_sobra(cls, valor: str) -> str:
        return " ".join(valor.split())


class Profesional(BaseModel):
    """Quién atiende. En aturno es la subcolección `staff` del negocio."""

    id: str = Field(min_length=1)
    nombre: str = Field(min_length=1)
    servicios: list[str] = Field(
        default_factory=list,
        description="Ids de los servicios que hace. Vacío = hace todos.",
    )

    def atiende(self, servicio_id: str) -> bool:
        return not self.servicios or servicio_id in self.servicios


class Disponibilidad(BaseModel):
    """Los horarios libres de un servicio en un día."""

    fecha: date
    servicio_id: str = Field(min_length=1)
    profesional_id: str | None = None
    horarios: list[time] = Field(
        default_factory=list,
        description="Vacío significa 'ese día no hay lugar', no es un error.",
    )


class SinLugar(str, Enum):
    """Por qué un día no tiene turnos. Los cuatro motivos son distintos.

    Existía un solo booleano `abierto`, y los cuatro casos salían como
    "cerrado". Decirle a alguien que el local está cerrado un sábado que abre
    —solo porque el profesional que eligió no trabaja ese día— es información
    falsa sobre el negocio, y la persona se va convencida de otra cosa.
    """

    CERRADO = "cerrado"            # el local no abre ese día
    NO_ATIENDE = "no_atiende"      # abre, pero esa persona no trabaja
    YA_PASO = "ya_paso"            # abre, pero ya no quedan horas hoy
    COMPLETO = "completo"          # abre y atiende, pero está todo tomado


class DiaConCupo(BaseModel):
    """Un día y cuántos turnos le quedan. Para mostrar antes de que elija.

    Es lo que hace la página de aturno: ver de un vistazo qué días tienen lugar
    en vez de ir probando uno por uno.
    """

    fecha: date
    libres: int = Field(ge=0)
    motivo: SinLugar | None = Field(
        default=None,
        description="Por qué no hay lugar. None cuando sí hay.",
    )

    @property
    def abierto(self) -> bool:
        """Si el LOCAL abre ese día. No es lo mismo que tener lugar."""
        return self.motivo != SinLugar.CERRADO


class MotivoNoDisponible(str, Enum):
    """Por qué no se puede dar el turno pedido. Cada uno se explica distinto."""

    CERRADO = "cerrado"                    # el negocio no abre ese día
    FUERA_DE_HORARIO = "fuera_de_horario"  # abre, pero no a esa hora
    OCUPADO = "ocupado"                    # esa hora ya está tomada
    PROFESIONAL_OCUPADO = "profesional_ocupado"
    PROFESIONAL_NO_HACE = "profesional_no_hace"  # esa persona no hace ese servicio


class Alternativa(BaseModel):
    """Una opción cercana a lo que la persona pidió."""

    fecha: date
    hora: time
    profesional_id: str | None = None
    profesional_nombre: str | None = None
    # Cuánto se aleja de lo pedido, en minutos. Ordena las opciones: primero
    # las más parecidas a lo que la persona quería.
    distancia_minutos: int = Field(ge=0)


class Consulta(BaseModel):
    """Lo que la persona pidió, y qué pasó con eso.

    Es la respuesta a "un corte con Lean el martes a las 15": decís si se puede,
    y si no, POR QUÉ y qué hay cerca. Sin el motivo, el bot solo sabe decir
    "no hay" — que es la respuesta menos útil posible.
    """

    disponible: bool
    motivo: MotivoNoDisponible | None = None
    alternativas: list[Alternativa] = Field(default_factory=list)

    @field_validator("motivo")
    @classmethod
    def _explicar_el_no(cls, valor, info):
        if info.data.get("disponible") is False and valor is None:
            raise ValueError("Si no está disponible hay que decir por qué")
        return valor


# ---------- Lo que el agente decide ----------

class EstadoDelTurno(str, Enum):
    """Hereda de str para que serialice como 'confirmado' y no como el enum."""

    CONFIRMADO = "confirmado"
    PENDIENTE_DE_SENA = "pendiente_de_sena"
    RECHAZADO = "rechazado"


class DatosDelCliente(BaseModel):
    """Lo que hay que saber de la persona para poder reservar.

    Es lo que el checkpointer recuerda entre conversaciones: la segunda vez que
    escribe, esto ya está cargado y no hay que volver a preguntarlo. Es la
    feature del producto y el requisito de persistencia del Capstone, a la vez.
    """

    nombre: str | None = Field(default=None, min_length=2, max_length=80)
    telefono: str = Field(min_length=8)
    email: str | None = None

    @field_validator("nombre", mode="before")
    @classmethod
    def _solo_un_nombre(cls, valor):
        """Se limpia acá y no en el flujo: es el único paso obligado.

        Cualquier camino que termine en una reserva construye este objeto, así
        que poner la limpieza en el esquema hace imposible que un nombre sin
        limpiar llegue a aturno — aunque mañana aparezca otra forma de tomarlo.
        Un `.strip()` suelto en el nodo que hoy lo pide se olvida en el que se
        agregue después.
        """
        if not isinstance(valor, str):
            return valor
        limpio = limpiar_nombre(valor)
        # Si lo que quedó no alcanza para ser un nombre, se descarta entero y
        # el flujo vuelve a preguntarlo. Guardar el resto sería peor.
        return limpio[:80] if len(limpio) >= 2 else None

    def esta_completo(self) -> bool:
        """Con nombre y teléfono alcanza para reservar; el email es opcional."""
        return bool(self.nombre and self.telefono)


class SolicitudDeTurno(BaseModel):
    """Lo que el agente extrajo de la conversación. La entrada de la tool.

    Los campos son opcionales a propósito: el agente los va completando de a
    poco a lo largo del chat. `esta_completa()` es lo que decide si ya se puede
    intentar reservar o si todavía falta preguntar algo.
    """

    servicio_id: str | None = None
    profesional_id: str | None = None   # opcional: "con Lean"
    fecha: date | None = None
    hora: time | None = None
    cliente: DatosDelCliente | None = None

    def esta_completa(self) -> bool:
        return all(
            [
                self.servicio_id,
                self.fecha,
                self.hora,
                self.cliente and self.cliente.esta_completo(),
            ]
        )

    def que_falta(self) -> list[str]:
        """Qué le falta para poder reservar. Guía la próxima pregunta del bot."""
        faltantes = []
        if not self.servicio_id:
            faltantes.append("servicio")
        if not self.fecha:
            faltantes.append("fecha")
        if not self.hora:
            faltantes.append("hora")
        if not (self.cliente and self.cliente.nombre):
            faltantes.append("nombre")
        return faltantes


class TurnoConfirmado(BaseModel):
    """La respuesta de aturno cuando el turno se creó (o no)."""

    estado: EstadoDelTurno
    booking_id: str | None = None
    codigo: str | None = Field(
        default=None,
        description="El código con el que el cliente consulta o cancela.",
    )
    fecha: date | None = None
    hora: time | None = None
    servicio: str | None = None
    # Con quién quedó, aunque la persona haya dicho que le daba igual. Es lo
    # primero que va a querer saber cuando llegue al local.
    profesional: str | None = None
    motivo_del_rechazo: str | None = None

    # ---- La seña, cuando el servicio la pide ----
    #
    # Con `estado = PENDIENTE_DE_SENA` el turno EXISTE pero todavía no es de
    # nadie: aturno lo guarda como `pending_deposit`, que aparta el horario
    # `minutos_de_retencion` y lo suelta solo si nadie paga. Los tres campos
    # viajan juntos porque el mensaje que va a recibir la persona necesita los
    # tres: cuánto, dónde pagar, y hasta cuándo le guardamos el lugar.
    #
    # `senia` la calcula el SERVIDOR de aturno a partir del `depositConfig` del
    # servicio real; acá llega para mostrarla, no para decidirla. Es la misma
    # regla que rige en `planes.js` y en `senas.js`: ningún monto que se le
    # manda a Mercado Pago sale de quien pide el cobro.
    senia: int | None = None
    link_de_pago: str | None = None
    minutos_de_retencion: int | None = None

    @field_validator("motivo_del_rechazo")
    @classmethod
    def _rechazo_explicado(cls, valor: str | None, info) -> str | None:
        """Un rechazo sin motivo deja al bot sin nada que decirle a la persona."""
        if info.data.get("estado") == EstadoDelTurno.RECHAZADO and not valor:
            raise ValueError("Un turno rechazado tiene que explicar por qué")
        return valor

    @field_validator("link_de_pago")
    @classmethod
    def _la_senia_necesita_link(cls, valor: str | None, info) -> str | None:
        """Un turno esperando la seña sin link es una persona sin dónde pagar.

        Es el estado peor de los tres posibles: el horario queda apartado, la
        persona cree que tiene algo, y no hay forma de completarlo. Si el link
        no se pudo generar, el turno NO puede volver como pendiente de seña —
        vuelve como rechazado, con su motivo, y la retención vence sola.
        """
        if info.data.get("estado") == EstadoDelTurno.PENDIENTE_DE_SENA and not valor:
            raise ValueError("Un turno pendiente de seña tiene que traer el link de pago")
        return valor
