"""
medir_costo.py — Cuánto cuesta, de verdad, cada turno sacado por WhatsApp.

POR QUÉ MEDIRLO Y NO ESTIMARLO
------------------------------
La cuenta de servilleta da mal por dos razones que se compensan al revés de lo
que uno cree: una parte de los mensajes NO llega al modelo (los números se
resuelven en código), y el prompt del sistema se manda entero en cada llamada.
Estimar sin eso da un número inventado.

Acá se cuenta lo que pasó: cuántas veces se llamó al modelo, con cuántos tokens
de entrada y de salida, en una conversación completa de punta a punta.

    python medir_costo.py

Corre contra el doble: lo que se mide es el gasto de tokens, y eso no cambia
según de dónde salgan los horarios.
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.callbacks import AsyncCallbackHandler
from langgraph.checkpoint.memory import MemorySaver

from src.agentes import flujo as F
from src.aturno.doble import AturnoDoble
from src.config import config
from src.fechas import calendario

NEG, TEL = "demo-peluqueria", "+5491130032002"

# Precios de Claude Haiku 4.5, por millón de tokens. Si cambian, se cambian acá
# y el resto de la cuenta sigue valiendo.
USD_ENTRADA_POR_M = 1.00
USD_SALIDA_POR_M = 5.00

# Las conversaciones que se miden. La primera es la más común de todas: alguien
# que toca los números que le ofrecen. La última es el peor caso razonable.
CONVERSACIONES = {
    "Toca los números (lo más común)": [
        "hola", "1", "3", "1", "1", "Ana Pérez", "sí"],
    "Escribe todo en palabras": [
        "hola", "quiero un corte de pelo", "me da igual", "el viernes",
        "a las 10", "Bruno Díaz", "dale"],
    "Pregunta antes de reservar": [
        "hola", "¿cuánto sale el corte?", "¿a qué hora abren?", "1", "3",
        "1", "1", "Carla Gómez", "sí"],
    "Se equivoca y cambia de idea": [
        "hola", "1", "3", "1", "mejor cambio de servicio", "2", "3", "1",
        "1", "Diego Luna", "sí"],
}


class Uso(AsyncCallbackHandler):
    """Anota los tokens que reporta el proveedor en cada llamada.

    Se mide con un callback y no contando tokens a mano porque lo único que
    importa es lo que el proveedor factura, incluido el prompt del sistema que
    viaja en cada llamada — que es justo lo que una estimación se olvida.
    """

    def __init__(self) -> None:
        self.llamadas = 0
        self.entrada = 0
        self.salida = 0

    async def on_llm_end(self, response, **kw) -> None:  # noqa: ANN001
        self.llamadas += 1
        for lote in response.generations:
            for gen in lote:
                uso = getattr(getattr(gen, "message", None), "usage_metadata", None) or {}
                self.entrada += uso.get("input_tokens", 0)
                self.salida += uso.get("output_tokens", 0)


class Medido:
    """Envuelve el clasificador para inyectarle el callback.

    No se toca `flujo.py`: lo que se mide tiene que ser exactamente lo que
    corre en producción, o la medición mide otra cosa.
    """

    def __init__(self, cadena, uso: Uso) -> None:
        self._cadena, self._uso = cadena, uso

    async def ainvoke(self, entrada, *a, **kw):
        return await self._cadena.ainvoke(entrada, config={"callbacks": [self._uso]})


async def main() -> None:
    logging.basicConfig(level=logging.ERROR)
    cfg = config()
    F.configurar(AturnoDoble())

    print(f"\n{'═'*72}")
    print("  CUÁNTO CUESTA UN TURNO")
    print(f"{'═'*72}")
    print(f"  modelo: {cfg.anthropic_modelo}   proveedor: {cfg.provider}\n")

    print(f"  {'conversación':34} {'msjs':>5} {'al LLM':>7} {'entrada':>8} {'salida':>7} {'USD':>9}")
    print(f"  {'-'*34} {'-'*5} {'-'*7} {'-'*8} {'-'*7} {'-'*9}")

    totales = {"msjs": 0, "llamadas": 0, "entrada": 0, "salida": 0, "usd": 0.0}
    for nombre, guion in CONVERSACIONES.items():
        c = Uso()
        F._clasificador = Medido(F.construir_clasificador(), c)
        grafo = F.construir_flujo(MemorySaver())
        hilo = F.hilo_de(NEG, f"{TEL}-{abs(hash(nombre)) % 9999}")

        for m in guion:
            await grafo.ainvoke({"mensaje": m}, {"configurable": {
                "thread_id": hilo, "business_id": NEG,
                "nombre_negocio": "Peluquería Demo", "telefono": TEL,
                "nombre_cliente": None, "calendario": calendario()}})

        usd = c.entrada / 1e6 * USD_ENTRADA_POR_M + c.salida / 1e6 * USD_SALIDA_POR_M
        print(f"  {nombre:34} {len(guion):5} {c.llamadas:7} "
              f"{c.entrada:8} {c.salida:7} {usd:9.5f}")
        totales["msjs"] += len(guion)
        totales["llamadas"] += c.llamadas
        totales["entrada"] += c.entrada
        totales["salida"] += c.salida
        totales["usd"] += usd

    n = len(CONVERSACIONES)
    ahorro = 1 - totales["llamadas"] / totales["msjs"] if totales["msjs"] else 0
    print(f"\n{'─'*72}")
    print(f"  promedio por turno: {totales['usd']/n:.5f} USD"
          f"   ({totales['entrada']//n} tokens de entrada, {totales['salida']//n} de salida)")
    print(f"  mensajes que NUNCA llegan al modelo: {ahorro:.0%}"
          f"  ({totales['msjs'] - totales['llamadas']} de {totales['msjs']})")
    print(f"  mil turnos por mes: {totales['usd']/n*1000:.2f} USD")
    print(f"{'═'*72}\n")


if __name__ == "__main__":
    asyncio.run(main())
