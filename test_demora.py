"""
test_demora.py — Qué pasa cuando el bot tarda.

Tres propiedades, y las tres nacieron de la misma pregunta: qué ve la persona
mientras espera una respuesta que no llega.

  1. Si contesta rápido, NO sale ningún aviso. Un "estoy tardando" cuando no
     tardó gasta cupo de Twilio y encima miente.
  2. Si tarda, el aviso sale a los `aviso_segundos` y la respuesta real llega
     igual después. Avisar no puede costar la respuesta.
  3. Si se agota el techo, el mensaje ofrece una salida en vez de pedirle a la
     persona que reintente contra algo que acaba de fallar.

    python test_demora.py
"""

import asyncio
import logging

import src.api.webhook as W
from src.schemas import MensajeEntrante, Tenant

logging.basicConfig(level=logging.CRITICAL)

ok = True


def chequear(nombre: str, cond: bool, detalle: str = "") -> None:
    global ok
    ok = ok and bool(cond)
    print(f"  {'✓' if cond else '✗'} {nombre}" + (f"  ({detalle})" if detalle else ""))


NEGOCIO = Tenant(business_id="uid-de-firebase", slug="demo-peluqueria",
                 nombre="Peluquería Demo", numero_whatsapp="+14155238886")
MENSAJE = MensajeEntrante(de="+5491130032002", para="+14155238886", texto="hola")


async def correr(tarda: float, techo: int = 30, aviso: int = 1):
    """Corre el procesamiento con una respuesta que tarda `tarda` segundos.

    Los tiempos van comprimidos —1 segundo en vez de 10— para que la prueba
    corra en segundos y no en minutos. Lo que se verifica es el MECANISMO: que
    el aviso dependa de si la respuesta llegó o no, no el número exacto, que es
    una decisión de producto y vive en la constante.
    """
    enviados: list[str] = []

    async def respuesta_lenta(_m, _n):
        await asyncio.sleep(tarda)
        return "listo, tu turno quedó tomado"

    # Los umbrales viven en la config, no en constantes del módulo: se pisan
    # ahí, que es de donde el código los lee de verdad.
    ajustes = W.config()
    original_enviar = W._enviar
    original_componer = W._componer_respuesta
    original_indicador = W._mostrar_escribiendo
    original_aviso, original_techo = ajustes.aviso_segundos, ajustes.techo_segundos
    original_grafo = W._grafo
    try:
        # Los dobles son `async` porque los originales lo son: mandar por
        # Twilio y prender el indicador salen a la red, y hacerlo en forma
        # sincrónica frenaba el bucle de eventos —y con él a todas las demás
        # conversaciones— mientras duraba la llamada.
        async def _enviar_falso(destino, negocio, texto):
            enviados.append(texto)

        async def _indicador_falso(sid):
            return None

        W._enviar = _enviar_falso
        W._componer_respuesta = respuesta_lenta
        W._mostrar_escribiendo = _indicador_falso
        ajustes.aviso_segundos, ajustes.techo_segundos = aviso, techo
        W._grafo = None                      # sin grafo no consulta el estado
        await W._procesar_y_responder(MENSAJE, NEGOCIO, "")
    finally:
        W._enviar = original_enviar
        W._componer_respuesta = original_componer
        W._mostrar_escribiendo = original_indicador
        ajustes.aviso_segundos, ajustes.techo_segundos = original_aviso, original_techo
        W._grafo = original_grafo
    return enviados


async def main():
    print("\n" + "═" * 66)
    print("  QUÉ VE LA PERSONA MIENTRAS ESPERA")
    print("═" * 66)

    print("\n[1] RESPUESTA RÁPIDA: NINGÚN AVISO")
    salidas = await correr(tarda=0.1, aviso=1)
    chequear("manda un solo mensaje", len(salidas) == 1, f"{len(salidas)}")
    chequear("y es la respuesta, no una disculpa",
             "turno quedó tomado" in salidas[-1])

    print("\n[2] RESPUESTA LENTA: AVISA Y DESPUÉS CONTESTA IGUAL")
    salidas = await correr(tarda=2.5, aviso=1, techo=30)
    chequear("manda dos: el aviso y la respuesta", len(salidas) == 2, f"{len(salidas)}")
    if len(salidas) == 2:
        chequear("el primero avisa que está tardando",
                 "tardando más de lo normal" in salidas[0])
        chequear("y ofrece hablar con una persona",
                 "una persona" in salidas[0])
        # Lo que importa: avisar NO canceló el trabajo.
        chequear("la respuesta real llega igual después",
                 "turno quedó tomado" in salidas[1], salidas[1][:40])

    print("\n[3] SE AGOTA EL TECHO: DA UNA SALIDA, NO PIDE REINTENTAR")
    salidas = await correr(tarda=30, aviso=1, techo=2)
    chequear("avisa y después contesta que no pudo", len(salidas) == 2, f"{len(salidas)}")
    ultimo = salidas[-1] if salidas else ""
    chequear("no le pide a la persona que lo mande de nuevo",
             "de nuevo" not in ultimo, ultimo[:50])
    chequear("le ofrece una salida concreta", "una persona" in ultimo)

    print("\n[4] UN ADJUNTO SIN TEXTO NO PUEDE QUEDAR SIN RESPUESTA")
    from src import plantillas as P
    chequear("un audio explica que no lo escucha, no dice 'no entendí'",
             "escuchar audios" in P.solo_adjunto("audio/ogg"))
    chequear("y ofrece la salida humana",
             "una persona" in P.solo_adjunto("audio/ogg"))
    chequear("un tipo desconocido también contesta algo",
             P.solo_adjunto("") != "")

    print("\n[5] CANCELAR NO PUEDE MENTIR")
    chequear("abandonar un pedido en curso NO dice que canceló una reserva",
             "cancelé la reserva" not in P.cancelado(), P.cancelado()[:45])
    chequear("y aclara que no reservó nada",
             "No llegué a reservarte nada" in P.cancelado())
    chequear("con un turno ya confirmado dice que no puede",
             "no los puedo cancelar yo" in P.no_puedo_cancelar(None))
    chequear("y manda a una persona, que es la vía que existe",
             "una persona" in P.no_puedo_cancelar(None))
    chequear("la confirmación ya no promete que el bot cancela",
             "avisame con tiempo" not in P.confirmado(
                 "Corte", None, __import__("datetime").date(2026, 8, 20),
                 __import__("datetime").time(16, 30), "ABC123"))

    print("\n" + "═" * 66)
    print("  RESULTADO:", "LA ESPERA ESTÁ CUBIERTA" if ok else "HAY FALLAS")
    print("═" * 66 + "\n")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
