"""
flujo.py — El orquestador: máquina de estados + plantillas + aturno, sobre LangGraph.

EL GRAFO
--------
    INICIO → entender → avanzar → responder → FIN

Tres responsabilidades separadas a propósito:

    entender   qué quiso decir la persona. Un número se resuelve en código;
               solo el texto libre llega al LLM.
    avanzar    qué paso sigue y qué se guardó. Determinístico, sin LLM.
    responder  qué texto sale. Siempre de plantillas.

El checkpointer de Postgres guarda el estado entre mensajes: en qué paso está
la conversación, qué eligió y quién es. Eso es a la vez la sesión del producto
y la persistencia que pide el Capstone.

LO QUE ESTE DISEÑO HACE IMPOSIBLE
---------------------------------
- Que el saludo salga distinto dos veces: lo escribe una plantilla.
- Que un listado salga horizontal: las plantillas usan "\\n".
- Que el usuario vea un JSON: el clasificador devuelve un objeto que nunca se
  imprime; lo que sale es siempre plantilla.
- Que se saltee un paso: `avanzar` solo consulta la tabla ORDEN.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import NotRequired, TypedDict

from src import plantillas as P
from src.agentes.clasificador import Clasificacion, clasificar, construir_clasificador
from src.agentes.estados import (
    AVANZA_CON,
    ORDEN,
    Estado,
    Intencion,
    anterior,
    numero_elegido,
    siguiente,
)
from src.aturno.base import ClienteAturno
from src.rag.indice import Recuperador, abrir_indice
from src.schemas import Alternativa, DatosDelCliente, MotivoNoDisponible

logger = logging.getLogger("pipeline.flujo")

DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

_aturno: ClienteAturno | None = None
_recuperadores: dict[str, Recuperador] = {}
_clasificador = None


def configurar(cliente: ClienteAturno) -> None:
    global _aturno, _clasificador
    _aturno = cliente
    _clasificador = construir_clasificador()


def _rag(business_id: str) -> Recuperador:
    if business_id not in _recuperadores:
        _recuperadores[business_id] = Recuperador(business_id, abrir_indice())
    return _recuperadores[business_id]


class Conversacion(TypedDict):
    """El estado que persiste entre mensajes."""

    mensaje: str
    estado: str                       # Estado.value
    respuesta: NotRequired[str]

    # Lo que la persona fue eligiendo
    servicio_id: NotRequired[str | None]
    profesional_id: NotRequired[str | None]
    fecha: NotRequired[str | None]    # ISO
    hora: NotRequired[str | None]     # HH:MM
    nombre: NotRequired[str | None]

    # Las opciones que se mostraron en el último mensaje. Sin esto no se puede
    # resolver "3": hay que saber contra qué lista.
    opciones: NotRequired[list[str]]

    # Resultado del paso `entender`, para que `avanzar` lo lea
    intent: NotRequired[str]
    entidades: NotRequired[dict]

    # Claves de un solo turno: `avanzar` le avisa a `responder` que use una
    # plantilla puntual en vez de la del paso. Van declaradas porque LangGraph
    # descarta cualquier clave que no esté en el esquema — se perdían en
    # silencio entre nodos y la confirmación salía como error técnico.
    # Se limpian al empezar cada mensaje: son de este turno, no de la sesión.
    # Solo primitivos: el checkpointer serializa a Postgres y guardar objetos
    # Pydantic ahí genera avisos de deserialización y ata el formato guardado
    # a la clase de hoy. Si el esquema cambia, las sesiones viejas se rompen.
    _plantilla: NotRequired[str | None]
    _datos: NotRequired[dict | None]


# ══════════════════════════════════════════════════════════════════
# Nodo 1 · entender
# ══════════════════════════════════════════════════════════════════

async def entender(conv: Conversacion, config) -> dict:
    """Un número se resuelve gratis; el texto libre va al clasificador."""
    estado = Estado(conv.get("estado") or Estado.APERTURA.value)
    opciones = conv.get("opciones") or []
    texto = conv["mensaje"]

    # Lo efímero del turno anterior no puede sobrevivir: si no, la plantilla
    # de confirmación se repetiría en el mensaje siguiente.
    limpio_turno = {"_plantilla": None, "_datos": None}

    indice = numero_elegido(texto, len(opciones))
    if indice is not None:
        # La persona eligió de la lista. No hace falta el modelo: sabemos qué
        # mostramos y en qué orden.
        logger.info("número %d → %s (sin LLM)", indice + 1, opciones[indice])
        return {
            **limpio_turno,
            "intent": (AVANZA_CON.get(estado) or Intencion.DESCONOCIDO).value,
            "entidades": {"_indice": indice},
        }

    cfg = (config.get("configurable") or {})
    hoy = date.today()
    resultado: Clasificacion = await clasificar(
        _clasificador, texto, estado, opciones,
        hoy.isoformat(), DIAS_ES[hoy.weekday()], cfg.get("calendario", ""),
    )
    return {
        **limpio_turno,
        "intent": resultado.intent.value,
        "entidades": resultado.entities.model_dump(exclude_none=True),
    }


# ══════════════════════════════════════════════════════════════════
# Nodo 2 · avanzar
# ══════════════════════════════════════════════════════════════════

# En qué paso se decide cada dato. Sirve para detectar cuando alguien quiere
# volver atrás ("mejor cambio de servicio" estando en el día).
PASO_DE = {
    Intencion.ELEGIR_SERVICIO: Estado.ESPERANDO_SERVICIO,
    Intencion.ELEGIR_STAFF: Estado.ESPERANDO_STAFF,
    Intencion.ELEGIR_DIA: Estado.ESPERANDO_DIA,
    Intencion.ELEGIR_HORARIO: Estado.ESPERANDO_HORARIO,
    Intencion.DAR_NOMBRE: Estado.ESPERANDO_NOMBRE,
}


async def avanzar(conv: Conversacion, config) -> dict:
    """Decide el próximo paso. Determinístico: no hay LLM en este nodo."""
    cfg = config.get("configurable") or {}
    negocio = cfg["business_id"]
    estado = Estado(conv.get("estado") or Estado.APERTURA.value)
    intent = Intencion(conv.get("intent") or Intencion.DESCONOCIDO.value)
    ent = conv.get("entidades") or {}
    cambios: dict = {}

    saltear = await _pasos_a_saltear(negocio, cfg)

    # Turno ya cerrado: el mensaje siguiente empieza un pedido nuevo. Se limpia
    # lo elegido pero NO quién es la persona — formulario nuevo, cliente
    # conocido. Es lo que espera alguien que vuelve a escribir después de
    # reservar, igual que en la web.
    if estado == Estado.CONFIRMADO:
        estado = Estado.APERTURA
        conv = {**conv, "estado": estado.value, "servicio_id": None,
                "profesional_id": None, "fecha": None, "hora": None}
        cambios.update({"servicio_id": None, "profesional_id": None,
                        "fecha": None, "hora": None})

    # ---- Transversales: no mueven el flujo ----
    if intent == Intencion.CANCELAR:
        return {"estado": Estado.APERTURA.value, "servicio_id": None,
                "profesional_id": None, "fecha": None, "hora": None,
                "opciones": [], "_plantilla": "cancelado"}

    if intent == Intencion.CONSULTAR_INFO:
        consulta = ent.get("consulta") or conv["mensaje"]
        return {"_plantilla": "info",
                "_datos": {"texto": await _rag(negocio).contexto(consulta)}}

    if intent == Intencion.VOLVER:
        return {"estado": anterior(estado, saltear).value}

    if intent in (Intencion.DESCONOCIDO, Intencion.SALUDO):
        # Un saludo a mitad de flujo no reinicia nada: repite el pedido actual.
        if estado == Estado.APERTURA:
            return _abrir(saltear)
        return {}

    # Primer contacto: SIEMPRE la apertura, sin importar qué haya escrito.
    # Es el requisito de que la puerta de entrada sea siempre la misma; el
    # dato que trajo no se pierde, se interpreta en el mensaje siguiente.
    if estado == Estado.APERTURA:
        return _abrir(saltear)

    # ---- ¿Quiere volver a un paso anterior? ----
    paso = PASO_DE.get(intent)
    if paso and paso in ORDEN and estado in ORDEN:
        if ORDEN.index(paso) < ORDEN.index(estado):
            # "mejor cambio de servicio" estando en el día. Retroceder está
            # permitido; saltear hacia adelante no. Se limpia lo elegido
            # después de ese paso: ya no es válido.
            logger.info("retrocede de %s a %s", estado.value, paso.value)
            return {"estado": paso.value, **_limpiar_desde(paso)}

    # ---- Avanza el paso actual ----
    if intent != AVANZA_CON.get(estado):
        return {}  # no corresponde a este paso: se repite el pedido

    resuelto = await _resolver(estado, conv, ent, negocio, cfg)
    if resuelto is None:
        return {}  # no se pudo resolver: se repite el pedido
    cambios.update(resuelto)

    if estado == Estado.ESPERANDO_CONFIRMACION:
        return {**cambios, **await _reservar(conv, cambios, negocio, cfg)}

    cambios["estado"] = siguiente(estado, saltear).value
    return cambios


def _abrir(saltear: set[Estado]) -> dict:
    """Muestra la apertura y deja el flujo listo para el próximo mensaje.

    El estado avanza a ESPERANDO_SERVICIO pero la plantilla que sale es la de
    apertura: sin esta marca, `responder` elegiría la plantilla del estado
    nuevo y el saludo con el nombre del negocio no se mostraría nunca.
    """
    return {"estado": siguiente(Estado.APERTURA, saltear).value,
            "_plantilla": "apertura"}


def _limpiar_desde(paso: Estado) -> dict:
    """Al retroceder, lo elegido después de ese paso deja de valer."""
    campos = {
        Estado.ESPERANDO_SERVICIO: ["servicio_id", "profesional_id", "fecha", "hora"],
        Estado.ESPERANDO_STAFF: ["profesional_id", "fecha", "hora"],
        Estado.ESPERANDO_DIA: ["fecha", "hora"],
        Estado.ESPERANDO_HORARIO: ["hora"],
    }.get(paso, [])
    return {c: None for c in campos}


async def _pasos_a_saltear(negocio: str, cfg: dict) -> set[Estado]:
    """Staff si no hay equipo; nombre si el teléfono ya es de un cliente."""
    saltear = set()
    if len(await _aturno.listar_personal(negocio)) <= 1:
        saltear.add(Estado.ESPERANDO_STAFF)
    if cfg.get("nombre_cliente"):
        saltear.add(Estado.ESPERANDO_NOMBRE)
    return saltear


async def _resolver(
    estado: Estado, conv: Conversacion, ent: dict, negocio: str, cfg: dict
) -> dict | None:
    """Traduce lo que dijo la persona al dato que guarda el estado."""
    indice = ent.get("_indice")

    if estado == Estado.ESPERANDO_SERVICIO:
        servicios = await _aturno.listar_servicios(negocio)
        if indice is not None:
            return {"servicio_id": servicios[indice].id}
        candidatos = _buscar(ent.get("servicio"), [(s.id, s.nombre) for s in servicios])
        # Un solo match: avanza (Nielsen #7). Cero o varios: se repite el
        # listado, que ahí sí es desambiguación y no confirmación redundante.
        return {"servicio_id": candidatos[0]} if len(candidatos) == 1 else None

    if estado == Estado.ESPERANDO_STAFF:
        gente = await _aturno.listar_personal(negocio, conv.get("servicio_id"))
        if indice is not None:
            return {"profesional_id": gente[indice].id if indice < len(gente) else None}
        nombre = (ent.get("profesional") or "").lower()
        if nombre in {"cualquiera", "me da igual", "el que sea", "no importa"}:
            return {"profesional_id": None}
        candidatos = _buscar(nombre, [(p.id, p.nombre) for p in gente])
        return {"profesional_id": candidatos[0]} if len(candidatos) == 1 else None

    if estado == Estado.ESPERANDO_DIA:
        dias = P.dias_elegibles(await _cupos(conv, negocio))
        if indice is not None and indice < len(dias):
            return {"fecha": dias[indice].fecha.isoformat()}
        if ent.get("fecha"):
            return {"fecha": ent["fecha"]}
        return None

    if estado == Estado.ESPERANDO_HORARIO:
        libres = await _horarios(conv, negocio)
        if indice is not None and indice < len(libres):
            return {"hora": libres[indice].strftime("%H:%M")}
        if ent.get("hora"):
            return {"hora": ent["hora"]}
        return None

    if estado == Estado.ESPERANDO_NOMBRE:
        return {"nombre": ent["nombre"]} if ent.get("nombre") else None

    if estado == Estado.ESPERANDO_CONFIRMACION:
        return {}

    return None


def _buscar(texto: str | None, opciones: list[tuple[str, str]]) -> list[str]:
    """Los ids cuyo nombre coincide. Sin tildes ni mayúsculas."""
    if not texto:
        return []
    import unicodedata

    def norm(s: str) -> str:
        d = unicodedata.normalize("NFD", s.lower())
        return " ".join("".join(c for c in d if unicodedata.category(c) != "Mn").split())

    objetivo = norm(texto)
    exactos = [i for i, n in opciones if norm(n) == objetivo]
    if exactos:
        return exactos
    return [i for i, n in opciones if objetivo in norm(n) or norm(n) in objetivo]


async def _cupos(conv: Conversacion, negocio: str):
    return await _aturno.dias_con_cupo(
        negocio, conv["servicio_id"], date.today(), 7, conv.get("profesional_id")
    )


async def _horarios(conv: Conversacion, negocio: str):
    disp = await _aturno.consultar_disponibilidad(
        negocio, conv["servicio_id"],
        date.fromisoformat(conv["fecha"]), conv.get("profesional_id"),
    )
    return disp.horarios


async def _reservar(conv: Conversacion, cambios: dict, negocio: str, cfg: dict) -> dict:
    """Crea el turno. Un rechazo es un resultado normal, no una excepción."""
    nombre = conv.get("nombre") or cfg.get("nombre_cliente") or ""
    fecha = date.fromisoformat(conv["fecha"])
    hora = datetime.strptime(conv["hora"], "%H:%M").time()

    turno = await _aturno.crear_turno(
        negocio, conv["servicio_id"], fecha, hora,
        DatosDelCliente(nombre=nombre, telefono=cfg.get("telefono", "")),
        conv.get("profesional_id"),
    )

    if turno.estado.value == "rechazado":
        consulta = await _aturno.consultar_pedido(
            negocio, conv["servicio_id"], fecha, hora, conv.get("profesional_id")
        )
        return {
            "_plantilla": "no_disponible",
            "_datos": {
                "motivo": consulta.motivo.value if consulta.motivo else None,
                "alternativas": [
                    {"fecha": a.fecha.isoformat(), "hora": a.hora.strftime("%H:%M")}
                    for a in consulta.alternativas[:3]
                ],
            },
            "estado": Estado.ESPERANDO_HORARIO.value, "hora": None,
        }

    return {
        "estado": Estado.CONFIRMADO.value,
        "_plantilla": "confirmado",
        "_datos": {
            "servicio": turno.servicio,
            "fecha": turno.fecha.isoformat(),
            "hora": turno.hora.strftime("%H:%M"),
            "codigo": turno.codigo,
        },
    }


# ══════════════════════════════════════════════════════════════════
# Nodo 3 · responder
# ══════════════════════════════════════════════════════════════════

async def responder(conv: Conversacion, config) -> dict:
    """Renderiza. Es el único lugar que produce texto para la persona."""
    cfg = config.get("configurable") or {}
    negocio = cfg["business_id"]
    nombre_negocio = cfg.get("nombre_negocio", "el negocio")
    estado = Estado(conv.get("estado") or Estado.APERTURA.value)
    especial = conv.get("_plantilla")

    # Plantillas puntuales que no dependen del paso
    if especial == "cancelado":
        return {"respuesta": P.cancelado(), "opciones": []}
    datos = conv.get("_datos") or {}

    if especial == "apertura":
        servicios = await _aturno.listar_servicios(negocio)
        return {"respuesta": P.apertura(nombre_negocio, servicios, cfg.get("nombre_cliente")),
                "opciones": [s.nombre for s in servicios]}

    if especial == "info":
        texto = datos.get("texto") or ""
        return {"respuesta": P.respuesta_info(texto) if texto else P.fuera_de_alcance()}

    if especial == "confirmado":
        return {"respuesta": P.confirmado(
            datos["servicio"], None,
            date.fromisoformat(datos["fecha"]),
            datetime.strptime(datos["hora"], "%H:%M").time(),
            datos["codigo"]), "opciones": []}

    if especial == "no_disponible":
        alts = [Alternativa(fecha=date.fromisoformat(a["fecha"]),
                            hora=datetime.strptime(a["hora"], "%H:%M").time(),
                            distancia_minutos=0) for a in datos.get("alternativas", [])]
        motivo = MotivoNoDisponible(datos["motivo"]) if datos.get("motivo") else None
        return {"respuesta": P.no_disponible(motivo, alts),
                "opciones": [a["hora"] for a in datos.get("alternativas", [])]}

    servicios = await _aturno.listar_servicios(negocio)

    if estado == Estado.APERTURA:
        return {"respuesta": P.apertura(nombre_negocio, servicios, cfg.get("nombre_cliente")),
                "opciones": [s.nombre for s in servicios]}

    if estado == Estado.ESPERANDO_SERVICIO:
        return {"respuesta": P.lista_servicios(servicios, conv.get("servicio_id")),
                "opciones": [s.nombre for s in servicios]}

    if estado == Estado.ESPERANDO_STAFF:
        gente = await _aturno.listar_personal(negocio, conv.get("servicio_id"))
        nombre_svc = next(s.nombre for s in servicios if s.id == conv["servicio_id"])
        return {"respuesta": P.lista_staff(gente, nombre_svc),
                "opciones": [p.nombre for p in gente] + ["Me da igual"]}

    if estado == Estado.ESPERANDO_DIA:
        cupos = await _cupos(conv, negocio)
        elegibles = P.dias_elegibles(cupos)
        return {"respuesta": P.selector_dias(cupos),
                "opciones": [d.fecha.isoformat() for d in elegibles]}

    if estado == Estado.ESPERANDO_HORARIO:
        libres = await _horarios(conv, negocio)
        dia = date.fromisoformat(conv["fecha"])
        if not libres:
            return {"respuesta": P.no_disponible(None, []), "opciones": []}
        return {"respuesta": P.lista_horarios(dia, libres),
                "opciones": [h.strftime("%H:%M") for h in libres[:8]]}

    if estado == Estado.ESPERANDO_NOMBRE:
        return {"respuesta": P.pedir_nombre(), "opciones": []}

    if estado == Estado.ESPERANDO_CONFIRMACION:
        nombre_svc = next(s.nombre for s in servicios if s.id == conv["servicio_id"])
        quien = None
        if conv.get("profesional_id"):
            gente = await _aturno.listar_personal(negocio)
            quien = next((p.nombre for p in gente if p.id == conv["profesional_id"]), None)
        return {"respuesta": P.resumen(nombre_svc, quien,
                                       date.fromisoformat(conv["fecha"]),
                                       datetime.strptime(conv["hora"], "%H:%M").time()),
                "opciones": ["sí", "no"]}

    return {"respuesta": P.error_tecnico(), "opciones": []}


# ══════════════════════════════════════════════════════════════════
# El grafo
# ══════════════════════════════════════════════════════════════════

def construir_flujo(checkpointer: BaseCheckpointSaver):
    g = StateGraph(Conversacion)
    g.add_node("entender", entender)
    g.add_node("avanzar", avanzar)
    g.add_node("responder", responder)
    g.add_edge(START, "entender")
    g.add_edge("entender", "avanzar")
    g.add_edge("avanzar", "responder")
    g.add_edge("responder", END)
    return g.compile(checkpointer=checkpointer)


def hilo_de(business_id: str, telefono: str) -> str:
    """El negocio va adelante: la misma persona puede ser cliente de dos."""
    return f"{business_id}:{telefono}"
