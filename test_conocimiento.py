"""
test_conocimiento.py — Lo que el negocio carga en el panel sobrevive un reinicio.

QUÉ PROPIEDAD PRUEBA
--------------------
Una sola, y es la que faltaba: **al arrancar, el bot trae de aturno lo que el
negocio cargó**, en vez de quedarse con la foto que viaja en la imagen.

Por qué importa. El panel EMPUJA: cuando el negocio guarda una respuesta, aturno
llama a `/panel/reindexar` y el bot la indexa. Eso ya andaba. Pero lo indexado
se escribe en `datos/`, y en Render el disco es efímero: en el deploy siguiente
el índice se reconstruye desde los `.md` del repo y todo lo cargado por el panel
desaparece.

El síntoma no parece un error, y por eso es peor: el negocio carga cómo llegar,
qué colectivos paran cerca y qué medios de pago acepta, lo prueba, anda — y una
semana después el bot contesta "ese dato no lo tengo cargado" sin que nadie haya
tocado nada.

    python test_conocimiento.py

USA EMBEDDINGS DE VERDAD, y por eso no vive en `test_bordes.py`, que corre
entero sin red a propósito. Son pocos fragmentos, pero consumen cuota.
"""

import asyncio

from src import plantillas as P
from src.aturno.doble import AturnoDoble
from src.rag.indice import Recuperador, abrir_indice, reindexar_negocio
from src.schemas import Tenant

VERDE, ROJO, GRIS, NEGRITA, FIN = "\033[32m", "\033[31m", "\033[90m", "\033[1m", "\033[0m"

NEGOCIO = "test-conocimiento"

ok = True


def chequear(nombre: str, cond: bool, detalle: str = "") -> None:
    global ok
    ok = ok and bool(cond)
    color = VERDE if cond else ROJO
    print(f"  {color}{'✓' if cond else '✗'}{FIN} {nombre}"
          + (f"{GRIS}  ({detalle}){FIN}" if detalle else ""))


# Lo que devolvería aturno para un negocio que contestó el formulario. El
# formato es el que escribe el panel: la línea `>` son las palabras con las que
# la gente pregunta, y es lo que hace que "¿dónde quedan?" encuentre una
# dirección con la que no comparte una sola palabra.
#
# La forma es la REAL, copiada de lo que devuelve aturno: varias preguntas
# distintas DENTRO de una misma sección `##`. Eso es lo que rompía todo, y por
# eso el fixture no se simplifica: con una pregunta por sección, la prueba pasa
# y el bot sigue roto.
CONOCIMIENTO = """# Negocio de prueba

## Cómo llegar

> dónde quedan dónde están en qué zona cómo llego dirección sobre qué calle
Roque Sáenz Peña 668, a media cuadra de la plaza.
> alguna referencia cómo lo encuentro no encuentro la puerta
Al lado del kiosco, puerta negra.
> qué colectivos paran cerca qué bondi me deja qué subte línea transporte
Te dejan cerca el 24, el 39 y el 152. El subte más cercano es Callao, línea B.
> hay estacionamiento dónde dejo el auto cochera
Tenemos cochera propia para dos autos.
> es accesible en silla de ruedas hay ascensor accesibilidad
Hay rampa en la entrada y el local es todo en planta baja.

## Pagos

> qué medios de pago aceptan aceptan tarjeta puedo pagar con débito toman efectivo
Aceptamos efectivo, débito, crédito y transferencia.
> aceptan transferencia a qué alias tienen cbu
El alias es negocio.prueba.
> hay descuento por pagar en efectivo sale menos en efectivo
Hacemos 7% de descuento pagando en efectivo.

## Turnos

> con cuánta anticipación conviene sacar turno para cuándo hay lugar
Conviene sacarlo con 5 días de anticipación.
> si llego tarde cuánta tolerancia hay me atrasé
Hay 15 minutos de tolerancia.
"""


class AturnoConConocimiento(AturnoDoble):
    """El doble de siempre, pero que además contesta el formulario cargado.

    Hereda en vez de reemplazar porque lo que se prueba es el arranque completo,
    y ahí adentro el resto del cliente se sigue usando.
    """

    async def conocimiento(self, business_id: str) -> str:
        return CONOCIMIENTO if business_id == NEGOCIO else ""


async def main() -> int:
    from src.api import webhook

    print(f"\n{NEGRITA}EL CONOCIMIENTO DEL PANEL SOBREVIVE UN REINICIO{FIN}")
    print(f"{GRIS}  Arrancar tiene que traerlo de aturno, no del .md de la imagen.{FIN}")

    # Se arranca desde CERO a propósito: es exactamente el estado de un
    # contenedor recién levantado en Render, sin nada de lo que se cargó antes.
    reindexar_negocio(NEGOCIO, "")

    vacio = Recuperador(NEGOCIO, abrir_indice()).contexto
    chequear("antes de arrancar, el bot no sabe nada de este negocio",
             not (await vacio("qué colectivos paran cerca")))

    # El arranque, con este negocio como único tenant.
    tenants_reales = dict(webhook.TENANTS)
    cliente_real = webhook.aturno
    webhook.TENANTS.clear()
    webhook.TENANTS["+10000000000"] = Tenant(
        business_id=NEGOCIO, slug=NEGOCIO, nombre="Prueba",
        numero_whatsapp="+10000000000")
    webhook.aturno = AturnoConConocimiento()

    try:
        await webhook._traer_el_conocimiento()

        r = Recuperador(NEGOCIO, abrir_indice())

        # Las preguntas son las que hace la gente, no las que están escritas:
        # ninguna comparte palabras con el título de su sección.
        casos = [
            ("qué bondi me deja cerca", "24"),
            ("hay subte cerca", "Callao"),
            ("puedo pagar con débito", "débito"),
            ("sobre qué calle están", "Roque Sáenz Peña"),
            ("dónde quedan", "Roque Sáenz Peña"),
            ("hay descuento si pago en efectivo", "7%"),
        ]
        for pregunta, esperado in casos:
            texto = await r.contexto(pregunta)
            chequear(f"«{pregunta}» encuentra la respuesta cargada",
                     esperado in texto, (texto or "sin resultado").splitlines()[0][:46])

        # ---- Y CONTESTA LO QUE LE PREGUNTARON, NO LA SECCIÓN ENTERA ----
        #
        # Acá estaba el bug que hacía que esto no sirviera. El índice cortaba
        # por `##`, pero el panel escribe MUCHAS preguntas dentro de una misma
        # sección. O sea que un fragmento eran seis respuestas distintas
        # pegadas, y eso rompía las dos mitades del pedido:
        #
        #   · encontrar — el vector de un fragmento con seis temas no se parece
        #     lo bastante a ninguno. "dónde quedan" NO recuperaba nada, con la
        #     dirección cargada y con esas dos palabras escritas como sinónimo.
        #   · prolijo  — cuando sí pegaba, contestaba las seis: preguntabas por
        #     el colectivo y recibías dirección, referencia, cochera y rampa.
        #
        # La unidad de sentido no es la sección: es una pregunta y su respuesta.
        leido = P.respuesta_info(await r.contexto("qué colectivos paran cerca"))
        chequear("contesta el colectivo", "24" in leido)
        for sobra in ("cochera", "rampa", "kiosco"):
            chequear(f"y NO le encaja «{sobra}» de la misma sección",
                     sobra not in leido, leido.replace("\n", " · ")[:60])
        chequear("la respuesta entra en pocas líneas",
                 len(leido.splitlines()) <= 4, f"{len(leido.splitlines())} líneas")

        # Y lo que LEE la persona es el texto cargado, no el andamiaje.
        #
        # Se mide sobre `respuesta_info` y no sobre `contexto`: el markdown lo
        # limpia la plantilla, que es el último paso antes de WhatsApp. Medirlo
        # antes daría un falso rojo sobre algo que sí funciona.
        crudo = await r.contexto("cómo llego")
        leido = P.respuesta_info(crudo)
        chequear("no se le filtra la línea de sinónimos del panel",
                 "dónde quedan dónde están" not in leido)
        chequear("ni los ## del markdown", "#" not in leido,
                 leido.splitlines()[0][:46])
        chequear("y la dirección llega entera", "Roque Sáenz Peña 668" in leido)

        # Arrancar de nuevo sin cambios no vuelve a gastar embeddings.
        chequear("un segundo arranque sin cambios no rompe nada",
                 await _segundo_arranque(webhook, r))
    finally:
        webhook.TENANTS.clear()
        webhook.TENANTS.update(tenants_reales)
        webhook.aturno = cliente_real
        reindexar_negocio(NEGOCIO, "")  # no dejar basura en el índice ni en datos/

    print(f"\n{'─' * 58}")
    print(f"{VERDE}El conocimiento sobrevive.{FIN}" if ok
          else f"{ROJO}El bot se olvida lo que le cargan.{FIN}")
    return 0 if ok else 1


async def _segundo_arranque(webhook, r) -> bool:
    """Reinicia y verifica que el conocimiento siga estando."""
    await webhook._traer_el_conocimiento()
    return "24" in await Recuperador(r._business_id, abrir_indice()).contexto(
        "qué bondi me deja cerca")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
