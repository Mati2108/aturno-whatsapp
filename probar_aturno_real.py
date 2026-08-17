"""
probar_aturno_real.py — Ejercita el adaptador contra el aturno de verdad.

Recorre el mismo camino que hace una persona por WhatsApp, pero contra el
backend real, y muestra qué contesta cada paso. Sirve para dos cosas: ver que
la integración anda, y ver los datos del negocio tal como los va a leer el bot.

    python probar_aturno_real.py <slug>              # solo lee, no reserva
    python probar_aturno_real.py <slug> --reservar   # CREA UN TURNO DE VERDAD

El `--reservar` está separado a propósito. Sin él esto no escribe nada: se
puede correr las veces que haga falta sin ensuciar la agenda del negocio. Con
él, el turno queda en la agenda real y hay que borrarlo desde el panel.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, timedelta

from src.aturno.api import TZ, AturnoAPI, _ahora
from src.config import config
from src.schemas import DatosDelCliente

logging.basicConfig(level=logging.WARNING, format="%(message)s")

TELEFONO_DE_PRUEBA = "+5491130032002"


def titulo(t: str) -> None:
    print(f"\n{'─' * 62}\n{t}\n{'─' * 62}")


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    slug = sys.argv[1]
    reservar = "--reservar" in sys.argv
    url = config().aturno_api_url

    print(f"\nnegocio : {slug}")
    print(f"backend : {url}")
    print(f"hoy     : {_ahora():%Y-%m-%d %H:%M} ({TZ})")
    if reservar:
        print("MODO    : va a CREAR un turno real")

    api = AturnoAPI(url)
    try:
        # ---- 1. servicios ----
        titulo("1 · Servicios")
        servicios = await api.listar_servicios(slug)
        if not servicios:
            print("  El negocio no tiene servicios cargados. El bot no tiene qué ofrecer.")
            return 1
        for s in servicios:
            print(f"  {s.nombre}  ·  {s.duracion_minutos} min  ·  ${s.precio:,.0f}")
        servicio = servicios[0]

        # ---- 2. profesionales ----
        titulo(f"2 · Quién hace «{servicio.nombre}»")
        personal = await api.listar_personal(slug, servicio.id)
        if personal:
            for p in personal:
                print(f"  {p.nombre}")
        else:
            print("  (sin personal asignado — el turno va sin profesional)")
        profesional = personal[0] if personal else None

        # ---- 3. días ----
        titulo("3 · Próximos 7 días")
        hoy = _ahora().date()
        dias = await api.dias_con_cupo(slug, servicio.id, hoy, 7)
        elegido: date | None = None
        for d in dias:
            if not d.abierto:
                print(f"  {d.fecha:%a %d/%m}  cerrado")
                continue
            print(f"  {d.fecha:%a %d/%m}  {d.libres} libre(s)")
            if elegido is None and d.libres > 0:
                elegido = d.fecha
        if elegido is None:
            print("\n  No hay ni un día con cupo en la semana. Nada más que probar.")
            return 1

        # ---- 4. horarios ----
        titulo(f"4 · Horarios del {elegido:%A %d/%m}")
        disp = await api.consultar_disponibilidad(slug, servicio.id, elegido)
        if not disp.horarios:
            print("  Sin horarios libres.")
            return 1
        print("  " + "  ".join(h.strftime("%H:%M") for h in disp.horarios[:12]))
        hora = disp.horarios[0]

        # ---- 5. ¿se puede este exacto? ----
        titulo(f"5 · ¿Se puede a las {hora:%H:%M}?")
        c = await api.consultar_pedido(slug, servicio.id, elegido, hora)
        print(f"  disponible: {c.disponible}   motivo: {c.motivo}")

        # y uno que NO debería poder: un horario fuera de agenda
        c2 = await api.consultar_pedido(slug, servicio.id, elegido, hora.replace(hour=4, minute=0))
        print(f"  a las 04:00 → disponible: {c2.disponible}   motivo: {c2.motivo}")
        if c2.disponible:
            print("  ⚠ ofreció un horario a las 4 de la mañana: revisar los tramos")

        # ---- 6. reservar ----
        titulo("6 · Reserva")
        if not reservar:
            print("  (salteado — agregá --reservar para crear el turno de verdad)")
            return 0

        turno = await api.crear_turno(
            slug, servicio.id, elegido, hora,
            DatosDelCliente(nombre="Prueba WhatsApp", telefono=TELEFONO_DE_PRUEBA),
            profesional_id=profesional.id if profesional else None,
        )
        print(f"  estado : {turno.estado.value}")
        print(f"  id     : {turno.booking_id}")
        print(f"  código : {turno.codigo}")
        if turno.motivo_del_rechazo:
            print(f"  motivo : {turno.motivo_del_rechazo}")
        if turno.estado.value == "confirmado":
            print(f"\n  ✓ Buscá «{elegido:%d/%m} {hora:%H:%M}» en el panel de aturno.")
        return 0 if turno.estado.value != "rechazado" else 1

    finally:
        await api.cerrar()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
