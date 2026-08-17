"""
fechas.py — Aritmética de calendario. En código, nunca en el modelo.

Existe por un bug concreto: pedimos "el lunes que viene" y el modelo resolvió
2026-08-23, que era domingo y con el negocio cerrado. El turno se reservó
igual. Los modelos calculan mal los días de la semana, y no hay prompt que lo
arregle de forma confiable.

La regla que salió de ahí: lo determinístico va en código, y al modelo le
queda lo que sí sabe hacer — entender a qué día se refiere alguien cuando
escribe "el finde".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# El huso del negocio, no el del servidor.
#
# El contenedor corre en UTC. `date.today()` ahí devuelve el día siguiente a
# partir de las 21:00 de Argentina, y `datetime.now()` adelanta tres horas.
# Las dos cosas rompen algo distinto y ninguna se nota probando de día:
#
#   - "hoy" pasa a ser mañana → el calendario que ve el clasificador arranca
#     un día corrido, y "mañana" resuelve a pasado mañana.
#   - la hora adelantada → a las 18:00 de Argentina el sistema cree que son
#     las 21:00 y esconde toda la tarde como si ya hubiera pasado.
#
# Por eso NINGÚN archivo de este proyecto llama a `date.today()` ni a
# `datetime.now()` directamente: todos pasan por acá.
TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def ahora() -> datetime:
    """El momento actual en la zona del negocio, con huso."""
    return datetime.now(TZ)


def hoy() -> date:
    """Qué día es para el negocio, que no siempre es el del servidor."""
    return ahora().date()


def calendario(desde: date | None = None, dias: int = 10) -> str:
    """La tabla de fechas ya resueltas que viaja en el prompt del clasificador.

    El modelo no calcula: busca en esta tabla. Convertir "el jueves" a una
    fecha pasa de ser un cálculo a ser una consulta.
    """
    desde = desde or hoy()
    filas = []
    for i in range(dias):
        d = desde + timedelta(days=i)
        etiqueta = {0: " (hoy)", 1: " (mañana)", 2: " (pasado mañana)"}.get(i, "")
        filas.append(f"  {d.isoformat()} — {DIAS[d.weekday()]}{etiqueta}")
    return "\n".join(filas)


def nombre_del_dia(d: date) -> str:
    return DIAS[d.weekday()]
