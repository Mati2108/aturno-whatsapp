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
from datetime import date, datetime, time, timedelta

import httpx

from src.aturno.base import ClienteAturno
# El huso del negocio sale de un solo lugar. El contenedor corre en UTC, y ahí
# `date.today()` devuelve el día siguiente a partir de las 21:00 de Argentina.
from src.fechas import TZ
from src.schemas import (
    Alternativa,
    Consulta,
    DatosDelCliente,
    DiaConCupo,
    Disponibilidad,
    EstadoDelTurno,
    MotivoNoDisponible,
    Profesional,
    Servicio,
    TurnoConfirmado,
)

logger = logging.getLogger("pipeline.aturno.api")

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
            total = len(await self._candidatos(business_id, d, servicio_id, profesional_id))
            salida.append(DiaConCupo(
                fecha=d,
                libres=max(0, total - int(llenos.get(d.isoformat(), 0))),
                abierto=total > 0,
            ))
        return salida

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

    async def crear_turno(self, business_id: str, servicio_id: str, dia: date,
                          hora: time, cliente: DatosDelCliente,
                          profesional_id: str | None = None) -> TurnoConfirmado:
        """Crea el turno de verdad, en la misma agenda que la web.

        Un rechazo NO es una excepción: entre que el bot ofreció el horario y
        la persona confirmó pueden pasar minutos, y en el medio alguien pudo
        tomarlo desde la página. Eso es un resultado normal que el bot tiene
        que poder contar, así que vuelve como RECHAZADO con motivo.
        """
        servicio = await self._servicio_crudo(business_id, servicio_id)
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
            "clientTimeZone": str(TZ),
            # De dónde salió el turno. El negocio lo ve en su panel y es lo que
            # le permite medir si el canal de WhatsApp le sirve.
            "origen": "whatsapp",
        }
        if profesional_id:
            persona = await self._staff_crudo(business_id, profesional_id)
            cuerpo["staff"] = {"id": profesional_id,
                               "name": (persona or {}).get("name")}

        r = await self._http.post(f"{self._base}/api/bookings", json=cuerpo)

        if r.status_code == 201:
            datos = r.json()
            logger.info("turno creado en aturno: %s", datos.get("bookingId"))
            return TurnoConfirmado(
                estado=EstadoDelTurno.CONFIRMADO,
                booking_id=datos.get("bookingId"),
                codigo=datos.get("code"),
                fecha=dia, hora=hora, servicio=servicio.get("name"),
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
