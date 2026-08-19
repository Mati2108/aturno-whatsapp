"""
api.py — La implementación real de `ClienteAturno`: pega contra el backend de aturno.

QUÉ CAMBIA RESPECTO DEL DOBLE
-----------------------------
Con `ATURNO_MODO=api`, un turno sacado por WhatsApp queda en la MISMA colección
`bookings` que uno sacado desde la web. Aparece en el panel del negocio sin
sincronizar nada, y el cliente recibe el mismo mail de confirmación. No hay dos
agendas que después haya que conciliar: hay una sola.

POR QUÉ NO HACE FALTA NINGUNA CREDENCIAL
----------------------------------------
Todo lo que necesita este bot ya es público en aturno, porque es exactamente lo
que hace la página de reservas para alguien que no tiene cuenta:

    GET  /api/public/business/{slug}        servicios, staff y horarios
    GET  /api/public/business/{slug}/ocupacion   qué días están llenos
    POST /api/public/horarios-ocupados      cuáles de estos horarios están tomados
    POST /api/bookings/check-availability   ¿se puede exactamente esto?
    POST /api/bookings                      crear el turno

O sea que este servicio es un cliente público más, con los mismos permisos que
un navegador anónimo. Eso es deliberado: si el bot se ve comprometido, el atacante
no gana nada que no pudiera hacer entrando a la página de reservas. No hay
service account, no hay token de admin, no hay clave de Firebase para filtrar.

EL LÍMITE DE ESTE ARCHIVO
-------------------------
Acá NO se decide si un turno se puede dar. Eso lo decide aturno, que es el único
que ve los turnos existentes, los bloqueos y las retenciones por seña. Lo único
que se calcula localmente son los horarios CANDIDATOS —a qué horas podría
haber turno según el horario del negocio y la duración del servicio—, porque
esa cuenta también la hace el navegador: el endpoint de ocupación espera
recibir la lista de horarios a chequear.

Ese cálculo es un port de `generateTimeSlots` y `getStaffSpecificTimeRanges` de
`aturno/src/components/BookingCalendar.jsx`. **Si allá cambian las reglas de
horarios, hay que tocar acá también.** Es la única duplicación del proyecto y
está acá anotada para que no se descubra por un turno mal ofrecido.

IDENTIDAD DE UN NEGOCIO
-----------------------
En este servicio `business_id` es el SLUG de aturno (el de la URL pública), no
el uid de Firebase. El uid se resuelve solo: sale del campo `id` que devuelve
el endpoint público. Un identificador y no dos, y el que se puede leer de una
URL sin entrar al panel.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, datetime, time, timedelta

import httpx

from src.aturno.base import ClienteAturno
# El huso del negocio sale de un solo lugar. El contenedor corre en UTC, y ahí
# `date.today()` devuelve el día siguiente a partir de las 21:00 de Argentina.
from src.fechas import TZ
from src.schemas import (
    Alternativa,
    Consulta,
    Contacto,
    DatosDelCliente,
    DiaConCupo,
    Disponibilidad,
    EstadoDelTurno,
    MotivoNoDisponible,
    Profesional,
    SinLugar,
    Servicio,
    TurnoConfirmado,
)

logger = logging.getLogger("pipeline.aturno.api")


def _senia_estimada(servicio: dict) -> int:
    """La seña, calculada igual que la calcula aturno. Sólo para proponerla.

    Es una copia deliberada de `calcularSenia` (backend/src/senas.js): mismo
    default de 50%, mismo redondeo a peso entero. Existe porque el cuerpo de la
    reserva lleva un `depositAmount` y el endpoint del link también.

    Lo importante es que NO decide nada: el servidor recalcula la seña desde el
    `depositConfig` del servicio real antes de cobrar, descarta lo que le
    mandemos si no coincide, y devuelve el número que efectivamente cobró. Ese
    —y no éste— es el que se le muestra a la persona. Si algún día las dos
    cuentas se separan, el que manda sigue siendo el servidor.
    """
    config = servicio.get("depositConfig") or {}
    try:
        cantidad = float(config.get("amount", 50))
    except (TypeError, ValueError):
        return 0
    if cantidad < 0:
        return 0
    try:
        precio = float(servicio.get("price") or 0)
    except (TypeError, ValueError):
        precio = 0.0
    bruto = precio * cantidad / 100 if config.get("type", "percentage") == "percentage" else cantidad
    return max(0, round(bruto))


def _minutos_hasta(vencimiento: str | None) -> int | None:
    """Cuántos minutos faltan para ese instante ISO, redondeando hacia abajo.

    Sale de lo que contestó aturno y no de una constante de este lado: la
    retención del horario y el vencimiento del link son el mismo reloj allá, y
    duplicar el número acá es la forma de que algún día dejen de coincidir.
    Devuelve `None` si no vino o no se entiende, y ahí el mensaje simplemente no
    promete un tiempo — mejor que prometer uno equivocado.
    """
    if not vencimiento:
        return None
    try:
        falta = datetime.fromisoformat(vencimiento.replace("Z", "+00:00")) - datetime.now(TZ)
    except (ValueError, TypeError):
        return None
    minutos = int(falta.total_seconds() // 60)
    return minutos if minutos > 0 else None

# Los nombres de día tal como los guarda aturno en `schedule`.
DIAS_JS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Cuánto vale el documento del negocio antes de volver a pedirlo. Cada paso del
# flujo lo necesita (servicios, staff, horarios) y traerlo por paso agregaría
# medio segundo a cada mensaje. Sesenta segundos es más que una conversación y
# menos que cualquier cambio que el negocio haga en su panel.
TTL_NEGOCIO = 60.0


def _ahora() -> datetime:
    return datetime.now(TZ)


def _a_minutos(hhmm: str) -> int:
    h, m = hhmm.split(":")[:2]
    return int(h) * 60 + int(m)


def _intersecar(a: list[dict], b: list[dict]) -> list[dict]:
    """Los tramos en que dos agendas se pisan.

    Es lo que hace `intersectTimeRanges` en el front cuando alguien elige un
    profesional: el turno tiene que entrar en el horario del negocio Y en el de
    esa persona. Sin esto se ofrecen horarios en los que el local está abierto
    pero el profesional no trabaja.
    """
    salida = []
    for r1 in a:
        for r2 in b:
            inicio = max(_a_minutos(r1["start"]), _a_minutos(r2["start"]))
            fin = min(_a_minutos(r1["end"]), _a_minutos(r2["end"]))
            if inicio < fin:
                salida.append(
                    {"start": f"{inicio // 60:02d}:{inicio % 60:02d}",
                     "end": f"{fin // 60:02d}:{fin % 60:02d}"}
                )
    return salida


class AturnoAPI(ClienteAturno):
    """Cliente HTTP del backend de aturno, usando solo endpoints públicos."""

    def __init__(self, base_url: str, timeout: float = 25.0) -> None:
        self._base = base_url.rstrip("/")
        # timeout generoso a propósito: el backend de aturno también está en el
        # plan gratuito de Render y arranca en frío. Un timeout corto acá
        # convierte una demora en un "no pude procesar tu mensaje".
        self._http = httpx.AsyncClient(timeout=timeout)
        self._cache: dict[str, tuple[float, dict]] = {}
        self._candados: dict[str, asyncio.Lock] = {}

    async def cerrar(self) -> None:
        await self._http.aclose()

    # ---------- el documento público del negocio ----------

    async def _negocio(self, slug: str) -> dict:
        """Trae (y cachea) servicios, staff y horarios del negocio.

        El candado por slug evita la estampida: si llegan tres mensajes juntos
        con el caché vencido, sin él salen tres pedidos idénticos a un backend
        que puede estar despertándose.
        """
        ahora = asyncio.get_running_loop().time()
        guardado = self._cache.get(slug)
        if guardado and ahora - guardado[0] < TTL_NEGOCIO:
            return guardado[1]

        candado = self._candados.setdefault(slug, asyncio.Lock())
        async with candado:
            guardado = self._cache.get(slug)
            ahora = asyncio.get_running_loop().time()
            if guardado and ahora - guardado[0] < TTL_NEGOCIO:
                return guardado[1]

            r = await self._http.get(f"{self._base}/api/public/business/{slug}")
            if r.status_code == 404:
                raise LookupError(f"aturno no conoce el negocio '{slug}'")
            r.raise_for_status()
            doc = r.json()
            self._cache[slug] = (ahora, doc)
            logger.info(
                "negocio %s: %d servicios, %d staff",
                slug, len(doc.get("services") or []), len(doc.get("staff") or []),
            )
            return doc

    async def _uid(self, slug: str) -> str:
        """El `businessId` que esperan los endpoints de reserva."""
        return (await self._negocio(slug))["id"]

    async def _servicio_crudo(self, slug: str, servicio_id: str) -> dict:
        for s in (await self._negocio(slug)).get("services") or []:
            if str(s.get("id")) == str(servicio_id):
                return s
        raise LookupError(f"servicio '{servicio_id}' no existe en '{slug}'")

    async def _staff_crudo(self, slug: str, profesional_id: str) -> dict | None:
        for p in (await self._negocio(slug)).get("staff") or []:
            if str(p.get("id")) == str(profesional_id):
                return p
        return None

    # ---------- horarios candidatos ----------

    async def _tramos(self, slug: str, dia: date, servicio: dict,
                      profesional_id: str | None) -> list[dict]:
        """Los tramos en que ese día se podría atender ese servicio."""
        doc = await self._negocio(slug)
        del_dia = (doc.get("schedule") or {}).get(DIAS_JS[dia.weekday()]) or {}
        if not del_dia.get("enabled") or not del_dia.get("ranges"):
            return []
        tramos_negocio = del_dia["ranges"]

        if profesional_id:
            persona = await self._staff_crudo(slug, profesional_id)
            propio = ((persona or {}).get("schedule") or {}).get(DIAS_JS[dia.weekday()]) or {}
            if not propio.get("enabled") or not propio.get("ranges"):
                # Sin horario propio cargado, esa persona no atiende ese día.
                # Es la misma decisión del front: no heredar el del negocio.
                return []
            return _intersecar(propio["ranges"], tramos_negocio)

        return tramos_negocio

    async def _candidatos(self, slug: str, dia: date, servicio_id: str,
                          profesional_id: str | None = None) -> list[time]:
        """A qué horas PODRÍA haber turno, sin mirar quién ya reservó.

        Port de `generateTimeSlots`: se avanza de a una duración de servicio y
        el turno tiene que terminar dentro del tramo. Los horarios ya pasados
        se descartan acá y no en aturno.
        """
        servicio = await self._servicio_crudo(slug, servicio_id)
        paso = int(servicio.get("duration") or 30)
        ahora = _ahora()

        horas: list[time] = []
        for tramo in await self._tramos(slug, dia, servicio, profesional_id):
            t, fin = _a_minutos(tramo["start"]), _a_minutos(tramo["end"])
            while t + paso <= fin:
                h = time(t // 60, t % 60)
                if datetime.combine(dia, h, tzinfo=TZ) > ahora:
                    horas.append(h)
                t += paso
        # Dos tramos pueden generar la misma hora; el front también deduplica.
        return sorted(set(horas))

    # ---------- la interfaz ----------

    async def nombre_visible(self, business_id: str) -> str | None:
        doc = await self._negocio(business_id)
        return (doc.get("businessInfo") or {}).get("name") or doc.get("name")

    async def conocimiento(self, slug: str) -> str:
        """El texto que el negocio cargó en el panel para que el bot conteste.

        Sin caché, al revés que el resto: esto se pide justo después de que
        alguien apretó "Guardar" en el panel, y devolver la versión de hace
        cinco minutos sería devolver exactamente lo que se acaba de cambiar.
        """
        r = await self._http.get(
            f"{self._base}/api/public/business/{slug}/conocimiento")
        if r.status_code >= 400:
            raise RuntimeError(f"aturno devolvió {r.status_code}")
        return (r.json() or {}).get("markdown") or ""

    async def contacto(self, business_id: str) -> Contacto:
        """El contacto del negocio, para derivar a una persona.

        Los datos viven en dos lugares según de qué época sea el documento: la
        raíz y `businessInfo`. Se leen los dos con `businessInfo` primero, que
        es el orden que ya usa el panel de aturno. Leer uno solo hacía que
        negocios viejos aparecieran sin teléfono.
        """
        d = await self._negocio(business_id)
        info = d.get("businessInfo") or {}
        elegir = lambda k: (info.get(k) or d.get(k) or None)  # noqa: E731
        return Contacto(
            telefono=elegir("phone"),
            email=elegir("email"),
            direccion=elegir("address"),
        )

    async def listar_servicios(self, business_id: str) -> list[Servicio]:
        doc = await self._negocio(business_id)
        salida = []
        for s in doc.get("services") or []:
            if not s.get("id") or not s.get("name"):
                continue
            salida.append(Servicio(
                id=str(s["id"]),
                nombre=str(s["name"]),
                duracion_minutos=int(s.get("duration") or 30),
                precio=float(s.get("price") or 0),
                # Para poder avisar la seña en el resumen, antes de reservar.
                requiere_senia=bool(s.get("requiresDeposit")),
                senia=_senia_estimada(s),
            ))
        return salida

    async def listar_personal(self, business_id: str,
                              servicio_id: str | None = None) -> list[Profesional]:
        doc = await self._negocio(business_id)
        activos = [p for p in (doc.get("staff") or []) if p.get("active")]

        if servicio_id:
            # `assignedStaff` vacío significa "lo hacen todos", no "no lo hace
            # nadie". Leerlo al revés dejaría el paso de profesional sin
            # opciones en cualquier negocio que no lo haya configurado.
            servicio = await self._servicio_crudo(business_id, servicio_id)
            asignados = servicio.get("assignedStaff") or []
            if asignados:
                activos = [p for p in activos if str(p.get("id")) in map(str, asignados)]

        servicios_de = {}
        for s in doc.get("services") or []:
            for pid in (s.get("assignedStaff") or []):
                servicios_de.setdefault(str(pid), []).append(str(s["id"]))

        return [
            Profesional(
                id=str(p["id"]),
                nombre=str(p.get("name") or "").strip() or "Sin nombre",
                servicios=servicios_de.get(str(p["id"]), []),
            )
            for p in activos if p.get("id")
        ]

    async def dias_con_cupo(self, business_id: str, servicio_id: str, desde: date,
                            dias: int = 7,
                            profesional_id: str | None = None) -> list[DiaConCupo]:
        """Cuántos horarios quedan libres por día, en un solo pedido.

        `ocupacion[fecha]` que devuelve aturno son los horarios LLENOS de ese
        día, con su misma regla de capacidad. Los candidatos los ponemos
        nosotros; la resta da los libres.
        """
        servicio = await self._servicio_crudo(business_id, servicio_id)
        hasta = desde + timedelta(days=dias - 1)

        params = {"desde": desde.isoformat(), "hasta": hasta.isoformat(),
                  "servicio": servicio.get("name")}
        if profesional_id:
            params["staff"] = profesional_id

        llenos: dict[str, int] = {}
        try:
            r = await self._http.get(
                f"{self._base}/api/public/business/{business_id}/ocupacion",
                params=params,
            )
            r.raise_for_status()
            llenos = r.json().get("ocupacion") or {}
        except Exception:
            # Sin ocupación, todos los días se ven abiertos según el horario.
            # Es una degradación aceptable: el paso siguiente pide los horarios
            # reales y ahí se corrige. Peor sería no mostrar el calendario.
            logger.warning("no se pudo leer la ocupación de %s", business_id, exc_info=True)

        salida = []
        for i in range(dias):
            d = desde + timedelta(days=i)
            candidatos = await self._candidatos(business_id, d, servicio_id, profesional_id)
            libres = max(0, len(candidatos) - int(llenos.get(d.isoformat(), 0)))
            salida.append(DiaConCupo(fecha=d, libres=libres,
                                     motivo=await self._por_que_vacio(
                                         business_id, d, servicio, profesional_id,
                                         candidatos, libres)))
        return salida

    async def _por_que_vacio(self, slug: str, dia: date, servicio: dict,
                             profesional_id: str | None, candidatos: list,
                             libres: int) -> SinLugar | None:
        """Por qué ese día no tiene turnos. Los cuatro motivos son distintos.

        Antes esto era `abierto = hay candidatos`, y por lo tanto un sábado que
        el local abre salía como "cerrado" solo porque el profesional elegido
        no trabaja los sábados. El bot le informaba mal el horario del negocio
        a alguien que después no vuelve.
        """
        if libres > 0:
            return None

        del_dia = (await self._negocio(slug)).get("schedule", {}).get(
            DIAS_JS[dia.weekday()]) or {}
        if not del_dia.get("enabled") or not del_dia.get("ranges"):
            return SinLugar.CERRADO

        # El local abre. ¿Por qué no hay nada, entonces?
        if not await self._tramos(slug, dia, servicio, profesional_id):
            return SinLugar.NO_ATIENDE       # esa persona no trabaja ese día
        if not candidatos:
            return SinLugar.YA_PASO          # había horarios, pero ya pasaron
        return SinLugar.COMPLETO             # los hay y están todos tomados

    async def consultar_disponibilidad(self, business_id: str, servicio_id: str,
                                       dia: date,
                                       profesional_id: str | None = None) -> Disponibilidad:
        """Los horarios realmente libres: candidatos menos los que aturno marca tomados."""
        candidatos = await self._candidatos(business_id, dia, servicio_id, profesional_id)
        if not candidatos:
            return Disponibilidad(fecha=dia, servicio_id=servicio_id,
                                  profesional_id=profesional_id, horarios=[])

        servicio = await self._servicio_crudo(business_id, servicio_id)
        cuerpo = {
            "businessId": await self._uid(business_id),
            "date": dia.isoformat(),
            "horarios": [h.strftime("%H:%M") for h in candidatos],
            "serviceName": servicio.get("name"),
            "duracionMinutos": int(servicio.get("duration") or 30),
        }
        if profesional_id:
            persona = await self._staff_crudo(business_id, profesional_id)
            cuerpo["staffId"] = profesional_id
            cuerpo["staffName"] = (persona or {}).get("name")

        r = await self._http.post(
            f"{self._base}/api/public/horarios-ocupados", json=cuerpo
        )
        if r.status_code == 404:
            # El backend del negocio es anterior a este endpoint. En vez de
            # fallar, se pregunta horario por horario con `check-availability`,
            # que existe desde siempre. Es más lento —un pedido por horario en
            # vez de uno por día— pero la alternativa es que el bot no funcione
            # contra un aturno desactualizado, y quién despliega el backend no
            # es quien despliega el bot.
            libres = await self._libres_uno_por_uno(
                business_id, servicio_id, dia, candidatos, profesional_id)
            logger.info("%s %s: %d candidatos, %d libres (modo compatible)",
                        business_id, dia, len(candidatos), len(libres))
            return Disponibilidad(fecha=dia, servicio_id=servicio_id,
                                  profesional_id=profesional_id, horarios=libres)
        r.raise_for_status()
        ocupados = set(r.json().get("ocupados") or [])

        libres = [h for h in candidatos if h.strftime("%H:%M") not in ocupados]
        logger.info("%s %s: %d candidatos, %d libres",
                    business_id, dia, len(candidatos), len(libres))
        return Disponibilidad(fecha=dia, servicio_id=servicio_id,
                              profesional_id=profesional_id, horarios=libres)

    async def _libres_uno_por_uno(self, slug: str, servicio_id: str, dia: date,
                                  candidatos: list[time],
                                  profesional_id: str | None) -> list[time]:
        """Plan B: preguntar horario por horario cuando falta el endpoint del día.

        La concurrencia va limitada a propósito. Sin tope, un día con veinte
        horarios dispara veinte pedidos simultáneos contra un backend que puede
        estar despertándose, y lo que se gana en paralelismo se pierde en
        timeouts. Seis alcanza para que un día entero resuelva en pocos
        segundos sin castigarlo.
        """
        servicio = await self._servicio_crudo(slug, servicio_id)
        nombre_prof = None
        if profesional_id:
            nombre_prof = (await self._staff_crudo(slug, profesional_id) or {}).get("name")
        uid = await self._uid(slug)
        tope = asyncio.Semaphore(6)

        async def libre(h: time) -> time | None:
            cuerpo = {"businessId": uid, "date": dia.isoformat(),
                      "time": h.strftime("%H:%M"), "serviceId": servicio_id,
                      "serviceName": servicio.get("name")}
            if profesional_id:
                cuerpo["staffId"] = profesional_id
                cuerpo["staffName"] = nombre_prof
            async with tope:
                try:
                    r = await self._http.post(
                        f"{self._base}/api/bookings/check-availability", json=cuerpo)
                    r.raise_for_status()
                    return h if r.json().get("available") else None
                except Exception:
                    # Un horario que no se pudo consultar NO se ofrece. Ofrecer
                    # a ciegas y que rebote al confirmar es peor que mostrar uno
                    # menos: la persona ya eligió y se le cae encima.
                    logger.warning("no se pudo consultar %s %s", dia, h, exc_info=False)
                    return None

        resultados = await asyncio.gather(*(libre(h) for h in candidatos))
        return [h for h in resultados if h is not None]

    async def consultar_pedido(self, business_id: str, servicio_id: str, dia: date,
                               hora: time,
                               profesional_id: str | None = None) -> Consulta:
        """¿Se puede exactamente esto? Y si no, por qué y qué hay cerca.

        El motivo se arma en dos etapas porque el backend devuelve un texto de
        conflicto que no distingue "cerrado" de "ocupado", y ésa es justo la
        diferencia que le importa a la persona: "los martes no abrimos" y
        "las 15 ya están tomadas" piden respuestas distintas.
        """
        # ¿Esa persona hace ese servicio?
        if profesional_id:
            habilitados = await self.listar_personal(business_id, servicio_id)
            if not any(p.id == str(profesional_id) for p in habilitados):
                return Consulta(disponible=False,
                                motivo=MotivoNoDisponible.PROFESIONAL_NO_HACE,
                                alternativas=[])

        # ¿El negocio abre ese día?
        servicio = await self._servicio_crudo(business_id, servicio_id)
        if not await self._tramos(business_id, dia, servicio, profesional_id):
            return Consulta(disponible=False, motivo=MotivoNoDisponible.CERRADO,
                            alternativas=await self._cercanas(
                                business_id, servicio_id, dia, hora, profesional_id))

        # ¿Y a esa hora?
        candidatos = await self._candidatos(business_id, dia, servicio_id, profesional_id)
        if hora not in candidatos:
            return Consulta(disponible=False,
                            motivo=MotivoNoDisponible.FUERA_DE_HORARIO,
                            alternativas=await self._cercanas(
                                business_id, servicio_id, dia, hora, profesional_id))

        # Recién acá pregunta aturno, que es el único que ve las reservas.
        cuerpo = {
            "businessId": await self._uid(business_id),
            "date": dia.isoformat(),
            "time": hora.strftime("%H:%M"),
            "serviceId": servicio_id,
            "serviceName": servicio.get("name"),
        }
        if profesional_id:
            persona = await self._staff_crudo(business_id, profesional_id)
            cuerpo["staffId"] = profesional_id
            cuerpo["staffName"] = (persona or {}).get("name")

        r = await self._http.post(
            f"{self._base}/api/bookings/check-availability", json=cuerpo
        )
        r.raise_for_status()
        datos = r.json()

        if datos.get("available"):
            return Consulta(disponible=True, motivo=None, alternativas=[])

        return Consulta(
            disponible=False,
            motivo=(MotivoNoDisponible.PROFESIONAL_OCUPADO if profesional_id
                    else MotivoNoDisponible.OCUPADO),
            alternativas=await self._cercanas(
                business_id, servicio_id, dia, hora, profesional_id),
        )

    async def _cercanas(self, business_id: str, servicio_id: str, dia: date,
                        hora: time, profesional_id: str | None) -> list[Alternativa]:
        """Los horarios libres más próximos al que pidió, ese mismo día.

        Ordenados por distancia y no por hora: quien pidió las 15 prefiere las
        14:30 antes que las 9, aunque las 9 vengan primero en la lista.
        """
        try:
            disp = await self.consultar_disponibilidad(
                business_id, servicio_id, dia, profesional_id)
        except Exception:
            logger.warning("no se pudieron buscar alternativas", exc_info=True)
            return []

        pedido = hora.hour * 60 + hora.minute
        nombre = None
        if profesional_id:
            nombre = (await self._staff_crudo(business_id, profesional_id) or {}).get("name")

        cercanas = sorted(
            disp.horarios, key=lambda h: abs(h.hour * 60 + h.minute - pedido)
        )[:3]
        return [
            Alternativa(
                fecha=dia, hora=h,
                profesional_id=profesional_id, profesional_nombre=nombre,
                distancia_minutos=abs(h.hour * 60 + h.minute - pedido),
            )
            for h in cercanas
        ]

    async def _quien_atiende(self, slug: str, servicio_id: str, dia: date,
                             hora: time) -> str | None:
        """Uno de los que estén libres a esa hora, al azar.

        AL AZAR Y NO EL PRIMERO, que es lo que hacía antes. Con dos personas
        libres a la misma hora, "el primero" significa que quien encabeza la
        lista se lleva TODOS los turnos de quien dice "me da igual" — un sesgo
        que mete el canal y que el negocio no eligió. Repartir parejo entre los
        que pueden atender es lo más cercano a no decidir nada.

        Es el único lugar del sistema donde el azar es correcto. En todo lo
        demás —qué paso sigue, qué texto sale, qué horarios se ofrecen— la
        variabilidad es un defecto; acá es justamente lo que se busca.

        Se consultan todos en paralelo. Secuencial cortaría en el primero libre
        —más rápido— pero es exactamente el sesgo que este método existe para
        evitar.

        Si ninguno está libre devuelve None y el turno se crea sin asignar. Es
        un caso raro, porque el horario se ofreció porque había lugar, y sigue
        siendo mejor que perder la reserva.
        """
        gente = await self.listar_personal(slug, servicio_id)
        if not gente:
            return None

        async def libre(persona) -> bool:
            try:
                consulta = await self.consultar_pedido(
                    slug, servicio_id, dia, hora, persona.id)
                return consulta.disponible
            except Exception:  # noqa: BLE001
                return False

        disponibles = [
            persona
            for persona, esta in zip(gente, await asyncio.gather(
                *(libre(p) for p in gente)))
            if esta
        ]
        if not disponibles:
            logger.warning("nadie libre a las %s del %s: el turno va sin asignar",
                           hora, dia)
            return None

        elegido = random.choice(disponibles)
        logger.info("«me da igual» → %s (sorteado entre %d libre(s))",
                    elegido.nombre, len(disponibles))
        return elegido.id

    async def _donde_atiende(self, slug: str, servicio: dict, dia: date,
                             hora: time) -> dict | None:
        """El lugar donde se da el turno: id y nombre, o None si no hay.

        Mismo problema que tenía el profesional: se mandaba `resource: null` y
        el turno quedaba sin lugar asignado. Con un solo local no se nota; con
        dos consultorios o dos sillones, el negocio no sabe cuál reservó.

        NO se usa `availableResources` de `check-availability`, que sería lo
        natural: ese campo viene siempre vacío porque el backend lo calcula
        leyendo `serviceData.resources` y el campo real se llama
        `assignedResources` (aturno, server.js). Está anotado en PENDIENTES;
        mientras tanto se resuelve acá, igual que lo hace la página pública.

        El sorteo es por lo mismo que en el profesional: con dos lugares
        libres, "el primero" haría que uno se use siempre y el otro nunca.
        """
        doc = await self._negocio(slug)
        activos = [r for r in (doc.get("resources") or []) if r.get("active")]
        if not activos:
            return None

        # `assignedResources` es el nombre en los datos; `assignedLocation` es
        # el que lee el frontend. Se miran los dos: cuál esté cargado depende
        # de la época del documento, y leer uno solo deja negocios sin lugar.
        asignados = servicio.get("assignedResources") or servicio.get("assignedLocation")
        if asignados:
            activos = [r for r in activos if str(r.get("id")) in map(str, asignados)]
        if not activos:
            return None

        # Los que trabajan ese día a esa hora, según su propio horario.
        del_dia = DIAS_JS[dia.weekday()]
        minutos = hora.hour * 60 + hora.minute
        sirven = []
        for r in activos:
            agenda = (r.get("schedule") or {}).get(del_dia) or {}
            if not agenda.get("enabled"):
                continue
            if any(_a_minutos(x["start"]) <= minutos < _a_minutos(x["end"])
                   for x in (agenda.get("ranges") or [])):
                sirven.append(r)

        # Sin horario propio cargado no se descarta: un recurso sin agenda es
        # "está siempre", que es lo que pasa con un local. Descartarlo dejaría
        # sin lugar a la mayoría de los negocios, que nunca lo configuran.
        if not sirven:
            sirven = [r for r in activos if not (r.get("schedule") or {})]
        if not sirven:
            return None

        elegido = random.choice(sirven)
        return {"id": elegido.get("id"), "name": elegido.get("name")}

    async def crear_turno(self, business_id: str, servicio_id: str, dia: date,
                          hora: time, cliente: DatosDelCliente,
                          profesional_id: str | None = None) -> TurnoConfirmado:
        """Crea el turno de verdad, en la misma agenda que la web.

        Un rechazo NO es una excepción: entre que el bot ofreció el horario y
        la persona confirmó pueden pasar minutos, y en el medio alguien pudo
        tomarlo desde la página. Eso es un resultado normal que el bot tiene
        que poder contar, así que vuelve como RECHAZADO con motivo.

        SERVICIOS CON SEÑA
        ------------------
        Si el servicio pide depósito, el turno NO nace confirmado: nace en
        `pending_deposit`, que aparta el horario mientras la persona paga y lo
        suelta solo si no paga. Hasta ahora el bot no mandaba `depositInfo`, así
        que aturno lo leía como un turno sin seña y lo daba por firme — o sea
        que **por WhatsApp se salteaba el depósito que la web sí cobra**.

        `requiresDeposit` sale del servicio REAL de la base, no de nada que
        arme el bot: es el negocio el que decide si su servicio se seña.
        """
        servicio = await self._servicio_crudo(business_id, servicio_id)

        # Si la persona dijo "me da igual", ACÁ se decide quién atiende.
        #
        # Antes se mandaba `staff: null` y aturno lo guardaba así: el turno
        # quedaba sin profesional asignado y el negocio tenía que resolverlo a
        # mano mirando su agenda. La página pública no hace eso —elige uno
        # libre antes de reservar— y el bot tiene que comportarse igual, o los
        # turnos que entran por WhatsApp se distinguen por estar incompletos.
        if profesional_id is None:
            profesional_id = await self._quien_atiende(
                business_id, servicio_id, dia, hora)

        cuerpo: dict = {
            "businessId": await self._uid(business_id),
            "service": {
                "id": servicio_id,
                "name": servicio.get("name"),
                "duration": int(servicio.get("duration") or 30),
                "price": servicio.get("price"),
            },
            "date": dia.isoformat(),
            "time": hora.strftime("%H:%M"),
            "customer": {
                "name": cliente.nombre,
                "phone": cliente.telefono,
                "email": cliente.email or "",
            },
            # El backend lee el profesional con dos nombres distintos según de
            # dónde venga la reserva. Mandamos el plano, que es el que usa la
            # reserva sin seña — el mismo camino que recorre este bot.
            "staff": None,
            # El lugar, resuelto igual que el profesional. Sin esto el turno
            # entra sin local asignado y el negocio no sabe dónde atenderlo.
            "resource": await self._donde_atiende(business_id, servicio, dia, hora),
            "clientTimeZone": str(TZ),
            # De dónde salió el turno. El negocio lo ve en su panel y es lo que
            # le permite medir si el canal de WhatsApp le sirve.
            "origen": "whatsapp",
        }
        if profesional_id:
            persona = await self._staff_crudo(business_id, profesional_id)
            cuerpo["staff"] = {"id": profesional_id,
                               "name": (persona or {}).get("name")}

        # ---- ¿Este servicio se seña? ----
        #
        # `requiresDeposit` viaja adentro de `service`, y aturno lo lee de ahí
        # —del cuerpo del request— para decidir el estado inicial. O sea que
        # omitirlo alcanza para saltear la seña: por eso se copia del servicio
        # real y no se deja que lo arme nadie más.
        #
        # `depositInfo.status = 'pending_payment'` es lo que hace que la reserva
        # nazca en `pending_deposit` en vez de firme (ver `estadoInicial` en
        # backend/src/reservas.js). El monto que va acá es informativo: el
        # servidor lo recalcula desde el `depositConfig` del servicio antes de
        # cobrar, y si no coinciden manda el suyo y deja el aviso en el log.
        con_senia = bool(servicio.get("requiresDeposit"))
        if con_senia:
            cuerpo["service"]["requiresDeposit"] = True
            cuerpo["depositInfo"] = {
                "status": "pending_payment",
                "depositAmount": _senia_estimada(servicio),
                "paymentMethod": {"id": "mercadopago", "name": "MercadoPago"},
            }

        r = await self._http.post(f"{self._base}/api/bookings", json=cuerpo)

        if r.status_code == 201:
            datos = r.json()
            logger.info("turno creado en aturno: %s", datos.get("bookingId"))
            base = dict(
                booking_id=datos.get("bookingId"),
                codigo=datos.get("code"),
                fecha=dia, hora=hora, servicio=servicio.get("name"),
                # Quién quedó asignado, aunque la persona haya dicho que le
                # daba igual: es la información que necesita cuando llega.
                profesional=(cuerpo.get("staff") or {}).get("name"),
            )
            if not con_senia:
                return TurnoConfirmado(estado=EstadoDelTurno.CONFIRMADO, **base)

            # El turno existe y tiene el horario apartado, pero todavía no es de
            # nadie. Falta el link, que es lo único que la persona puede hacer.
            pago = await self._link_de_senia(datos.get("bookingId"), cliente, servicio)
            if pago is not None:
                # El plazo que se le promete a la persona sale de la RESERVA, no
                # de la respuesta del link.
                #
                # `holdExpiresAt` es el campo que de verdad decide cuándo se
                # suelta el horario; el `expiresAt` del link es otro número, y
                # medido contra producción decía 30 minutos cuando la retención
                # duraba 5. O sea que el bot prometía seis veces el tiempo que
                # la persona tenía para pagar.
                #
                # Ese `expiresAt` está arreglado del lado de aturno, pero el
                # arreglo bueno es este: leer el dato de donde se decide y no de
                # donde se repite. Así el mensaje dice la verdad aunque el
                # backend todavía no esté desplegado, y sigue diciéndola si
                # mañana cambia la duración.
                minutos = await self._minutos_apartado(
                    business_id, datos.get("code"))
                if minutos is not None:
                    pago["minutos"] = minutos
            if pago is None:
                # Sin link no hay forma de pagar, así que no se promete un turno
                # que no existe: vuelve como rechazado y se le ofrece la web.
                #
                # La reserva creada NO se cancela a mano: quedó en
                # `pending_deposit` con su vencimiento, así que suelta el
                # horario sola. Es exactamente para esto que la retención vence
                # —y el endpoint público de cancelar exige dos horas de
                # anticipación, así que para un turno de hoy no serviría.
                logger.warning("seña sin link para %s: el turno queda sin confirmar",
                               datos.get("bookingId"))
                return TurnoConfirmado(
                    estado=EstadoDelTurno.RECHAZADO,
                    fecha=dia, hora=hora, servicio=servicio.get("name"),
                    motivo_del_rechazo="no_se_pudo_cobrar_la_senia",
                )
            return TurnoConfirmado(
                estado=EstadoDelTurno.PENDIENTE_DE_SENA,
                senia=pago["monto"], link_de_pago=pago["link"],
                minutos_de_retencion=pago["minutos"], **base,
            )

        motivo = "Ese horario ya no está disponible"
        try:
            cuerpo_error = r.json()
            motivo = cuerpo_error.get("error") or motivo
        except Exception:
            pass
        logger.warning("aturno rechazó el turno (%d): %s", r.status_code, motivo)
        return TurnoConfirmado(
            estado=EstadoDelTurno.RECHAZADO,
            fecha=dia, hora=hora, servicio=servicio.get("name"),
            motivo_del_rechazo=motivo,
        )

    async def _minutos_apartado(self, business_id: str, codigo: str | None) -> int | None:
        """Cuántos minutos queda apartado el horario, según la propia reserva.

        Sale de `holdExpiresAt`, que es el campo con el que aturno decide si la
        retención sigue viva (`ocupaElHorario` lo compara contra el instante de
        cada consulta). Cualquier otro número que se le diga a la persona es una
        promesa que no la respalda nadie.

        Es una consulta más por reserva con seña, y se paga con gusto: es el
        único momento en que el bot le pone un reloj a alguien, y equivocarse ahí
        significa que la persona deja de apurarse creyendo que le sobra tiempo.

        `None` si no se puede saber, y ahí quien llama se queda con lo que haya:
        el mensaje sabe decir "un rato" cuando no hay número.
        """
        if not codigo:
            return None
        try:
            r = await self._http.get(
                f"{self._base}/api/bookings/by-code/{codigo}",
                params={"businessId": await self._uid(business_id)},
            )
            reserva = (r.json() or {}).get("booking") or {}
        except Exception:  # noqa: BLE001 — mejor sin número que con uno inventado
            logger.warning("no se pudo leer la retención de %s", codigo, exc_info=True)
            return None
        return _minutos_hasta(reserva.get("holdExpiresAt"))

    async def senia_pagada(self, business_id: str, codigo: str) -> bool | None:
        """Le pregunta a aturno si la reserva de ese código ya dejó de esperar.

        Se consulta por CÓDIGO y no por id de reserva porque el endpoint por
        código es público —el código es la credencial que el cliente ya tiene— y
        este servicio no maneja credenciales de Firebase. El mismo camino que
        usa la persona para ver su turno.

        Lo que se mira es el estado: `pending_deposit` es "sigue esperando", y
        cualquier otro estado vivo significa que el pago entró y aturno la
        movió. Ante cualquier duda —red, 404, un cuerpo raro— devuelve `None`,
        que quien llama sabe distinguir de "todavía no".
        """
        if not codigo:
            return None
        try:
            r = await self._http.get(
                f"{self._base}/api/bookings/by-code/{codigo}",
                params={"businessId": await self._uid(business_id)},
            )
        except Exception:  # noqa: BLE001 — no saber no es saber que no pagó
            logger.warning("no se pudo consultar la seña de %s", codigo, exc_info=True)
            return None

        if r.status_code != 200:
            logger.info("consulta de seña %s devolvió %d", codigo, r.status_code)
            return None
        try:
            reserva = (r.json() or {}).get("booking") or {}
        except Exception:  # noqa: BLE001
            return None

        estado = reserva.get("status")
        if not estado:
            return None
        if estado == "pending_deposit":
            return False
        if estado in ("cancelled", "rejected"):
            # No está esperando, pero tampoco se pagó. Se trata como "no sé":
            # avisar "listo, confirmado" por un turno cancelado sería peor que
            # no decir nada.
            logger.info("la reserva %s terminó en %s", codigo, estado)
            return None
        return True

    async def _link_de_senia(self, booking_id: str | None, cliente: DatosDelCliente,
                             servicio: dict) -> dict | None:
        """Pide el link de Mercado Pago para la seña de esta reserva.

        Devuelve `None` ante cualquier problema, y eso NO es tragar el error: es
        un resultado posible y frecuente. El link se genera con el token de
        Mercado Pago del negocio, que puede no estar conectado, haber vencido, o
        estar caído del lado de Mercado Pago. Quien llama decide qué hacer —y lo
        que hace es no prometer el turno.

        El monto que devuelve es el que calculó el SERVIDOR, no el que mandamos:
        `create-link` recalcula la seña desde el `depositConfig` del servicio
        real y avisa en su log si el cliente propuso otra cosa. Mostrar el
        nuestro cuando el servidor cobró otro sería la peor variante posible.
        """
        if not booking_id:
            return None
        try:
            r = await self._http.post(
                f"{self._base}/api/payments/mercadopago/create-link",
                json={
                    "bookingId": booking_id,
                    "depositAmount": _senia_estimada(servicio),
                    "description": f"Seña - {servicio.get('name') or 'Turno'}",
                    "customerName": cliente.nombre or "",
                    "customerEmail": cliente.email or "",
                    "customerPhone": cliente.telefono or "",
                },
            )
        except Exception:  # noqa: BLE001 — sin link, el turno no se promete
            logger.exception("no se pudo pedir el link de seña de %s", booking_id)
            return None

        if r.status_code != 200:
            # El 400 con `motivo` es el caso interesante: Mercado Pago no está
            # disponible para ESTE negocio (sin conectar, o token vencido). Va al
            # log con el motivo porque es algo que el dueño tiene que arreglar en
            # su panel, y no hay ninguna otra señal de que esté pasando.
            detalle = ""
            try:
                detalle = str(r.json())[:160]
            except Exception:
                detalle = r.text[:160]
            logger.error("create-link devolvió %d para %s: %s",
                         r.status_code, booking_id, detalle)
            return None

        datos = r.json()
        link = datos.get("paymentLink")
        if not link:
            logger.error("create-link contestó 200 sin link para %s", booking_id)
            return None

        # Cuánto le queda para pagar, leído de lo que contestó el servidor y no
        # de una constante de este lado. La retención del horario y el
        # vencimiento del link son el MISMO reloj en aturno; duplicar el número
        # acá sería la forma de que algún día dejen de coincidir y alguien pague
        # un horario ya liberado.
        minutos = _minutos_hasta(datos.get("expiresAt"))
        return {"link": link,
                "monto": datos.get("depositAmount") or _senia_estimada(servicio),
                "minutos": minutos}
