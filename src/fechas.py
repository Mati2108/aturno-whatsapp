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

from datetime import date, timedelta

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def calendario(desde: date | None = None, dias: int = 10) -> str:
    """La tabla de fechas ya resueltas que viaja en el prompt del clasificador.

    El modelo no calcula: busca en esta tabla. Convertir "el jueves" a una
    fecha pasa de ser un cálculo a ser una consulta.
    """
    desde = desde or date.today()
    filas = []
    for i in range(dias):
        d = desde + timedelta(days=i)
        etiqueta = {0: " (hoy)", 1: " (mañana)", 2: " (pasado mañana)"}.get(i, "")
        filas.append(f"  {d.isoformat()} — {DIAS[d.weekday()]}{etiqueta}")
    return "\n".join(filas)


def nombre_del_dia(d: date) -> str:
    return DIAS[d.weekday()]
