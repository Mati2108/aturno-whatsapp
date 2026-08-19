"""
gasto.py — Cuánto se le está pagando al proveedor del modelo, en vivo.

POR QUÉ EXISTE
--------------
Un día aparecieron 5 dólares gastados sin volumen que los explicara, y para
saber de dónde salían hubo que leer el código y hacer cuentas a mano. La causa
resultó ser el chequeo de salud —`/salud` llamaba al modelo, y Render lo pincha
cada 5 a 10 segundos, o sea entre 8.640 y 17.280 veces por día— pero el problema
de fondo no era ese: era que **el servicio no tenía idea de lo que gastaba**.

Phoenix mide todo esto, pero está apagado en producción (`PHOENIX_HABILITADO`
en `render.yaml`) porque no hay colector desplegado. Así que en el único lugar
donde el gasto importa de verdad no había una sola métrica.

Esto es el reemplazo mínimo: un contador en proceso, sin dependencias, sin red
y sin base. No compite con Phoenix —que sirve para entender una conversación—;
sirve para contestar "¿cuánto llevamos hoy y en qué se fue?" mirando un endpoint.

QUÉ CUENTA Y QUÉ NO
-------------------
Cuenta lo que el proveedor informa en cada respuesta, no una estimación. Los
precios son los de la lista y están abajo: si el modelo no está en la tabla, se
cuentan los tokens igual y el costo queda en cero, porque un número inventado
es peor que ninguno.

Se reinicia con el proceso. No es contabilidad: es un tablero. La factura la
tiene Anthropic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from src.fechas import hoy

logger = logging.getLogger("pipeline.gasto")

# USD por millón de tokens, (entrada, salida). De la lista de precios.
#
# Sólo los modelos que este proyecto puede usar. Uno que no esté acá suma
# tokens y no suma plata: ver el docstring — un costo inventado es peor que
# ninguno, porque se lo cree.
PRECIOS: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
}


def _precio(modelo: str, entrada: int, salida: int) -> float:
    """Lo que costaron estos tokens, o 0 si no sabemos cotizar ese modelo."""
    for nombre, (pe, ps) in PRECIOS.items():
        if nombre in (modelo or ""):
            return entrada / 1e6 * pe + salida / 1e6 * ps
    return 0.0


@dataclass
class Linea:
    """El acumulado de un motivo: para qué se llamó al modelo."""

    llamadas: int = 0
    entrada: int = 0
    salida: int = 0
    usd: float = 0.0

    def sumar(self, entrada: int, salida: int, usd: float) -> None:
        self.llamadas += 1
        self.entrada += entrada
        self.salida += salida
        self.usd += usd


@dataclass
class Gasto:
    """El tablero del día. Se reinicia solo cuando cambia la fecha.

    El corte es por día del NEGOCIO, no del servidor: `src.fechas.hoy` ya
    resuelve el huso, y un tablero que cambia de día a las 21:00 mientras el
    local sigue atendiendo no se puede leer.
    """

    dia: date = field(default_factory=hoy)
    por_motivo: dict[str, Linea] = field(default_factory=dict)

    def _al_dia(self) -> None:
        if hoy() != self.dia:
            self.dia, self.por_motivo = hoy(), {}

    def anotar(self, motivo: str, modelo: str, entrada: int, salida: int) -> None:
        self._al_dia()
        self.por_motivo.setdefault(motivo, Linea()).sumar(
            entrada, salida, _precio(modelo, entrada, salida))

    def usd_hoy(self) -> float:
        self._al_dia()
        return sum(l.usd for l in self.por_motivo.values())

    def resumen(self) -> dict[str, Any]:
        self._al_dia()
        return {
            "dia": self.dia.isoformat(),
            "usd": round(self.usd_hoy(), 6),
            "llamadas": sum(l.llamadas for l in self.por_motivo.values()),
            "por_motivo": {
                m: {"llamadas": l.llamadas, "entrada": l.entrada,
                    "salida": l.salida, "usd": round(l.usd, 6)}
                for m, l in sorted(self.por_motivo.items(),
                                   key=lambda kv: -kv[1].usd)
            },
        }


GASTO = Gasto()

# El motivo por defecto. Que exista uno evita la trampa de este diseño: una
# llamada sin etiquetar no desaparece del total, aparece acá y se nota.
SIN_ETIQUETA = "sin_motivo"


class Contador(BaseCallbackHandler):
    """Suma lo que informa cada respuesta. Se engancha en `construir_modelo`.

    Va como callback y no como envoltorio de cada llamada porque el proyecto
    tiene varios caminos al modelo —el clasificador, sus respaldos, el chequeo
    de salud— y uno nuevo tiene que quedar contado sin que nadie se acuerde de
    sumarlo. `construir_modelo` es el único lugar por el que pasan todos.
    """

    def __init__(self, motivo: str) -> None:
        self._motivo = motivo or SIN_ETIQUETA

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        # NUNCA lanza: contar el gasto no puede romper una conversación. Un
        # tablero que tira abajo lo que mide es peor que no tenerlo — la misma
        # regla que ya sigue `observabilidad.py` con Phoenix.
        try:
            salida = response.llm_output or {}
            modelo = salida.get("model_name") or salida.get("model") or ""
            for generaciones in response.generations:
                for gen in generaciones:
                    uso = getattr(getattr(gen, "message", None), "usage_metadata", None)
                    if not uso:
                        continue
                    GASTO.anotar(self._motivo, modelo,
                                 uso.get("input_tokens", 0), uso.get("output_tokens", 0))
        except Exception:  # noqa: BLE001
            logger.debug("no se pudo contar el gasto", exc_info=True)
