"""
doble.py — La implementación en memoria del contrato con aturno.

No es un mock de tests: es una implementación real y completa que guarda los
turnos en memoria. Se usa para desarrollar sin levantar el backend, para correr
los tests sin Firestore, y como plan B si la integración falla.

Imita el comportamiento de aturno en lo que importa:
  - los horarios se generan según la duración del servicio, no en grilla fija
  - el negocio tiene horario y hay días cerrados
  - cada profesional tiene su agenda y hace ciertos servicios
  - un turno tomado deja de ofrecerse
  - reservar algo imposible devuelve RECHAZADO con motivo, no una excepción

Lo que NO imita: bloqueos, señas y límites de plan. Esas reglas viven en aturno
y el objetivo del doble es desbloquear el desarrollo, no reimplementarlas.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, time, timedelta

from src.aturno.base import ClienteAturno
from src.fechas import TZ, ahora, hoy
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
    Servicio,
    SinLugar,
    TurnoConfirmado,
)

SERVICIOS_DEMO: dict[str, list[Servicio]] = {
    "demo-peluqueria": [
        Servicio(id="svc-corte", nombre="Corte de pelo", duracion_minutos=30, precio=8000),
        Servicio(id="svc-color", nombre="Coloración", duracion_minutos=90, precio=25000),
        Servicio(id="svc-barba", nombre="Perfilado de barba", duracion_minutos=20, precio=5000),
    ],
    "demo-consultorio": [
        Servicio(id="svc-consulta", nombre="Consulta clínica", duracion_minutos=30, precio=15000),
        Servicio(id="svc-control", nombre="Control", duracion_minutos=20, precio=10000),
    ],
}

# El equipo. En aturno es la subcolección `staff` del negocio; cada persona
# tiene los servicios que sabe hacer, y eso es lo que permite pedir "un corte
# con Lean" y que el sistema sepa si Lean hace cortes.
PERSONAL_DEMO: dict[str, list[Profesional]] = {
    "demo-peluqueria": [
        Profesional(id="p-lean", nombre="Lean", servicios=["svc-corte", "svc-barba"]),
        Profesional(id="p-sofi", nombre="Sofi", servicios=["svc-corte", "svc-color"]),
        Profesional(id="p-nico", nombre="Nico"),  # sin lista = hace todos
    ],
    "demo-consultorio": [
        Profesional(id="p-dra-ruiz", nombre="Dra. Ruiz"),
    ],
}

CONTACTO_DEMO: dict[str, Contacto] = {
    "demo-peluqueria": Contacto(telefono="+541130032002", direccion="Av. Siempreviva 742"),
    "demo-consultorio": Contacto(telefono="+541130032003"),
}

# Horario por día de la semana (0=lunes … 6=domingo). None = cerrado.
# Tiene que coincidir con el .md del negocio: si el documento del RAG y la
# disponibilidad real se contradicen, el bot informa un horario y reserva otro.
HORARIOS: dict[int, tuple[time, time] | None] = {
    0: (time(9, 0), time(18, 0)),
    1: (time(9, 0), time(18, 0)),
    2: (time(9, 0), time(18, 0)),
    3: (time(9, 0), time(18, 0)),
    4: (time(9, 0), time(18, 0)),
    5: (time(9, 0), time(13, 0)),   # sábado, medio día
    6: None,                        # domingo cerrado
}


class AturnoDoble(ClienteAturno):
    """Guarda los turnos en memoria, keyeados por negocio y profesional."""

    def __init__(
        self,
        servicios: dict[str, list[Servicio]] | None = None,
        personal: dict[str, list[Profesional]] | None = None,
    ) -> None:
        self._servicios = servicios if servicios is not None else SERVICIOS_DEMO
        self._personal = personal if personal is not None else PERSONAL_DEMO
        # (business_id, profesional_id, fecha, hora) -> booking_id
        # El profesional entra en la clave: dos personas pueden atender a la
        # misma hora, que es justamente para lo que existe tener equipo.
        self._ocupados: dict[tuple[str, str, date, time], str] = {}

    async def contacto(self, business_id: str) -> Contacto:
        """Contacto de demostración, para poder probar la derivación sin red."""
        return CONTACTO_DEMO.get(business_id, Contacto())

    # ---------- Catálogo ----------

    async def listar_servicios(self, business_id: str) -> list[Servicio]:
        return list(self._servicios.get(business_id, []))

    async def listar_personal(
        self, business_id: str, servicio_id: str | None = None
    ) -> list[Profesional]:
        """El equipo. Si pasás un servicio, solo quienes lo hacen."""
        gente = list(self._personal.get(business_id, []))
        if servicio_id:
            gente = [p for p in gente if p.atiende(servicio_id)]
        return gente

    # ---------- Disponibilidad ----------

    async def consultar_disponibilidad(
        self,
        business_id: str,
        servicio_id: str,
        dia: date,
        profesional_id: str | None = None,
    ) -> Disponibilidad:
        servicio = await self._buscar_servicio(business_id, servicio_id)
        if servicio is None:
            return Disponibilidad(fecha=dia, servicio_id=servicio_id, horarios=[])

        equipo = await self._equipo_para(business_id, servicio_id, profesional_id)
        libres = [
            h
            for h in self._grilla(servicio.duracion_minutos, dia)
            if any((business_id, p.id, dia, h) not in self._ocupados for p in equipo)
        ]
        return Disponibilidad(
            fecha=dia,
            servicio_id=servicio_id,
            profesional_id=profesional_id,
            horarios=libres,
        )

    async def dias_con_cupo(
        self,
        business_id: str,
        servicio_id: str,
        desde: date,
        dias: int = 7,
        profesional_id: str | None = None,
    ) -> list[DiaConCupo]:
        """Cuántos turnos quedan por día. Para mostrar antes de que elija el día.

        Es lo que hace la página de aturno: se ve de un vistazo dónde hay lugar
        en vez de ir preguntando día por día.
        """
        resultado: list[DiaConCupo] = []
        for i in range(dias):
            d = desde + timedelta(days=i)
            abre = HORARIOS.get(d.weekday()) is not None
            disp = await self.consultar_disponibilidad(
                business_id, servicio_id, d, profesional_id
            )
            libres = len(disp.horarios)
            if libres:
                motivo = None
            elif not abre:
                motivo = SinLugar.CERRADO
            elif not self._grilla(30, d):
                motivo = SinLugar.YA_PASO
            else:
                motivo = SinLugar.COMPLETO
            resultado.append(DiaConCupo(fecha=d, libres=libres, motivo=motivo))
        return resultado

    async def consultar_pedido(
        self,
        business_id: str,
        servicio_id: str,
        dia: date,
        hora: time,
        profesional_id: str | None = None,
    ) -> Consulta:
        """¿Se puede dar exactamente esto? Y si no, ¿por qué y qué hay cerca?

        Es el corazón de "quiero un corte con Lean el martes a las 15": en vez
        de un sí/no, devuelve el MOTIVO del no y las opciones más parecidas.
        Un bot que solo sabe decir "no hay" obliga a la persona a adivinar.
        """
        servicio = await self._buscar_servicio(business_id, servicio_id)
        if servicio is None:
            return Consulta(disponible=False, motivo=MotivoNoDisponible.CERRADO)

        # ¿La persona pedida hace este servicio?
        if profesional_id:
            quien = await self._buscar_profesional(business_id, profesional_id)
            if quien is None or not quien.atiende(servicio_id):
                return Consulta(
                    disponible=False,
                    motivo=MotivoNoDisponible.PROFESIONAL_NO_HACE,
                    alternativas=await self._cercanas(
                        business_id, servicio_id, dia, hora, None
                    ),
                )

        grilla = self._grilla(servicio.duracion_minutos, dia)

        if not grilla:
            return Consulta(
                disponible=False,
                motivo=MotivoNoDisponible.CERRADO,
                alternativas=await self._cercanas(
                    business_id, servicio_id, dia, hora, profesional_id
                ),
            )

        if hora not in grilla:
            return Consulta(
                disponible=False,
                motivo=MotivoNoDisponible.FUERA_DE_HORARIO,
                alternativas=await self._cercanas(
                    business_id, servicio_id, dia, hora, profesional_id
                ),
            )

        equipo = await self._equipo_para(business_id, servicio_id, profesional_id)
        hay_alguien = any(
            (business_id, p.id, dia, hora) not in self._ocupados for p in equipo
        )
        if hay_alguien:
            return Consulta(disponible=True)

        return Consulta(
            disponible=False,
            motivo=(
                MotivoNoDisponible.PROFESIONAL_OCUPADO
                if profesional_id
                else MotivoNoDisponible.OCUPADO
            ),
            alternativas=await self._cercanas(
                business_id, servicio_id, dia, hora, profesional_id
            ),
        )

    async def _cercanas(
        self,
        business_id: str,
        servicio_id: str,
        dia: date,
        hora: time,
        profesional_id: str | None,
        cuantas: int = 4,
    ) -> list[Alternativa]:
        """Las opciones más parecidas a lo pedido, ordenadas por cercanía.

        Busca en una ventana de días alrededor del pedido y ordena por
        distancia en minutos respecto del momento deseado. Así "el viernes a
        las 15" que está ocupado devuelve primero las 14:30 del mismo viernes,
        no un martes cualquiera.
        """
        deseado = datetime.combine(dia, hora)
        candidatas: list[Alternativa] = []

        for delta in range(0, 8):
            d = dia + timedelta(days=delta)
            if d < hoy():
                continue
            disp = await self.consultar_disponibilidad(
                business_id, servicio_id, d, profesional_id
            )
            for h in disp.horarios:
                distancia = abs(int((datetime.combine(d, h) - deseado).total_seconds() // 60))
                candidatas.append(
                    Alternativa(fecha=d, hora=h, profesional_id=profesional_id,
                                distancia_minutos=distancia)
                )

        candidatas.sort(key=lambda a: a.distancia_minutos)
        return candidatas[:cuantas]

    # ---------- Reservar ----------

    async def crear_turno(
        self,
        business_id: str,
        servicio_id: str,
        dia: date,
        hora: time,
        cliente: DatosDelCliente,
        profesional_id: str | None = None,
    ) -> TurnoConfirmado:
        servicio = await self._buscar_servicio(business_id, servicio_id)
        if servicio is None:
            return TurnoConfirmado(
                estado=EstadoDelTurno.RECHAZADO,
                motivo_del_rechazo="Ese servicio no existe en este negocio.",
            )

        consulta = await self.consultar_pedido(
            business_id, servicio_id, dia, hora, profesional_id
        )
        if not consulta.disponible:
            return TurnoConfirmado(
                estado=EstadoDelTurno.RECHAZADO,
                motivo_del_rechazo=_explicar(consulta.motivo, dia, hora),
            )

        # Asignamos a la primera persona libre del equipo habilitado.
        equipo = await self._equipo_para(business_id, servicio_id, profesional_id)
        asignado = next(
            p for p in equipo if (business_id, p.id, dia, hora) not in self._ocupados
        )

        booking_id = f"bk-{secrets.token_hex(6)}"
        self._ocupados[(business_id, asignado.id, dia, hora)] = booking_id

        return TurnoConfirmado(
            estado=EstadoDelTurno.CONFIRMADO,
            booking_id=booking_id,
            codigo=self._codigo(),
            fecha=dia,
            hora=hora,
            servicio=f"{servicio.nombre} con {asignado.nombre}",
        )

    # ---------- internos ----------

    async def _buscar_servicio(self, business_id: str, servicio_id: str) -> Servicio | None:
        for s in await self.listar_servicios(business_id):
            if s.id == servicio_id:
                return s
        return None

    async def _buscar_profesional(
        self, business_id: str, profesional_id: str
    ) -> Profesional | None:
        for p in self._personal.get(business_id, []):
            if p.id == profesional_id:
                return p
        return None

    async def _equipo_para(
        self, business_id: str, servicio_id: str, profesional_id: str | None
    ) -> list[Profesional]:
        """Quiénes pueden atender este servicio; uno solo si lo pidieron."""
        if profesional_id:
            quien = await self._buscar_profesional(business_id, profesional_id)
            return [quien] if quien and quien.atiende(servicio_id) else []
        return await self.listar_personal(business_id, servicio_id)

    @staticmethod
    def _grilla(duracion_minutos: int, dia: date) -> list[time]:
        """Los horarios avanzan de a la duración del servicio, no de a 30 fijos.

        Es como lo hace aturno (`generateTimeSlotsForDay`). Si el día está
        cerrado la lista es vacía, que es una respuesta válida y no un error.

        Los horarios ya pasados quedan afuera. Acá se filtraban solo los DÍAS
        pasados, así que a las 12:55 el bot todavía ofrecía las 09:00 de hoy —
        y las aceptaba. Un turno para una hora que ya pasó no es un detalle
        cosmético: es el sistema mintiendo sobre algo que la persona verifica
        mirando el reloj.
        """
        abierto = HORARIOS.get(dia.weekday())
        if abierto is None:
            return []
        abre, cierra = abierto
        momento_actual = ahora()
        horarios: list[time] = []
        momento = datetime.combine(dia, abre, tzinfo=TZ)
        limite = datetime.combine(dia, cierra, tzinfo=TZ)
        while momento + timedelta(minutes=duracion_minutos) <= limite:
            if momento > momento_actual:
                horarios.append(momento.time())
            momento += timedelta(minutes=duracion_minutos)
        return horarios

    @staticmethod
    def _codigo() -> str:
        """Mismo alfabeto que `backend/src/codigos.js`: sin 0/O/I/1/L.

        Son los caracteres que la gente confunde al leer un código en voz alta,
        y este viaja por WhatsApp para que después lo dicten por teléfono.
        """
        alfabeto = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
        return "".join(secrets.choice(alfabeto) for _ in range(8))


def _explicar(motivo: MotivoNoDisponible | None, dia: date, hora: time) -> str:
    """Traduce el motivo a algo que el bot pueda decirle a una persona."""
    return {
        MotivoNoDisponible.CERRADO: f"El {dia} el negocio está cerrado.",
        MotivoNoDisponible.FUERA_DE_HORARIO: f"A las {hora:%H:%M} el {dia} no atendemos.",
        MotivoNoDisponible.OCUPADO: f"Las {hora:%H:%M} del {dia} ya están tomadas.",
        MotivoNoDisponible.PROFESIONAL_OCUPADO: (
            f"Esa persona ya tiene un turno a las {hora:%H:%M} del {dia}."
        ),
        MotivoNoDisponible.PROFESIONAL_NO_HACE: "Esa persona no hace ese servicio.",
    }.get(motivo, "No se puede dar ese turno.")
