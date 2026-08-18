"""
base.py — El contrato con aturno.

Esta clase abstracta es la pieza que blinda el proyecto. Las tools del agente
hablan SOLO con esta interfaz, nunca con `httpx` ni con la URL de aturno
directamente. Eso permite dos implementaciones intercambiables por variable de
entorno:

    ATURNO_MODO=api    -> api.py, pega contra el backend real de aturno
    ATURNO_MODO=doble  -> doble.py, datos en memoria, sin red

Sirve para tres cosas distintas:
  1. Desarrollar sin depender de que el backend esté levantado.
  2. Testear la lógica del agente sin tocar Firestore.
  3. Plan B: si la integración se rompe, el demo sigue funcionando.

Es el mismo patrón que aturno ya usa en `backend/src/whatsapp.js`, donde el
adaptador existe para que cambiar de proveedor sea "un segundo adaptador y no
cirugía sobre server.js".

TODO lo que sea decidir si un turno se puede dar vive del otro lado, en aturno
(`disponibilidad.js`, `bloqueos.js`, `reservas.js`). Acá no se replica ninguna
regla de negocio: este servicio entiende lo que la persona quiere, aturno
decide si se puede.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, time

from src.schemas import (
    Consulta,
    Contacto,
    DatosDelCliente,
    DiaConCupo,
    Disponibilidad,
    Profesional,
    Servicio,
    TurnoConfirmado,
)


class ClienteAturno(ABC):
    """Contrato común: toda implementación debe resolver estas tres preguntas."""

    async def nombre_visible(self, business_id: str) -> str | None:
        """Cómo se llama el negocio HOY, según aturno.

        El nombre viaja en cada saludo, así que no puede vivir en una tabla de
        este repo: el día que el negocio se renombra en su panel, el bot
        seguiría saludando con el nombre viejo y nadie lo notaría hasta que un
        cliente lo dijera. Devuelve None si no se sabe, y ahí manda el Tenant.
        """
        return None

    async def conocimiento(self, business_id: str) -> str:
        """Lo que el negocio cargó para que el bot pueda contestar preguntas.

        Vacío por defecto, como `contacto`: una implementación que no lo
        resuelva sigue siendo válida —el doble en memoria no tiene panel de
        dónde leerlo— y el bot ya sabe qué contestar cuando no hay nada
        cargado. Hacerlo abstracto rompería el doble para agregar algo que
        puede faltar legítimamente.
        """
        return ""

    async def contacto(self, business_id: str) -> Contacto:
        """Cómo hablar con una persona del negocio.

        No es abstracto y devuelve vacío por defecto a propósito: una
        implementación que no lo resuelva sigue siendo válida, y el bot ya sabe
        qué contestar cuando no hay contacto cargado. Hacerlo obligatorio
        rompería toda implementación existente para agregar un dato que puede
        faltar legítimamente.
        """
        return Contacto()

    @abstractmethod
    async def listar_servicios(self, business_id: str) -> list[Servicio]:
        """Qué vende el negocio, con duración y precio.

        Alimenta tanto la respuesta directa ("¿cuánto sale el corte?") como el
        contexto que necesita el agente para mapear lo que dice la persona a un
        `servicio_id` real.
        """

    @abstractmethod
    async def listar_personal(
        self, business_id: str, servicio_id: str | None = None
    ) -> list[Profesional]:
        """Quiénes atienden. Filtrado por servicio si se pide.

        En aturno es la subcolección `staff`. Permite pedir "un corte con Lean"
        y que el sistema sepa si Lean hace cortes.
        """

    @abstractmethod
    async def dias_con_cupo(
        self,
        business_id: str,
        servicio_id: str,
        desde: date,
        dias: int = 7,
        profesional_id: str | None = None,
    ) -> list[DiaConCupo]:
        """Cuántos turnos quedan por día, para mostrar antes de que elija."""

    @abstractmethod
    async def consultar_pedido(
        self,
        business_id: str,
        servicio_id: str,
        dia: date,
        hora: time,
        profesional_id: str | None = None,
    ) -> Consulta:
        """¿Se puede dar exactamente esto? Si no, por qué y qué hay cerca.

        Devolver el MOTIVO es la diferencia entre un bot que dice "no hay" y
        uno que dice "Lean está ocupado a las 15, pero tiene las 14:30".
        """

    @abstractmethod
    async def consultar_disponibilidad(
        self,
        business_id: str,
        servicio_id: str,
        dia: date,
        profesional_id: str | None = None,
    ) -> Disponibilidad:
        """Qué horarios hay libres ese día para ese servicio.

        La respuesta la manda aturno, que ya cruza turnos existentes, bloqueos
        y retenciones por seña. Una lista vacía es una respuesta válida — el día
        está lleno — y no un error.
        """

    @abstractmethod
    async def crear_turno(
        self,
        business_id: str,
        servicio_id: str,
        dia: date,
        hora: time,
        cliente: DatosDelCliente,
        profesional_id: str | None = None,
    ) -> TurnoConfirmado:
        """Intenta reservar. Puede fallar, y fallar es un resultado normal.

        Entre que el bot ofreció el horario y la persona lo confirmó pueden
        pasar minutos, y en el medio alguien más pudo tomarlo desde la web. Por
        eso el rechazo devuelve `TurnoConfirmado` con estado RECHAZADO y un
        motivo legible, en vez de lanzar una excepción: el agente tiene que
        poder contárselo a la persona y ofrecerle otro horario.
        """
