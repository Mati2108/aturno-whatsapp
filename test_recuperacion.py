"""
test_recuperacion.py — La evaluación de calidad del RAG.

"Si no podés medirlo, no podés optimizarlo." Este archivo existe para que las
decisiones sobre el RAG salgan de números y no de intuición: cada pregunta
declara qué sección DEBERÍA responderla, y medimos cuántas veces el recuperador
la trae primera (top-1) y cuántas la trae entre las tres (top-3).

Fue lo que decidió el cambio de `nomic-embed-text` a `bge-m3`: 4/8 contra 7/8
en top-1. Sin esta medición, el bot habría salido a producción sin poder
contestar "¿cuánto cuesta un corte?".

    python test_recuperacion.py
"""

import asyncio

from src.rag.indice import Recuperador, abrir_indice

# pregunta -> sección que debería responderla
CASOS: dict[str, dict[str, str]] = {
    "demo-peluqueria": {
        "cuánto cuesta un corte": "Servicios y precios",
        "cuánto sale teñirse el pelo": "Servicios y precios",
        "a qué hora cierran": "Horarios de atención",
        "abren los sábados": "Horarios de atención",
        "puedo pagar con crédito": "Formas de pago",
        "dónde quedan": "Dónde estamos y cómo llegar",
        "cómo llego": "Dónde estamos y cómo llegar",
        "puedo cancelar el turno": "Cómo sacar y cancelar un turno",
    },
    "demo-consultorio": {
        "atienden OSDE": "Obras sociales",
        "cuánto sale la consulta": "Servicios y precios",
        "atienden los martes": "Horarios de atención",
        "con cuánto aviso hay que cancelar": "Cómo sacar y cancelar un turno",
    },
}

# Debajo de esto, el bot empieza a contestar cosas que no le preguntaron.
UMBRAL_TOP3 = 0.85


async def main() -> None:
    indice = abrir_indice()
    total = aciertos1 = aciertos3 = 0
    fallos: list[str] = []

    for negocio, casos in CASOS.items():
        r = Recuperador(negocio, indice, k=3)
        print(f"\n── {negocio} " + "─" * (56 - len(negocio)))
        for pregunta, esperada in casos.items():
            secciones = [d.metadata.get("seccion") for d in await r.buscar(pregunta)]
            en1 = bool(secciones) and secciones[0] == esperada
            en3 = esperada in secciones
            total += 1
            aciertos1 += en1
            aciertos3 += en3
            if not en3:
                fallos.append(f"[{negocio}] {pregunta}")
            marca = "✓" if en1 else ("~" if en3 else "✗")
            # "nada" y no `secciones[0]`: cuando ninguna sección pasa el umbral
            # la lista viene vacía, y ese es justamente el resultado que hay que
            # poder leer. Indexando a ciegas, la evaluación entera se caía con
            # un IndexError en la primera pregunta sin respuesta — o sea que el
            # instrumento se rompía exactamente cuando había algo que medir.
            print(f"  {marca} {pregunta:<34} → {secciones[0] if secciones else 'nada'}")

    print("\n" + "=" * 64)
    print(f"  top-1: {aciertos1}/{total}    top-3: {aciertos3}/{total}")
    if fallos:
        print("  Ni en top-3:")
        for f in fallos:
            print(f"    - {f}")
    ratio = aciertos3 / total
    ok = ratio >= UMBRAL_TOP3
    print(f"  {'✓ PASA' if ok else '✗ NO PASA'} el umbral de {UMBRAL_TOP3:.0%} en top-3 ({ratio:.0%})")
    print("=" * 64 + "\n")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
