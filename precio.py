"""
precio.py — Cuánto cuesta atender a un negocio, y hasta dónde cierra el precio.

PARA QUÉ EXISTE
---------------
Para poder sentarse frente a un negocio, preguntarle cuántos turnos saca por
mes, y saber en el momento si el precio del plan cubre el costo y con cuánto
margen. Sin eso, vender es adivinar en qué punto se empieza a perder plata.

    python precio.py 400                      # el mundo desde octubre
    python precio.py 400 --canal meta-oct     # Meta directo, desde octubre
    python precio.py 400 --negocios 20 --margen 5 --dolar 1450

LO QUE HAY QUE ENTENDER ANTES DE MIRAR NINGÚN NÚMERO
----------------------------------------------------
**El modelo no es el costo. La entrega sí.**

Un turno reservado gasta menos de dos milésimas de dólar de Claude. Los mismos
mensajes, entregados, cuestan entre cincuenta y cien veces eso. Optimizar
prompts al lado de eso es acomodar las sillas: lo que mueve el margen es por
dónde salen los mensajes.

LA FECHA QUE MANDA: 1 DE OCTUBRE DE 2026
----------------------------------------
Desde noviembre de 2024, Meta no cobra los **mensajes de servicio** —las
respuestas dentro de la ventana de 24 h que abre el cliente cuando escribe—, y
este bot no manda otra cosa: cada mensaje suyo contesta a alguien que escribió
primero. Eso hacía que por Meta la entrega saliera CERO.

**El 1 de octubre de 2026 Meta vuelve a cobrarlos**, a la misma tarifa que las
plantillas de utility de cada país. Las tarifas finales las publica el 1 de
septiembre.

O sea que "migrar a Meta y la entrega es gratis" tiene fecha de vencimiento.
Migrar sigue conviniendo —Meta cobra sólo los mensajes del negocio y Twilio
cobra los dos, más su recargo, así que Meta directo termina costando la mitad—
pero deja de ser gratis y el precio de venta tiene que cubrirlo.

MEDIDO VS SUPUESTO
------------------
Lo que sale de correr el código está marcado MEDIDO y se puede rehacer. Lo que
sale de una lista de precios de un tercero está marcado SUPUESTO y hay que
confirmarlo antes de cerrar un trato: las tarifas cambian y no las controlamos.

MEDIDO VS SUPUESTO
------------------
Lo que sale de correr el código está marcado MEDIDO y se puede rehacer. Lo que
sale de una lista de precios de un tercero está marcado SUPUESTO y hay que
confirmarlo antes de cerrar un trato: las tarifas cambian y no las controlamos.
"""

from __future__ import annotations

import argparse

VERDE, ROJO, AMARILLO, GRIS, NEGRITA, FIN = (
    "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[1m", "\033[0m")


# ---------- Lo medido ----------

# MEDIDO con `medir_costo.py` sobre cuatro conversaciones completas: lo que el
# proveedor factura, no una estimación. Incluye el prompt del sistema, que
# viaja en cada llamada y es lo que toda cuenta de servilleta se olvida.
MODELO_POR_TURNO = 0.00176

# MEDIDO: los guiones de `medir_costo.py` son de 7, 7, 9 y 11 mensajes de la
# persona. El bot contesta uno por cada uno. Se cuentan los DOS sentidos porque
# Twilio cobra los dos.
MENSAJES_DE_LA_PERSONA = 8.5
MENSAJES_DEL_BOT = 8.5


# ---------- Lo supuesto, a confirmar ----------

# SUPUESTO: tarifa pública de Twilio para WhatsApp, por mensaje y en los dos
# sentidos. Confirmar en twilio.com/whatsapp/pricing antes de cerrar: varía por
# país y por volumen contratado.
TWILIO_POR_MENSAJE = 0.005

# Con Meta directo, HASTA EL 30 DE SEPTIEMBRE DE 2026, los mensajes de servicio
# dentro de la ventana de 24 h no se cobran. Cero y no "casi cero": es la
# categoría entera lo que es gratis.
META_POR_MENSAJE = 0.0

# EL 1 DE OCTUBRE DE 2026 ESO SE TERMINA.
#
# Meta vuelve a cobrar los mensajes de servicio —las respuestas de texto libre
# dentro de la ventana de 24 h—, que es exactamente lo único que manda este bot.
# Fueron gratis desde noviembre de 2024 hasta septiembre de 2026, y esa ventana
# se cierra. Las tarifas finales las publica Meta el 1 de septiembre.
#
# SUPUESTO: 0,0120 USD por mensaje para Argentina, que es la tarifa de utility
# —a la que Meta dijo que va a igualar las de servicio—. CONFIRMAR EN
# SEPTIEMBRE: de este número depende el precio de venta entero.
#
# Ojo con la asimetría: Meta cobra los mensajes del NEGOCIO, no los de la
# persona. Twilio cobra los dos. Por eso no se multiplican por lo mismo.
META_DESDE_OCTUBRE = 0.0120

# Lo que Twilio suma ARRIBA de la tarifa de Meta, por su cuenta y por mensaje en
# los dos sentidos. Después de octubre se pagan las dos cosas.
TWILIO_RECARGO = 0.005

# SUPUESTO: Render Starter para el bot más su Postgres. El plan gratuito NO
# sirve en producción —duerme a los 15 minutos y el primer mensaje después se
# pierde entero— así que esto es piso, no opcional.
#
# Es un costo FIJO y COMPARTIDO: no escala con la cantidad de negocios, así que
# cuantos más clientes haya, menos pesa en cada uno. Por eso entra dividido.
HOSTING_MENSUAL = 14.0

# Los embeddings del RAG: el plan gratuito de Gemini da 1.000 por día, o sea
# 30.000 por mes, y sólo se consume uno por PREGUNTA (no por turno). A los
# volúmenes de un negocio local no se toca ni de cerca. Se deja anotado para
# que no parezca olvidado.
EMBEDDINGS = 0.0

# SUPUESTO: lo que el negocio paga de más por el plan que incluye el bot.
# Sale de `backend/src/planes.js` — el flag `whatsappBotAI` separa
# personal-pro (19.999) de personal-pro-plus (29.999).
SALTO_PERSONAL = 10_000
SALTO_BUSINESS = 15_000


# Los cuatro escenarios de entrega, y cuánto cobra cada uno por qué.
#
# `del_bot` se cobra por cada mensaje que manda el bot; `de_ambos`, por cada
# mensaje en cualquier dirección. La distinción no es cosmética: Meta cobra sólo
# los del negocio y Twilio cobra los dos, así que mezclarlos da casi el doble.
CANALES = {
    "twilio": ("Twilio (hoy)", 0.0, TWILIO_RECARGO),
    "meta": ("Meta directo (hasta el 30/9/2026)", META_POR_MENSAJE, 0.0),
    "meta-oct": ("Meta directo (desde el 1/10/2026)", META_DESDE_OCTUBRE, 0.0),
    "twilio-oct": ("Twilio (desde el 1/10/2026)", META_DESDE_OCTUBRE, TWILIO_RECARGO),
}


def costos(turnos: int, del_bot: float, de_ambos: float, negocios: int) -> dict:
    """El desglose mensual para un negocio con ese volumen."""
    salientes = turnos * MENSAJES_DEL_BOT
    todos = turnos * (MENSAJES_DE_LA_PERSONA + MENSAJES_DEL_BOT)
    modelo = turnos * MODELO_POR_TURNO
    entrega = salientes * del_bot + todos * de_ambos
    hosting = HOSTING_MENSUAL / max(1, negocios)
    return {
        "mensajes": todos,
        "modelo": modelo,
        "entrega": entrega,
        "embeddings": EMBEDDINGS,
        "hosting": hosting,
        "variable": modelo + entrega + EMBEDDINGS,
        "total": modelo + entrega + EMBEDDINGS + hosting,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("turnos", type=int, help="turnos por mes que saca el negocio")
    p.add_argument("--canal", choices=list(CANALES), default="twilio-oct",
                   help="por dónde salen los mensajes (default: el mundo desde octubre)")
    p.add_argument("--dolar", type=float, default=1450.0, help="pesos por dólar")
    p.add_argument("--negocios", type=int, default=1,
                   help="entre cuántos negocios se reparte el hosting")
    p.add_argument("--margen", type=float, default=4.0,
                   help="cuántas veces el costo querés cobrar (default 4)")
    a = p.parse_args()

    etiqueta, del_bot, de_ambos = CANALES[a.canal]
    c = costos(a.turnos, del_bot, de_ambos, a.negocios)
    por_turno = c["total"] / max(1, a.turnos)
    variable = c["variable"] / max(1, a.turnos)

    print(f"\n{'=' * 70}")
    print(f"{NEGRITA}  {a.turnos} TURNOS/MES  ·  {etiqueta}{FIN}")
    print("=" * 70)

    print(f"\n  {'TE CUESTA POR MES':<36} {'USD':>9}   {'por turno':>10}")
    print(f"  {'-' * 36} {'-' * 9}   {'-' * 10}")
    for nombre, valor, nota in [
        ("Modelo (Claude Haiku)", c["modelo"], "MEDIDO"),
        (f"Entrega · {c['mensajes']:,.0f} mensajes", c["entrega"], "SUPUESTO"),
        ("Embeddings (RAG)", c["embeddings"], "plan gratis"),
        (f"Hosting ÷ {a.negocios} negocio(s)", c["hosting"], "SUPUESTO"),
    ]:
        print(f"  {nombre:<36} {valor:>9.2f}   {valor / max(1, a.turnos):>10.5f}"
              f"   {GRIS}{nota}{FIN}")
    print(f"  {'-' * 36} {'-' * 9}   {'-' * 10}")
    print(f"  {NEGRITA}{'COSTO':<36} {c['total']:>9.2f}   {por_turno:>10.5f}{FIN}")

    if c["total"]:
        pct = c["entrega"] / c["total"] * 100
        print(f"\n  {GRIS}La entrega es el {pct:.0f}% del costo. "
              f"El modelo, el {c['modelo'] / c['total'] * 100:.0f}%.{FIN}")

    # ---- El número que se le pasa al negocio ----
    precio = c["total"] * a.margen
    print(f"\n  {NEGRITA}LE COBRÁS (a {a.margen:g}× el costo){FIN}")
    print(f"    {VERDE}{NEGRITA}US$ {precio:>8.2f} / mes   =   "
          f"$ {precio * a.dolar:>11,.0f} pesos{FIN}")
    print(f"    {GRIS}ganás US$ {precio - c['total']:.2f} por mes con este negocio{FIN}")

    # ---- Para armar tramos ----
    #
    # Con precio variable por cliente, lo que hace falta no es UN número sino
    # saber cuánto sube el costo por cada turno de más: es lo que permite decir
    # "hasta 300 incluidos, el excedente a tanto" sin perder plata.
    print(f"\n  {NEGRITA}PARA ARMAR TRAMOS{FIN}")
    print(f"    cada turno de más te cuesta  US$ {variable:.5f}"
          f"   = $ {variable * a.dolar:,.2f} pesos")
    print(f"    cobrándolo a {a.margen:g}×          US$ {variable * a.margen:.5f}"
          f"   = $ {variable * a.margen * a.dolar:,.2f} pesos por turno")

    print(f"\n  {NEGRITA}EL MISMO NEGOCIO POR CADA CANAL{FIN}")
    for clave, (nom, db, da) in CANALES.items():
        o = costos(a.turnos, db, da, a.negocios)
        marca = "◀" if clave == a.canal else " "
        print(f"    {marca} {nom:<38} US$ {o['total']:>7.2f}/mes"
              f"   {o['total'] / max(1, a.turnos):>8.5f}/turno")

    print(f"\n{'=' * 70}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
