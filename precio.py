"""
precio.py — Cuánto cuesta atender a un negocio, y hasta dónde cierra el precio.

PARA QUÉ EXISTE
---------------
Para poder sentarse frente a un negocio, preguntarle cuántos turnos saca por
mes, y saber en el momento si el precio del plan cubre el costo y con cuánto
margen. Sin eso, vender es adivinar en qué punto se empieza a perder plata.

    python precio.py 400            # 400 turnos por mes, con Twilio
    python precio.py 400 --meta     # lo mismo con la API de Meta
    python precio.py 400 --dolar 1450

LO QUE HAY QUE ENTENDER ANTES DE MIRAR NINGÚN NÚMERO
----------------------------------------------------
**El modelo no es el costo. La entrega sí.**

Un turno reservado gasta menos de dos milésimas de dólar de Claude. Los mismos
mensajes, entregados por Twilio, cuestan veinte a cuarenta veces eso. Optimizar
prompts al lado de eso es acomodar las sillas: lo que mueve el margen es por
dónde salen los mensajes.

Y ahí está la decisión más importante del producto, que es de plomería y no de
IA: con la API de Meta, los **mensajes de servicio** —los que responden dentro
de la ventana de 24 horas que abre el cliente cuando escribe— **no se cobran**.
Este bot no manda otra cosa: cada mensaje suyo contesta a alguien que escribió
primero. Migrar a Meta no es una mejora técnica, es lo que vuelve el costo
variable casi cero.

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

# Con Meta directo, los mensajes de servicio dentro de la ventana de 24 h no se
# cobran. Cero y no "casi cero": es la categoría entera lo que es gratis.
META_POR_MENSAJE = 0.0

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


def costos(turnos: int, por_mensaje: float, negocios: int) -> dict:
    """El desglose mensual para un negocio con ese volumen."""
    mensajes = turnos * (MENSAJES_DE_LA_PERSONA + MENSAJES_DEL_BOT)
    modelo = turnos * MODELO_POR_TURNO
    entrega = mensajes * por_mensaje
    hosting = HOSTING_MENSUAL / max(1, negocios)
    return {
        "mensajes": mensajes,
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
    p.add_argument("--meta", action="store_true",
                   help="con la API de Meta (mensajes de servicio gratis)")
    p.add_argument("--dolar", type=float, default=1450.0,
                   help="pesos por dólar, para comparar con el precio del plan")
    p.add_argument("--negocios", type=int, default=1,
                   help="entre cuántos negocios se reparte el hosting")
    p.add_argument("--business", action="store_true",
                   help="comparar contra el salto de plan business, no personal")
    a = p.parse_args()

    por_mensaje = META_POR_MENSAJE if a.meta else TWILIO_POR_MENSAJE
    canal = "Meta (API oficial)" if a.meta else "Twilio"
    c = costos(a.turnos, por_mensaje, a.negocios)
    salto = SALTO_BUSINESS if a.business else SALTO_PERSONAL
    salto_usd = salto / a.dolar

    print(f"\n{'═' * 68}")
    print(f"{NEGRITA}  UN NEGOCIO CON {a.turnos} TURNOS POR MES · canal: {canal}{FIN}")
    print("═" * 68)

    print(f"\n  {'COSTO MENSUAL':<34} {'USD':>10}   {'por turno':>10}")
    print(f"  {'-' * 34} {'-' * 10}   {'-' * 10}")
    filas = [
        ("Modelo (Claude Haiku)", c["modelo"], "MEDIDO"),
        (f"Entrega · {c['mensajes']:,.0f} mensajes", c["entrega"],
         "MEDIDO" if a.meta else "SUPUESTO"),
        ("Embeddings (RAG)", c["embeddings"], "entra en el plan gratis"),
        (f"Hosting ÷ {a.negocios} negocio(s)", c["hosting"], "SUPUESTO"),
    ]
    for nombre, valor, nota in filas:
        por_turno = valor / a.turnos if a.turnos else 0
        print(f"  {nombre:<34} {valor:>10.2f}   {por_turno:>10.5f}   {GRIS}{nota}{FIN}")

    print(f"  {'-' * 34} {'-' * 10}   {'-' * 10}")
    print(f"  {NEGRITA}{'TOTAL':<34} {c['total']:>10.2f}   "
          f"{c['total'] / max(1, a.turnos):>10.5f}{FIN}")

    # ---- Contra qué se compara ----
    print(f"\n  {NEGRITA}CONTRA EL PRECIO{FIN}")
    print(f"  El plan con bot cuesta {salto:,} pesos más por mes")
    print(f"  = US$ {salto_usd:.2f} al dólar {a.dolar:,.0f}")

    margen = salto_usd - c["total"]
    veces = salto_usd / c["total"] if c["total"] else 0
    color = VERDE if margen > 0 else ROJO
    print(f"\n  {color}{NEGRITA}margen: US$ {margen:.2f} por mes "
          f"({veces:.0f}× el costo){FIN}")

    # ---- Hasta dónde aguanta ----
    #
    # El número que falta en COMO-SE-OFRECE.md: "el plan debería decir hasta
    # cuántos turnos incluye". Con un costo variable por turno y un ingreso
    # fijo, el punto donde se empatan es una división.
    variable_por_turno = c["variable"] / a.turnos if a.turnos else 0
    if variable_por_turno > 0:
        techo = (salto_usd - c["hosting"]) / variable_por_turno
        print(f"\n  {NEGRITA}A ESTE PRECIO, EL PLAN CIERRA HASTA "
              f"{techo:,.0f} TURNOS/MES{FIN}")
        if techo < a.turnos:
            print(f"  {ROJO}  ⚠ Este negocio está POR ENCIMA: perdés plata.{FIN}")
        elif techo < a.turnos * 2:
            print(f"  {AMARILLO}  ⚠ Este negocio está cerca del techo. "
                  f"Si crece, revisá el precio.{FIN}")
        else:
            print(f"  {GRIS}  Este negocio usa el "
                  f"{a.turnos / techo * 100:.0f}% de lo que el plan aguanta.{FIN}")

    if not a.meta:
        con_meta = costos(a.turnos, META_POR_MENSAJE, a.negocios)
        print(f"\n  {GRIS}Con la API de Meta el mismo negocio costaría "
              f"US$ {con_meta['total']:.2f} en vez de {c['total']:.2f} "
              f"({c['total'] / con_meta['total']:.0f}× menos).{FIN}")
        print(f"  {GRIS}La entrega es el {c['entrega'] / c['total'] * 100:.0f}% "
              f"del costo. El modelo, el "
              f"{c['modelo'] / c['total'] * 100:.0f}%.{FIN}")

    print(f"\n{'═' * 68}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
