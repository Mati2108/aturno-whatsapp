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

import asyncio
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


def t3_sin_dato_dice_lo_que_si_sabe() -> list[str]:
    """Cuando no hay respuesta cargada, decir qué SÍ se sabe.

    Hoy el bot contesta "ese dato no lo tengo" y ahí se termina. La persona
    queda con un no y sin saber por qué otra cosa preguntar — y encima el bot
    tiene cargadas cuatro o cinco secciones que nunca nombra.

    Nombrarlas no necesita modelo: los títulos son los `##` del archivo que
    cargó el negocio, y salen del índice con un filtro por metadato. Gratis,
    y sin que nada pueda inventarse un tema que no existe.
    """
    print(f"\n{NEGRITA}[3] SIN DATO, DICE DE QUÉ SÍ PUEDE HABLAR{FIN}")
    print(f"{GRIS}  Un «no lo tengo» a secas deja a la persona sin próximo paso.{FIN}")

    from src import plantillas as P

    fallos = []
    temas = ["Servicios y precios", "Horarios de atención", "Formas de pago",
             "Dónde estamos y cómo llegar"]
    texto = P.sin_dato(temas)

    def chequear(nombre, cond, detalle=""):
        marca = f"{VERDE}✓{FIN}" if cond else f"{ROJO}✗{FIN}"
        print(f"  {marca} {nombre}" + (f"{GRIS}  ({detalle}){FIN}" if detalle else ""))
        if not cond:
            fallos.append(nombre)

    chequear("sigue diciendo que no lo tiene", "no lo tengo" in texto.lower(),
             texto[:50])
    for t in temas:
        chequear(f"nombra «{t}»", t.lower() in texto.lower())
    chequear("los temas van en renglones, no en un párrafo",
             texto.count("\n") >= len(temas))
    chequear("no lleva markdown", "**" not in texto and "##" not in texto)

    # Sin temas cargados no se anuncia una lista vacía. Es el mismo agujero de
    # «Elegí el servicio:» sin servicios, y ya nos mordió una vez.
    solo = P.sin_dato([])
    chequear("sin temas, no anuncia una lista que no existe",
             "\n·" not in solo and "puedo contarte" not in solo.lower(),
             repr(solo[:60]))
    chequear("y ofrece igual la salida a una persona",
             "persona" in solo.lower() or "local" in solo.lower())

    return fallos


def t4_redactar_solo_pasa_lo_verificado() -> list[str]:
    """El modelo redacta, pero nada sale sin pasar por el guardián.

    Con un modelo de mentira, para probar el CAMINO y no al modelo: qué pasa
    cuando escribe bien, cuando inventa, cuando se rinde y cuando explota. Las
    cuatro tienen que terminar en algo seguro.

    La propiedad que se está protegiendo es una sola y vale la pena escribirla:
    **el peor caso posible es el bot de antes.** No existe ninguna rama donde
    esto deje a la persona peor que con el texto literal de siempre.
    """
    print(f"\n{NEGRITA}[4] REDACTAR: NADA SALE SIN PASAR EL GUARDIÁN{FIN}")
    print(f"{GRIS}  Escribe bien, inventa, se rinde o explota. Las cuatro.{FIN}")

    from src.redaccion import redactar

    fallos = []

    def chequear(nombre, cond, detalle=""):
        marca = f"{VERDE}✓{FIN}" if cond else f"{ROJO}✗{FIN}"
        print(f"  {marca} {nombre}" + (f"{GRIS}  ({detalle}){FIN}" if detalle else ""))
        if not cond:
            fallos.append(nombre)

    class Falso:
        """Un modelo que dice lo que le pidas."""

        def __init__(self, respuesta): self._r = respuesta

        async def ainvoke(self, _):
            if isinstance(self._r, Exception):
                raise self._r
            class R: content = self._r
            return R()

    fuente = "Corte de pelo: 30 minutos, $8.000"
    pregunta = "cuánto sale el corte?"

    salio = asyncio.run(redactar(pregunta, fuente,
                                 modelo=Falso("El corte sale $8.000 y lleva 30 minutos.")))
    chequear("una respuesta buena sale tal cual", salio is not None, repr(salio))

    inventa = asyncio.run(redactar(pregunta, fuente,
                                   modelo=Falso("El corte sale $9.500.")))
    chequear("una que inventa un precio NO sale", inventa is None, repr(inventa))

    empatica = asyncio.run(redactar(pregunta, fuente,
                                    modelo=Falso("¡Con gusto! Sale $8.000.")))
    chequear("una con empatía actuada tampoco", empatica is None, repr(empatica))

    rendido = asyncio.run(redactar(pregunta, fuente, modelo=Falso("NO_SE_PUEDE")))
    chequear("si el modelo se rinde, devuelve None", rendido is None, repr(rendido))

    roto = asyncio.run(redactar(pregunta, fuente,
                                modelo=Falso(RuntimeError("sin crédito"))))
    chequear("si el modelo explota, NO propaga la excepción", roto is None, repr(roto))

    vacia = asyncio.run(redactar(pregunta, fuente, modelo=Falso("   ")))
    chequear("una respuesta vacía tampoco sale", vacia is None, repr(vacia))

    # Y sin fuente ni siquiera se le pregunta al modelo: sería pagarle por
    # inventar desde cero, que es exactamente lo que no queremos.
    class Explota:
        async def ainvoke(self, _):
            raise AssertionError("no tendría que haberse llamado al modelo")

    sin_fuente = asyncio.run(redactar(pregunta, "", modelo=Explota()))
    chequear("sin fuente NO se llama al modelo", sin_fuente is None)

    return fallos


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

    fallos_3 = t3_sin_dato_dice_lo_que_si_sabe()
    if fallos_3:
        print(f"\n{ROJO}{NEGRITA}  El «no lo tengo» no dice de qué sí puede "
              f"hablar.{FIN}")
        return 1

    fallos_4 = t4_redactar_solo_pasa_lo_verificado()
    if fallos_4:
        print(f"\n{ROJO}{NEGRITA}  Hay un camino por el que algo sale sin "
              f"verificar.{FIN}")
        return 1

    print(f"\n{VERDE}{NEGRITA}  El guardián atrapa todo lo prohibido y no frena "
          f"nada legítimo.{FIN}")
    print(f"{GRIS}  Recién ahora se puede dejar que algo redacte.{FIN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
