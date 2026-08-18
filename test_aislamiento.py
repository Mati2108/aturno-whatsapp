"""
test_aislamiento.py — La prueba de seguridad del multi-tenant.

Verifica que el conocimiento de un negocio nunca se filtre al de otro. Es el
requisito que hace vendible el producto: si la peluquería puede leer los precios
del consultorio, no se le puede ofrecer a nadie.

    python test_aislamiento.py
"""

import asyncio

from src.rag.indice import Recuperador, abrir_indice

PELUQUERIA = "demo-peluqueria"
CONSULTORIO = "demo-consultorio"

# Términos que solo existen en el archivo de UN negocio. Si aparecen en la
# recuperación del otro, hubo filtración.
HUELLAS = {
    PELUQUERIA: ["coloración", "barba", "amoníaco", "medrano", "almagro", "corrientes"],
    CONSULTORIO: ["osde", "swiss medical", "galeno", "pami", "recoleta", "dni"],
}

# Preguntas que SOLO el otro negocio puede responder. Son la trampa: el
# recuperador debería devolver poco o nada relevante, nunca datos ajenos.
CRUZADAS = {
    PELUQUERIA: "¿Atienden OSDE o Swiss Medical?",
    CONSULTORIO: "¿Cuánto sale la coloración y usan productos sin amoníaco?",
}

PROPIAS = {
    PELUQUERIA: [
        "¿Cuánto sale un corte de pelo?",
        "¿A qué hora abren los sábados?",
        "¿Puedo pagar con tarjeta de crédito?",
    ],
    CONSULTORIO: [
        "¿Qué obras sociales aceptan?",
        "¿Atienden los martes a la mañana?",
    ],
}

ok = True


def chequear(nombre: str, cond: bool, detalle: str = "") -> None:
    global ok
    ok = ok and bool(cond)
    print(f"  {'✓' if cond else '✗'} {nombre}" + (f"  ({detalle})" if detalle else ""))


class SinCuota(Exception):
    """El proveedor de embeddings no contesta. NO es una falla de aislamiento."""


def _es_falta_de_cuota(e: Exception) -> bool:
    texto = str(e)
    return "RESOURCE_EXHAUSTED" in texto or "429" in texto


async def main() -> None:
    """Verifica que ningún negocio pueda ver los datos de otro.

    Si el proveedor de embeddings no responde, esto NO dice "falla": dice que
    no se pudo verificar. Son cosas distintas y confundirlas es peligroso — un
    día alguien va a ver rojo, asumir que es la cuota otra vez, y va a estar
    mirando una filtración real entre clientes.
    """
    indice = abrir_indice()
    r = {
        PELUQUERIA: Recuperador(PELUQUERIA, indice),
        CONSULTORIO: Recuperador(CONSULTORIO, indice),
    }

    print("\n[1] CADA NEGOCIO ENCUENTRA LO SUYO")
    for negocio, preguntas in PROPIAS.items():
        for p in preguntas:
            docs = await r[negocio].buscar(p)
            chequear(f"[{negocio}] {p}", len(docs) > 0, f"{len(docs)} fragmentos")

    print("\n[2] TODO LO RECUPERADO PERTENECE AL NEGOCIO QUE PREGUNTÓ")
    for negocio in (PELUQUERIA, CONSULTORIO):
        todas = PROPIAS[negocio] + [CRUZADAS[negocio]]
        ajenos = []
        for p in todas:
            for d in await r[negocio].buscar(p):
                if d.metadata.get("business_id") != negocio:
                    ajenos.append((p, d.metadata.get("business_id")))
        chequear(f"[{negocio}] sin fragmentos ajenos", not ajenos, str(ajenos[:2]))

    print("\n[3] PREGUNTA CRUZADA: NO SE FILTRA EL DATO DEL OTRO")
    otro = {PELUQUERIA: CONSULTORIO, CONSULTORIO: PELUQUERIA}
    for negocio, pregunta in CRUZADAS.items():
        texto = (await r[negocio].contexto(pregunta)).lower()
        filtradas = [h for h in HUELLAS[otro[negocio]] if h in texto]
        chequear(
            f"[{negocio}] '{pregunta[:38]}…'",
            not filtradas,
            f"filtró: {filtradas}" if filtradas else "nada del otro negocio",
        )

    print("\n[4] NO SE PUEDE CONSTRUIR UN RECUPERADOR SIN NEGOCIO")
    try:
        Recuperador("", indice)
        chequear("rechaza business_id vacío", False, "lo aceptó")
    except ValueError:
        chequear("rechaza business_id vacío", True)

    print("\n[5] MUESTRA DE UNA RESPUESTA REAL")
    ctx = await r[PELUQUERIA].contexto("¿cuánto sale teñirse el pelo?")
    print("   " + "\n   ".join(ctx.splitlines()[:6]))

    print("\n" + "=" * 62)
    print("  RESULTADO:", "AISLAMIENTO VERIFICADO" if ok else "HAY FILTRACIÓN")
    print("=" * 62 + "\n")
    raise SystemExit(0 if ok else 1)


async def _correr() -> None:
    try:
        await main()
    except Exception as e:  # noqa: BLE001
        if not _es_falta_de_cuota(e):
            raise
        print(f"\n{'=' * 62}")
        print("  NO SE PUDO VERIFICAR — el proveedor de embeddings sin cuota")
        print("  (1.000 pedidos por día en el plan gratuito de Gemini)")
        print("  El aislamiento no falló: no se llegó a probar.")
        print(f"{'=' * 62}\n")
        raise SystemExit(2) from None


if __name__ == "__main__":
    asyncio.run(_correr())
