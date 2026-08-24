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
    cerrada_en    timestamptz,
    -- Lo último que escribió antes de irse. Sin esto, «2 abandonaron eligiendo
    -- el horario» dice DÓNDE pero no POR QUÉ, y el por qué es lo único que se
    -- puede arreglar. Del paso del nombre no se guarda: ver `_PASO_PRIVADO`.
    ultimo_mensaje text
);
alter table conversaciones add column if not exists ultimo_mensaje text;
create index if not exists conversaciones_negocio on conversaciones (business_id);

-- Lo que el bot YA detecta y hasta ahora tiraba.
--
-- Cada vez que no entiende, que el guardián frena una redacción, que alguien
-- pide un horario que no está o que preguntan algo sin cargar, el bot lo sabe:
-- lo usa para contestar y después se pierde en un log que nadie lee.
--
-- Una tabla sola para los cuatro tipos, y no cuatro tablas. Todos tienen la
-- misma forma —qué pasó, dónde, con qué texto— y lo que se hace con ellos
-- también es lo mismo: agrupar y contar. Cuatro tablas serían cuatro consultas
-- iguales y cuatro lugares donde olvidarse de agregar un tipo nuevo.
create table if not exists senales (
    id          bigserial primary key,
    tipo        text        not null,
    business_id text        not null,
    paso        text,
    texto       text,
    detalle     text,
    cuando      timestamptz not null
);
create index if not exists senales_negocio on senales (business_id, tipo);

-- Cuántos mensajes pasó cada paso. Es el DENOMINADOR, y sin él los tropiezos no
-- significan nada: «24 veces no entendió eligiendo el servicio» puede ser un
-- desastre o puede ser normal, según si fueron 24 de 30 o 24 de 900.
--
-- Una fila por (negocio, paso) y no una por mensaje: lo único que se pregunta
-- es «cuántos», así que guardar cada mensaje sería pagar millones de filas por
-- un número que cabe en una.
-- UN EVENTO POR MENSAJE. La base de todo lo demás.
--
-- Hasta acá cada métrica nueva pedía una tabla o una columna: `conversaciones`
-- para el containment, `senales` para lo que hay que arreglar, `pasos` para el
-- denominador. Tres tablas de propósito único, y la cuarta pregunta que se nos
-- ocurriera iba a pedir la cuarta tabla.
--
-- Con un evento por mensaje —de qué paso venía, a cuál fue, si avanzó— el
-- embudo, los tiempos, la matriz de confusión y lo que se nos ocurra dentro de
-- tres meses se CALCULAN. Es la práctica estándar en observabilidad de agentes
-- y es exactamente lo contrario de lo que veníamos haciendo.
--
-- Volumen: un negocio con 30 conversaciones diarias son ~180 filas por día.
-- Cien negocios, 18.000. Postgres ni se entera, y lo viejo se puede podar.
--
-- `texto` va SÓLO cuando no se entendió, y nunca del paso del nombre: guardar
-- lo que el bot sí entendió no arregla nada y son mensajes de personas.
create table if not exists eventos (
    id           bigserial primary key,
    conversacion text        not null,
    business_id  text        not null,
    paso_antes   text        not null,
    paso_despues text        not null,
    avanzo       boolean     not null,
    intent       text,
    resuelto_por text,
    plantilla    text,
    demoro_ms    integer,
    texto        text,
    cuando       timestamptz not null
);
create index if not exists eventos_negocio on eventos (business_id, cuando);
create index if not exists eventos_conv on eventos (conversacion);

create table if not exists pasos (
    business_id text not null,
    paso        text not null,
    mensajes    bigint not null default 0,
    primary key (business_id, paso)
);
"""

# Los cuatro tipos, escritos para que agregar uno sea agregarlo acá.
TIPOS = ("no_entendio", "guardian", "demanda_perdida", "sin_respuesta", "abuso")

# El paso donde la gente escribe su nombre completo. De ahí NO se guarda el
# texto: un nombre que el bot no entendió no se arregla mirando una tabla, así
# que guardarlo es quedarse con un dato personal a cambio de nada.
_PASO_PRIVADO = "esperando_nombre"


async def preparar() -> None:
    """Crea la tabla si falta. Se llama al arrancar, junto al checkpointer."""
    pool = await _conexiones()
    async with pool.connection() as c:
        await c.execute(TABLA)


async def registrar(hilo: str, business_id: str, estado: str,
                    desenlace: str | None = None,
                    cuando: datetime | None = None,
                    mensaje: str | None = None) -> None:
    """Anota un mensaje de esta conversación. Nunca levanta.

    Se llama con CADA mensaje, no sólo al cerrar: así el contador de mensajes y
    el paso donde quedó la persona están al día aunque la conversación no
    termine nunca — que es justo el caso del abandono.

    `desenlace` sólo viaja cuando la conversación se cierra de verdad
    (`reservado` o `escalado`). Una vez cerrada no se pisa: quien reservó y
    sigue escribiendo ya ganó, y el mensaje siguiente empieza otra historia.
    """
    t = cuando or ahora()
    if estado == _PASO_PRIVADO:
        mensaje = None
    ultimo = (mensaje or "").strip()[:200] or None
    try:
        pool = await _conexiones()
        async with pool.connection() as c:
            await c.execute("""
                insert into conversaciones
                       (hilo, business_id, abierta_en, ultimo_en, turnos,
                        estado_final, desenlace, cerrada_en, ultimo_mensaje)
                values (%s, %s, %s, %s, 1, %s, %s, %s, %s)
                on conflict (hilo) do update set
                    ultimo_en     = excluded.ultimo_en,
                    turnos        = conversaciones.turnos + 1,
                    estado_final  = excluded.estado_final,
                    desenlace     = coalesce(conversaciones.desenlace, excluded.desenlace),
                    cerrada_en    = coalesce(conversaciones.cerrada_en, excluded.cerrada_en),
                    ultimo_mensaje = excluded.ultimo_mensaje
            """, (_hilo_hash(hilo), business_id, t, t, estado, desenlace,
                  t if desenlace else None, ultimo))
    except Exception as e:  # noqa: BLE001
        logger.warning("no se pudo registrar la métrica (%s): %s", type(e).__name__, e)


async def evento(conversacion: str, business_id: str, paso_antes: str,
                 paso_despues: str, avanzo: bool, intent: str | None = None,
                 resuelto_por: str | None = None, plantilla: str | None = None,
                 demoro_ms: int | None = None, texto: str | None = None,
                 cuando: datetime | None = None) -> None:
    """Anota un mensaje. Nunca levanta.

    Los dos campos que importan son `paso_antes` y `paso_despues`: con ellos el
    embudo sale de una consulta, y sin ellos no sale de ninguna. Todo lo demás
    es contexto que se agradece después.
    """
    if paso_antes == _PASO_PRIVADO or paso_despues == _PASO_PRIVADO:
        texto = None
    try:
        pool = await _conexiones()
        async with pool.connection() as c:
            await c.execute("""
                insert into eventos (conversacion, business_id, paso_antes,
                    paso_despues, avanzo, intent, resuelto_por, plantilla,
                    demoro_ms, texto, cuando)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (_hilo_hash(conversacion), business_id, paso_antes, paso_despues,
                  avanzo, intent, resuelto_por, plantilla, demoro_ms,
                  (texto or "").strip()[:200] or None, cuando or ahora()))
    except Exception as e:  # noqa: BLE001
        logger.warning("no se pudo anotar el evento (%s): %s", type(e).__name__, e)


# El orden del flujo. El embudo se muestra en este orden y no por frecuencia:
# un embudo desordenado deja de ser un embudo — lo que se lee es la caída de un
# escalón al siguiente, y eso sólo se ve si los escalones están en orden.
_ORDEN_PASOS = ("apertura", "esperando_servicio", "esperando_staff",
                "esperando_dia", "esperando_horario", "esperando_nombre",
                "esperando_confirmacion", "esperando_senia", "confirmado")


async def embudo(business_id: str | None = None) -> list[dict]:
    """Cuántos LLEGARON a cada paso y cuántos lo PASARON.

    Es la diferencia entre un dato y un número suelto. «24 tropiezos eligiendo
    el servicio» no se puede leer; «se cae 1 de cada 5» sí.

    Y separa dos fallas que de otro modo se confunden:

      · CAÍDA alta — el paso está mal planteado, la gente se va.
      · MENSAJES POR CONVERSACIÓN alto con caída baja — se entiende mal pero la
        gente insiste. No se ve de ninguna otra forma, y suele ser lo más barato
        de arreglar.

    «Llegaron» se cuenta por conversaciones distintas, no por mensajes: alguien
    que intenta cuatro veces el mismo paso llegó una sola vez.
    """
    try:
        pool = await _conexiones()
        async with pool.connection() as c:
            filas = await (await c.execute("""
                select paso_antes as paso,
                       count(distinct conversacion) as llegaron,
                       count(distinct conversacion) filter (where avanzo) as pasaron,
                       count(*) as mensajes
                from eventos
                where (%s::text is null or business_id = %s::text)
                group by paso_antes
            """, (business_id, business_id))).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.warning("no se pudo leer el embudo (%s): %s", type(e).__name__, e)
        return []

    por_paso = {f["paso"]: f for f in filas}
    salida = []
    for paso in _ORDEN_PASOS:
        f = por_paso.pop(paso, None)
        if f:
            salida.append(_fila_embudo(f))
    # Un paso que no está en `_ORDEN_PASOS` igual se muestra, al final. Es la
    # red para cuando alguien agregue un estado y se olvide de la lista: mejor
    # verlo fuera de orden que no verlo.
    salida += [_fila_embudo(f) for f in por_paso.values()]
    return salida


def _fila_embudo(f: dict) -> dict:
    llegaron, pasaron = f["llegaron"], f["pasaron"]
    return {
        "paso": f["paso"],
        "llegaron": llegaron,
        "pasaron": pasaron,
        "caida": round((llegaron - pasaron) / llegaron, 4) if llegaron else None,
        "mensajes": f["mensajes"],
        "mensajes_por_conversacion": (round(f["mensajes"] / llegaron, 2)
                                      if llegaron else None),
    }


async def contar_paso(business_id: str, paso: str) -> None:
    """Suma un mensaje al paso donde ocurrió. Nunca levanta.

    Se llama con CADA mensaje, no sólo con los que fallan. Es la mitad que
    faltaba: los tropiezos sin el total son un número sin escala.
    """
    if not paso:
        return
    try:
        pool = await _conexiones()
        async with pool.connection() as c:
            await c.execute("""
                insert into pasos (business_id, paso, mensajes) values (%s, %s, 1)
                on conflict (business_id, paso)
                do update set mensajes = pasos.mensajes + 1
            """, (business_id, paso))
    except Exception as e:  # noqa: BLE001
        logger.warning("no se pudo contar el paso (%s): %s", type(e).__name__, e)


async def anotar(tipo: str, business_id: str, paso: str | None = None,
                 texto: str | None = None, detalle: str | None = None,
                 cuando: datetime | None = None) -> None:
    """Guarda algo que el bot detectó y hasta ahora tiraba. Nunca levanta.

    `texto` es lo que se va a agrupar después: el mensaje que no entendió, la
    palabra que frenó el guardián, el día que pidieron y no había. Es lo único
    que convierte una estadística en algo accionable — «falló 7 veces» no dice
    qué arreglar; «"tenés turno pa hoy?" falló 7 veces» sí.

    Del paso del nombre no se guarda el texto. Ver `_PASO_PRIVADO`.
    """
    if paso == _PASO_PRIVADO:
        texto = None
    try:
        pool = await _conexiones()
        async with pool.connection() as c:
            await c.execute(
                "insert into senales (tipo, business_id, paso, texto, detalle, cuando)"
                " values (%s, %s, %s, %s, %s, %s)",
                (tipo, business_id, paso, (texto or "").strip()[:200] or None,
                 detalle, cuando or ahora()))
    except Exception as e:  # noqa: BLE001
        logger.warning("no se pudo anotar la señal (%s): %s", type(e).__name__, e)


async def senales(business_id: str | None = None, tope: int = 25) -> dict:
    """Lo que el bot no supo hacer, AGRUPADO y ordenado por frecuencia.

    Agrupar es toda la función. Una lista de incidentes sueltos se mira una vez
    y se abandona; «esta frase falló 7 veces» se arregla, porque el número dice
    solo cuál vale la pena.

    Y por eso también va ordenado: lo primero de cada lista es lo próximo que
    conviene tocar.
    """
    vacio = {t: [] for t in TIPOS}
    try:
        pool = await _conexiones()
        async with pool.connection() as c:
            filas = await (await c.execute("""
                select tipo, texto, detalle,
                       min(paso)  as paso,
                       count(*)   as veces,
                       max(cuando) as ultima
                from senales
                where (%s::text is null or business_id = %s::text)
                group by tipo, texto, detalle
                order by count(*) desc, max(cuando) desc
            """, (business_id, business_id))).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.warning("no se pudo leer las señales (%s): %s", type(e).__name__, e)
        return vacio

    for f in filas:
        grupo = vacio.setdefault(f["tipo"], [])
        if len(grupo) < tope:
            grupo.append({"texto": f["texto"], "detalle": f["detalle"],
                          "paso": f["paso"], "veces": f["veces"],
                          "ultima": f["ultima"].isoformat() if f["ultima"] else None})
    return vacio


# ══════════════════════════════════════════════════════════════════
#  Leer
# ══════════════════════════════════════════════════════════════════

VACIO = {"cerradas": 0, "en_curso": 0, "reservadas": 0, "escaladas": 0,
         "abandonadas": 0, "containment": None, "escalacion": None,
         "abandono": None, "abandono_por_paso": {}, "abandono_frases": {},
         "turnos_hasta_reservar": None}


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
                select desenlace, estado_final, turnos, ultimo_en, ultimo_mensaje
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

    # Dónde se cayeron, y QUÉ ESCRIBIERON antes de irse. El paso solo dice el
    # lugar; lo que escribieron dice el motivo, y el motivo es lo único que se
    # puede arreglar.
    por_paso: dict[str, int] = {}
    frases: dict[str, list[str]] = {}
    for f in abandonadas:
        paso = f["estado_final"] or "desconocido"
        por_paso[paso] = por_paso.get(paso, 0) + 1
        ultimo = f.get("ultimo_mensaje")
        if ultimo and ultimo not in frases.setdefault(paso, []):
            frases[paso].append(ultimo)

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
        "abandono_frases": {p: v[:4] for p, v in frases.items()},
        "turnos_hasta_reservar": (int(median(f["turnos"] for f in reservadas))
                                  if reservadas else None),
    }


async def cuellos_de_botella(business_id: str | None = None) -> list[dict]:
    """En qué paso se traba la gente, contado por paso y no por frase.

    Es la misma tabla que `senales`, agregada de otra manera, y las dos hacen
    falta:

      · Por FRASE dice qué enseñarle al bot — «tenés turno pa hoy?» ×6 es una
        línea de código.
      · Por PASO dice dónde está el problema — 20 tropiezos repartidos en veinte
        frases distintas, todos en «elegir el horario», no se arreglan con una
        línea: ahí lo que está mal es el paso.

    Sin esta vista, veinte frases distintas se ven como veinte problemas chicos
    en vez de como uno grande.
    """
    try:
        pool = await _conexiones()
        async with pool.connection() as c:
            filas = await (await c.execute("""
                select s.paso,
                       count(*)                     as tropiezos,
                       count(distinct s.texto)      as frases,
                       max(s.cuando)                as ultima,
                       coalesce(max(p.mensajes), 0) as mensajes
                from senales s
                left join (
                    select business_id, paso, sum(mensajes) as mensajes
                    from pasos
                    where (%s::text is null or business_id = %s::text)
                    group by business_id, paso
                ) p on p.paso = s.paso and p.business_id = s.business_id
                where s.tipo = 'no_entendio' and s.paso is not null
                  and (%s::text is null or s.business_id = %s::text)
                group by s.paso
                order by count(*) desc
            """, (business_id, business_id, business_id, business_id))).fetchall()
        return [{"paso": f["paso"], "tropiezos": f["tropiezos"],
                 "frases": f["frases"], "mensajes": f["mensajes"],
                 # El porcentaje es lo único accionable de la fila: 24 tropiezos
                 # sobre 900 mensajes es ruido; sobre 30 es el paso roto.
                 "falla": (round(f["tropiezos"] / f["mensajes"], 4)
                           if f["mensajes"] else None),
                 "ultima": f["ultima"].isoformat() if f["ultima"] else None}
                for f in filas]
    except Exception as e:  # noqa: BLE001
        logger.warning("no se pudo leer los cuellos (%s): %s", type(e).__name__, e)
        return []


async def negocios() -> list[dict]:
    """Los negocios que tienen datos, con cuánto tiene cada uno.

    Sirve para el selector del tablero. Sale de las dos tablas y no de
    `TENANTS`: lo que interesa mostrar es dónde HAY algo para mirar, no la lista
    de configurados — un negocio dado de alta ayer y sin una sola conversación
    es una pestaña vacía que ensucia.
    """
    try:
        pool = await _conexiones()
        async with pool.connection() as c:
            filas = await (await c.execute("""
                select business_id,
                       count(*) filter (where origen = 'conv')  as conversaciones,
                       count(*) filter (where origen = 'senal') as senales
                from (
                    select business_id, 'conv'  as origen from conversaciones
                    union all
                    select business_id, 'senal' as origen from senales
                ) todo
                group by business_id
                order by count(*) desc
            """)).fetchall()
        return [dict(f) for f in filas]
    except Exception as e:  # noqa: BLE001
        logger.warning("no se pudo listar los negocios (%s): %s", type(e).__name__, e)
        return []


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
            await c.execute("delete from senales where business_id = %s",
                            (business_id,))
            await c.execute("delete from eventos where business_id = %s",
                            (business_id,))
            await c.execute("delete from pasos where business_id = %s",
                            (business_id,))
    except Exception:  # noqa: BLE001
        pass
