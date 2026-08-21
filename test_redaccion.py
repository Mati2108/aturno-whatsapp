"""
test_redaccion.py — El guardián, probado antes de que exista quien redacte.

POR QUÉ ESTE ARCHIVO VA PRIMERO
-------------------------------
Hoy la garantía de que el bot no inventa es estructural: el LLM no tiene ningún
camino hacia el texto que lee una persona. Para poder aflojar eso hace falta
reemplazarla por otra garantía, y una garantía que no se puede verificar no es
una garantía — es una esperanza.

El guardián se escribe y se prueba SOLO, contra `casos_invencion.jsonl`, antes
de que exista el código que redacta. El orden no es capricho: un guardián
escrito después termina ajustado para dejar pasar lo que el modelo ya produce, y
ahí deja de guardar nada.

LAS DOS MITADES PESAN IGUAL
---------------------------
· Si UNA sola de las prohibidas pasa, el guardián no sirve y no se puede seguir.
· Si UNA sola de las legítimas se rechaza, el guardián está tan apretado que la
  función queda apagada en la práctica, y tampoco sirve.

Corre sin LLM y sin red: los textos candidatos están escritos a mano en el
archivo de casos. Lo que se prueba es el guardián, no el modelo.

    python test_redaccion.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from src.redaccion import verificar

VERDE, ROJO, AMARILLO, GRIS, NEGRITA, FIN = (
    "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[1m", "\033[0m")

CASOS = Path(__file__).parent / "casos_invencion.jsonl"


def cargar() -> list[dict]:
    casos = []
    for linea in CASOS.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        caso = json.loads(linea)
        if "_" not in caso:
            casos.append(caso)
    return casos


def main() -> int:
    casos = cargar()
    prohibidas = [c for c in casos if c["veredicto"] == "rechazar"]
    legitimas = [c for c in casos if c["veredicto"] == "aceptar"]

    print(f"\n{NEGRITA}EL GUARDIÁN, CONTRA LA LISTA NEGRA{FIN}")
    print(f"{GRIS}  {len(prohibidas)} que no pueden pasar · "
          f"{len(legitimas)} que no se pueden rechazar{FIN}")

    # ── Las que NO pueden pasar ─────────────────────────────────────
    print(f"\n{NEGRITA}[1] LO QUE EL BOT NO PUEDE DECIR NUNCA{FIN}")
    coladas, por_regla = [], Counter()
    for c in prohibidas:
        motivo = verificar(c["texto"], c["fuente"], c["pregunta"])
        atrapada = motivo is not None
        if atrapada:
            por_regla[motivo.split(":")[0]] += 1
        else:
            coladas.append(c)
        marca = f"{VERDE}✓{FIN}" if atrapada else f"{ROJO}✗ SE COLÓ{FIN}"
        detalle = (f"{GRIS}{motivo}{FIN}" if atrapada
                   else f"{ROJO}{c['motivo']}{FIN}")
        print(f"  {marca} {c['texto'][:52]:<52} {detalle}")

    # ── Las que SÍ tienen que pasar ─────────────────────────────────
    print(f"\n{NEGRITA}[2] LO QUE EL BOT SÍ TIENE QUE PODER DECIR{FIN}")
    print(f"{GRIS}  Un guardián que rechaza todo obliga a apagar la función.{FIN}")
    frenadas = []
    for c in legitimas:
        motivo = verificar(c["texto"], c["fuente"], c["pregunta"])
        pasa = motivo is None
        if not pasa:
            frenadas.append((c, motivo))
        marca = f"{VERDE}✓{FIN}" if pasa else f"{ROJO}✗ FRENADA{FIN}"
        detalle = "" if pasa else f"{ROJO}{motivo}{FIN}"
        print(f"  {marca} {c['texto'][:52]:<52} {detalle}")

    # ── El veredicto ────────────────────────────────────────────────
    print(f"\n{'─' * 72}")
    atrapadas = len(prohibidas) - len(coladas)
    print(f"  atrapadas: {atrapadas}/{len(prohibidas)}   "
          f"legítimas que pasan: {len(legitimas) - len(frenadas)}/{len(legitimas)}")
    if por_regla:
        print(f"\n{GRIS}  qué regla atrapó qué:{FIN}")
        for regla, n in por_regla.most_common():
            print(f"{GRIS}    {regla:<14} ×{n}{FIN}")

    if coladas:
        print(f"\n{ROJO}{NEGRITA}  {len(coladas)} invención(es) se colaron. "
              f"NO se puede seguir al paso 3.{FIN}")
        for c in coladas:
            print(f"{ROJO}    · {c['texto']}{FIN}\n{GRIS}      {c['motivo']}{FIN}")
        return 1

    if frenadas:
        print(f"\n{AMARILLO}{NEGRITA}  {len(frenadas)} respuesta(s) legítima(s) "
              f"frenada(s). El guardián está demasiado apretado.{FIN}")
        for c, motivo in frenadas:
            print(f"{AMARILLO}    · {c['texto']}{FIN}\n{GRIS}      lo frenó: {motivo}{FIN}")
        return 1

    print(f"\n{VERDE}{NEGRITA}  El guardián atrapa todo lo prohibido y no frena "
          f"nada legítimo.{FIN}")
    print(f"{GRIS}  Recién ahora se puede dejar que algo redacte.{FIN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
