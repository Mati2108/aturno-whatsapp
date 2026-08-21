"""
test_clasificador.py — La única pieza probabilística, medida.

POR QUÉ FALTABA
---------------
Todo el resto del repo se prueba sin LLM, y esa decisión es correcta: hace que
un rojo signifique siempre un bug del código y nunca "el modelo contestó otra
cosa". Pero deja al descubierto justo lo que no es determinístico.

Los tres bugs de la semana del 18 de agosto fueron los tres del clasificador
—«no soy Milagros», «quiero otro turno», el bucle de «De nada»— y los tres los
encontró una persona escribiéndole al bot en producción. Este archivo es el que
los habría encontrado antes.

NO CORRE EN CADA CAMBIO, Y NO ES UN DESCUIDO
--------------------------------------------
Cuesta plata: una llamada al modelo por caso. El resto de la suite es gratis y
se corre siempre; esto se corre cuando se toca el prompt, el esquema, la tabla
de atajos o el enum de intenciones. Imprime lo que va a costar antes de gastar
un centavo.

LO QUE HAY QUE MIRAR ES LA MATRIZ, NO EL PORCENTAJE
---------------------------------------------------
Un acierto global del 90% no dice nada accionable. La matriz de confusión sí:
dice CON QUÉ se confunde cada intención, que es exactamente donde estuvieron los
tres bugs —`ver_mas` comiéndose «otro turno», `saludo` comiéndose «gracias»—.

Y si esto diera 100%, el conjunto de casos está mal armado: hay que meterle
casos más difíciles. Ver el bloque `duro: true` de `casos.jsonl`.

    python test_clasificador.py            # todo
    python test_clasificador.py --si       # sin preguntar (para un script)
    python test_clasificador.py --faciles  # saltea los marcados «duro»
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

from src.agentes.clasificador import (
    clasificar, construir_clasificador, construir_respaldos)
from src.agentes.estados import (
    Estado, Intencion, correccion_de_nombre, pedido_de_cambio, respuesta_fija,
    sin_contenido)
from src.config import config
from src.fechas import DIAS, calendario, hoy
from src.gasto import GASTO

logging.basicConfig(level=logging.ERROR)

VERDE, ROJO, AMARILLO, GRIS, NEGRITA, FIN = (
    "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[1m", "\033[0m")

CASOS = Path(__file__).parent / "casos.jsonl"

# Medido con `medir_costo.py`: una clasificación son ~1.470 tokens de entrada y
# ~58 de salida. Con Haiku ($1 / $5 por millón) eso da ~0,0018 USD por llamada.
#
# El plan decía "~100 casos ≈ 0,002 USD". Estaba mal por cien: 0,002 es el costo
# de UNA llamada, no de cien. Cien casos cuestan ~0,18 USD.
USD_POR_CASO = 0.0018

# EL PISO POR INTENCIÓN. Fijado después de la primera corrida —21/8/2026,
# 92,9% global— y no antes: poner un número inventado y después ajustarlo hasta
# que pase es escribir un test que no puede fallar.
#
# No todos valen lo mismo, y por eso no es un solo número:
#
#   · 1.00 va donde fallar es inaceptable, no donde el modelo hoy acierta.
#     `hablar_con_persona` es la salida a un humano, que el 87% de los clientes
#     considera esencial. `rechazar` es el peor bug que tuvo este proyecto —un
#     "no" al resumen que reservaba igual—. `confirmar` reserva de verdad.
#   · 0.85 para las que hoy dan 100% pero dependen del modelo: deja aire para
#     que el proveedor cambie de versión sin pintar todo de rojo, y no tanto
#     como para que una regresión real pase inadvertida.
#   · Las dos que hoy no llegan al 100% quedan en su número actual, redondeado
#     para abajo. No se bajan para que pasen: se dejan como están para que se
#     note el día que empeoren, y como deuda a la vista para que alguien las
#     suba.
PISO: dict[str, float] = {
    "hablar_con_persona": 1.00,
    "rechazar": 1.00,
    "confirmar": 1.00,
    "dar_nombre": 0.85,
    "elegir_servicio": 0.85,
    "elegir_staff": 0.85,
    "elegir_horario": 0.85,
    "consultar_info": 0.85,
    "cancelar": 0.85,
    "saludo": 0.85,
    "ver_mas": 0.85,
    "pedir_link": 0.85,
    "volver": 0.85,
    # Deuda conocida: los cuatro errores que quedan son frases con "no" que el
    # modelo lee como `rechazar`. No se ve en pantalla —el flujo sólo honra
    # RECHAZAR en el resumen, y fuera de ahí cae en el pedido del paso, que es
    # lo mismo que haría con `desconocido`— pero está y se mide.
    "desconocido": 0.60,
    "elegir_dia": 0.70,
}


def cargar(solo_faciles: bool = False) -> list[dict]:
    """Lee `casos.jsonl`. Las líneas con `_` son títulos de sección."""
    casos = []
    for linea in CASOS.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("//"):
            continue
        caso = json.loads(linea)
        if "_" in caso:
            continue
        if solo_faciles and caso.get("duro"):
            continue
        casos.append(caso)
    return casos


async def evaluar(caso: dict) -> tuple[str, bool]:
    """Clasifica un caso. Devuelve qué dio y si acertó.

    Pasa por el mismo camino que un mensaje de verdad —primero los atajos, y
    sólo si no resuelven, el modelo— porque lo que se está midiendo es qué
    entiende EL BOT, no qué entiende el LLM suelto. Un caso que resuelve un
    atajo es un acierto igual, y además gratis.
    """
    estado = Estado(caso["estado"])
    sin_llm = _sin_modelo(caso["mensaje"], estado)
    if sin_llm is not None:
        return sin_llm, sin_llm == caso["espera"]

    d = hoy()
    salida = await clasificar(
        _cadena(), mensaje=caso["mensaje"], estado=estado, opciones=None,
        hoy_iso=d.isoformat(), dia_semana=DIAS[d.weekday()],
        calendario=calendario(d, 8), respaldos=_respaldos(),
    )
    return salida.intent.value, salida.intent.value == caso["espera"]


# EL MISMO ORDEN QUE `entender`, y eso no es un detalle.
#
# La primera versión de este archivo llamaba al clasificador derecho, y marcó
# como fallas cuatro casos que el bot resuelve bien —«no soy Milagros», «ese no
# es mi nombre», «mejor otro servicio»—: los resuelven las tablas de código,
# antes de que el modelo vea nada. Un evaluador que mide una pieza suelta en vez
# del camino real inventa bugs que no existen y esconde los que sí.
#
# Si `entender` gana un escalón nuevo, tiene que aparecer también acá.
def _sin_modelo(texto: str, estado: Estado) -> str | None:
    """Lo que el bot resuelve sin gastar modelo, o None si hay que preguntarle."""
    if sin_contenido(texto):
        return Intencion.DESCONOCIDO.value
    fija = respuesta_fija(texto, estado)
    if fija is not None:
        return fija[0].value
    if correccion_de_nombre(texto) is not None:
        return Intencion.DAR_NOMBRE.value
    cambio = pedido_de_cambio(texto)
    if cambio is not None:
        return cambio.value
    return None


# La misma cadena para toda la corrida: armarla por caso no cambiaría el
# resultado pero sí el tiempo, y son 56 casos.
_CACHE: dict = {}


def _cadena():
    if "c" not in _CACHE:
        _CACHE["c"] = construir_clasificador()
    return _CACHE["c"]


def _respaldos():
    if "r" not in _CACHE:
        _CACHE["r"] = construir_respaldos()
    return _CACHE["r"]


def matriz(errores: list[tuple[dict, str]]) -> None:
    """Con qué se confunde cada intención. Lo único accionable de la corrida."""
    if not errores:
        return
    print(f"\n{NEGRITA}CON QUÉ SE CONFUNDE{FIN}")
    print(f"{GRIS}  Acá estuvieron los tres bugs de agosto.{FIN}\n")
    pares: Counter = Counter((c["espera"], dio) for c, dio in errores)
    ancho = max(len(a) for a, _ in pares) + 2
    for (espera, dio), n in pares.most_common():
        print(f"  {espera:<{ancho}} → {ROJO}{dio}{FIN}  {GRIS}×{n}{FIN}")


async def main() -> int:
    solo_faciles = "--faciles" in sys.argv
    casos = cargar(solo_faciles)

    print(f"\n{NEGRITA}EL CLASIFICADOR, CONTRA {len(casos)} CASOS CONOCIDOS{FIN}")
    print(f"{GRIS}  modelo: {config().provider} · duros incluidos: "
          f"{'no' if solo_faciles else 'sí'}{FIN}")

    # ---- La baranda de plata ----
    #
    # El resto de la suite es gratis. Ésta no, y una corrida por accidente en un
    # loop es exactamente cómo aparecieron los cinco dólares de aquel día.
    costo = len(casos) * USD_POR_CASO
    print(f"\n  Va a costar hasta {NEGRITA}{costo:.3f} USD{FIN} "
          f"{GRIS}(menos: los atajos no llaman al modelo){FIN}")
    print(f"  Gastado hoy: {GASTO.usd_hoy():.4f} de {config().tope_diario_usd:.2f} USD")
    if "--si" not in sys.argv:
        if input("\n  ¿Sigo? [s/N] ").strip().lower() not in ("s", "si", "sí"):
            print(f"{GRIS}  Listo, no gasté nada.{FIN}")
            return 0

    aciertos, errores = 0, []
    por_intencion: dict[str, list[bool]] = defaultdict(list)
    sin_modelo = 0

    print()
    for i, caso in enumerate(casos, 1):
        try:
            dio, bien = await evaluar(caso)
        except Exception as e:  # noqa: BLE001
            print(f"\n{ROJO}  Se cortó en el caso {i} ({type(e).__name__}): {e}{FIN}")
            print(f"{GRIS}  Los {i - 1} casos anteriores ya corrieron. "
                  f"Revisá la credencial o el tope y volvé.{FIN}")
            break

        if _sin_modelo(caso["mensaje"], Estado(caso["estado"])) is not None:
            sin_modelo += 1
        por_intencion[caso["espera"]].append(bien)
        aciertos += bien
        if not bien:
            errores.append((caso, dio))
        marca = f"{VERDE}✓{FIN}" if bien else f"{ROJO}✗{FIN}"
        duro = f"{AMARILLO}·duro{FIN}" if caso.get("duro") else ""
        print(f"  {marca} {caso['mensaje'][:44]:<44} {GRIS}[{caso['estado'][:18]}]{FIN} "
              + ("" if bien else f"{ROJO}dio {dio}, esperaba {caso['espera']}{FIN} ") + duro)

    corridos = sum(len(v) for v in por_intencion.values())
    if not corridos:
        return 1

    # ---- Los números ----
    print(f"\n{'─' * 66}")
    global_ = aciertos / corridos
    print(f"{NEGRITA}ACIERTO GLOBAL: {global_:.1%}{FIN}  "
          f"{GRIS}({aciertos}/{corridos} · {sin_modelo} sin gastar modelo){FIN}")

    print(f"\n{NEGRITA}POR INTENCIÓN{FIN}")
    for intencion, res in sorted(por_intencion.items(), key=lambda kv: sum(kv[1]) / len(kv[1])):
        tasa = sum(res) / len(res)
        color = VERDE if tasa == 1 else (AMARILLO if tasa >= 0.5 else ROJO)
        print(f"  {color}{tasa:>6.0%}{FIN}  {intencion:<22} {GRIS}{sum(res)}/{len(res)}{FIN}")

    matriz(errores)

    # ---- El veredicto ----
    print(f"\n{'─' * 66}")
    if global_ >= 0.999 and not solo_faciles:
        # No es una buena noticia: es la señal de que el conjunto no exige nada.
        print(f"{AMARILLO}100%. El conjunto de casos está MAL ARMADO.{FIN}")
        print(f"{GRIS}  Un conjunto dorado sano arranca entre 80% y 90%. "
              f"Metele casos más difíciles a casos.jsonl.{FIN}")
        return 1

    if not PISO:
        print(f"{GRIS}Primera corrida: esto informa, todavía no juzga.{FIN}")
        print(f"{GRIS}Fijá PISO con estos números —redondeando para abajo— y a "
              f"partir de ahí una baja es un bug.{FIN}")
        return 0

    bajaron = [i for i, res in por_intencion.items()
               if i in PISO and sum(res) / len(res) < PISO[i]]
    if bajaron:
        print(f"{ROJO}Bajó el acierto en: {', '.join(bajaron)}{FIN}")
        return 1
    print(f"{VERDE}Ninguna intención bajó de su piso.{FIN}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
