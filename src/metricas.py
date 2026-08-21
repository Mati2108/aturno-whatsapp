"""
metricas.py — Cuántas conversaciones termina el bot solo, y dónde se cae.

POR QUÉ EXISTE
--------------
`gasto.py` ya dice cuánto cuesta cada conversación. Faltaba la otra mitad: de
esas conversaciones, cuántas terminaron en un turno. Sin ese número, el costo
por turno RESUELTO —que es el que va en un presupuesto— no se puede calcular, y
la pregunta que hace cualquier negocio antes de pagar —«¿cuántos me resuelve
solo?»— sólo se puede contestar con una anécdota.

El nombre de industria es *containment rate*. La referencia para un bot angosto
y transaccional como éste es 65–85%.

TRES DECISIONES QUE VALE LA PENA EXPLICAR
-----------------------------------------
**1 · Tabla propia, no leer la del checkpointer.**
El estado final SE PUEDE leer del checkpointer: `channel_values->>'estado'` está
ahí y la API de LangGraph devuelve lo mismo. Pero ese esquema es de ellos y
cambia sin avisarnos, y además la historia que hay guardada es inservible —de
45 conversaciones en la base de desarrollo, 38 son de versiones viejas del grafo
y ninguna llegó a `confirmado`—. Así que: tabla nuestra, y los números arrancan
de cero el día que esto se despliega. No hay historia que recuperar.

**2 · El abandono se calcula al LEER, no se escribe.**
Nadie avisa que abandonó. Si se esperara a marcarlo, haría falta un barrendero
periódico, y una conversación que nunca vuelve no se marcaría jamás. Acá una
conversación abierta cuya última señal es más vieja que el vencimiento de sesión
ES un abandono, y eso se decide con un `where` en el momento de contar. Sin
tarea de fondo y sin perder ninguna.

**3 · El hilo se guarda hasheado.**
El identificador del hilo lleva el teléfono adentro. Para contar no hace falta
saber de quién es la conversación, sólo que son distintas — que es exactamente
lo que da un hash. Guardar el número sería el pecado de pedir más datos de los
necesarios, cometido del lado nuestro.

NUNCA LEVANTA
-------------
Igual que `gasto.py` y que Phoenix: si la base no está, si la tabla no existe o
si la consulta falla, se loguea y se sigue. Una conversación que no se contó es
un número menos; una excepción acá sería un turno que no se reservó.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from statistics import median

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from src.config import config
from src.fechas import ahora

logger = logging.getLogger(__name__)

# Cuánto silencio convierte una conversación abierta en un abandono. Es el mismo
# vencimiento que usa el flujo para reiniciar una sesión: si para el bot la
# conversación ya venció, para las cuentas también terminó.
HORAS_HASTA_ABANDONO = 2

_URL: str | None = None
_pool: AsyncConnectionPool | None = None


def _url() -> str:
    global _URL
    if _URL is None:
        _URL = config().database_url
    return _URL


async def _conexiones() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(_url(), min_size=1, max_size=4, open=False,
                                    kwargs={"row_factory": dict_row})
        await _pool.open(wait=True, timeout=5)
    return _pool


def olvidar_pool() -> None:
    """Suelta el pool sin cerrarlo. Para los tests, que cambian de base."""
    global _pool
    _pool = None


async def cerrar() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _hilo_hash(hilo: str) -> str:
    return hashlib.sha256(hilo.encode()).hexdigest()[:32]


# ══════════════════════════════════════════════════════════════════
#  Escribir
# ══════════════════════════════════════════════════════════════════

TABLA = """
create table if not exists conversaciones (
    hilo          text primary key,
    business_id   text        not null,
    abierta_en    timestamptz not null,
    ultimo_en     timestamptz not null,
    turnos        integer     not null default 0,
    estado_final  text,
    desenlace     text,
    cerrada_en    timestamptz
);
create index if not exists conversaciones_negocio on conversaciones (business_id);
"""


async def preparar() -> None:
    """Crea la tabla si falta. Se llama al arrancar, junto al checkpointer."""
    pool = await _conexiones()
    async with pool.connection() as c:
        await c.execute(TABLA)


async def registrar(hilo: str, business_id: str, estado: str,
                    desenlace: str | None = None,
                    cuando: datetime | None = None) -> None:
    """Anota un mensaje de esta conversación. Nunca levanta.

    Se llama con CADA mensaje, no sólo al cerrar: así el contador de mensajes y
    el paso donde quedó la persona están al día aunque la conversación no
    termine nunca — que es justo el caso del abandono.

    `desenlace` sólo viaja cuando la conversación se cierra de verdad
    (`reservado` o `escalado`). Una vez cerrada no se pisa: quien reservó y
    sigue escribiendo ya ganó, y el mensaje siguiente empieza otra historia.
    """
    t = cuando or ahora()
    try:
        pool = await _conexiones()
        async with pool.connection() as c:
            await c.execute("""
                insert into conversaciones
                       (hilo, business_id, abierta_en, ultimo_en, turnos,
                        estado_final, desenlace, cerrada_en)
                values (%s, %s, %s, %s, 1, %s, %s, %s)
                on conflict (hilo) do update set
                    ultimo_en    = excluded.ultimo_en,
                    turnos       = conversaciones.turnos + 1,
                    estado_final = excluded.estado_final,
                    desenlace    = coalesce(conversaciones.desenlace, excluded.desenlace),
                    cerrada_en   = coalesce(conversaciones.cerrada_en, excluded.cerrada_en)
            """, (_hilo_hash(hilo), business_id, t, t, estado, desenlace,
                  t if desenlace else None))
    except Exception as e:  # noqa: BLE001
        logger.warning("no se pudo registrar la métrica (%s): %s", type(e).__name__, e)


# ══════════════════════════════════════════════════════════════════
#  Leer
# ══════════════════════════════════════════════════════════════════

VACIO = {"cerradas": 0, "en_curso": 0, "reservadas": 0, "escaladas": 0,
         "abandonadas": 0, "containment": None, "escalacion": None,
         "abandono": None, "abandono_por_paso": {}, "turnos_hasta_reservar": None}


async def resumen(business_id: str | None = None, solo_hoy: bool = False) -> dict:
    """Los números. Ante cualquier problema devuelve ceros, no una excepción.

    `solo_hoy` existe para poder dividir por lo que dice `gasto.py`, que lleva
    la cuenta del día. Dividir un gasto de hoy por conversaciones de siempre da
    un número que parece un costo y no lo es.
    """
    ahora_ = ahora()
    corte = ahora_ - timedelta(hours=HORAS_HASTA_ABANDONO)
    desde = ahora_.replace(hour=0, minute=0, second=0, microsecond=0) if solo_hoy else None
    try:
        pool = await _conexiones()
        async with pool.connection() as c:
            filas = await (await c.execute("""
                select desenlace, estado_final, turnos, ultimo_en
                from conversaciones
                -- El cast es obligatorio: sin él Postgres no puede deducir el
                -- tipo de un parámetro que sólo aparece en un «is null».
                where (%s::text is null or business_id = %s::text)
                  and (%s::timestamptz is null or ultimo_en >= %s::timestamptz)
            """, (business_id, business_id, desde, desde))).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.warning("no se pudo leer las métricas (%s): %s", type(e).__name__, e)
        return dict(VACIO)

    reservadas = [f for f in filas if f["desenlace"] == "reservado"]
    escaladas = [f for f in filas if f["desenlace"] == "escalado"]
    # Abierta y callada hace rato = abandonada. Abierta y reciente = en curso.
    abiertas = [f for f in filas if f["desenlace"] is None]
    abandonadas = [f for f in abiertas if f["ultimo_en"] <= corte]
    en_curso = len(abiertas) - len(abandonadas)

    cerradas = len(reservadas) + len(escaladas) + len(abandonadas)
    porcion = (lambda n: round(n / cerradas, 4)) if cerradas else (lambda n: None)

    por_paso: dict[str, int] = {}
    for f in abandonadas:
        paso = f["estado_final"] or "desconocido"
        por_paso[paso] = por_paso.get(paso, 0) + 1

    return {
        "cerradas": cerradas,
        "en_curso": en_curso,
        "reservadas": len(reservadas),
        "escaladas": len(escaladas),
        "abandonadas": len(abandonadas),
        "containment": porcion(len(reservadas)),
        "escalacion": porcion(len(escaladas)),
        "abandono": porcion(len(abandonadas)),
        "abandono_por_paso": dict(sorted(por_paso.items(), key=lambda kv: -kv[1])),
        "turnos_hasta_reservar": (int(median(f["turnos"] for f in reservadas))
                                  if reservadas else None),
    }


# ══════════════════════════════════════════════════════════════════
#  Para los tests
# ══════════════════════════════════════════════════════════════════

async def volcado(business_id: str) -> list[dict]:
    """Las filas crudas de un negocio. Sólo lo usa `test_metricas.py`."""
    pool = await _conexiones()
    async with pool.connection() as c:
        return await (await c.execute(
            "select * from conversaciones where business_id = %s",
            (business_id,))).fetchall()


async def borrar_negocio(business_id: str) -> None:
    """Limpia lo que sembró una corrida de prueba."""
    try:
        pool = await _conexiones()
        async with pool.connection() as c:
            await c.execute("delete from conversaciones where business_id = %s",
                            (business_id,))
    except Exception:  # noqa: BLE001
        pass
