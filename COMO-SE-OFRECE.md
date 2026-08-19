# Cómo se ofrece

Números medidos, no estimados. Lo que es una suposición está marcado como tal.

---

## La decisión ya está tomada en el código de aturno

`backend/src/planes.js` tiene el flag `whatsappBotAI`, y está en `true` en
exactamente tres planes:

| Plan | Precio | Bot con IA |
|---|---:|:---:|
| personal-pro | 19.999 | no |
| **personal-pro-plus** | **29.999** | **sí** |
| business-pro | 34.999 | no |
| **business-pro-plus** | **49.999** | **sí** |
| enterprise | a medida | sí |

O sea que **esto no es un producto nuevo: es lo que justifica el salto al plan
de arriba.** El gating por plan ya está escrito y probado (`planes.test.js`,
63 aserciones). No hay que facturar aparte, ni cobrar aparte, ni construir un
onboarding aparte.

El salto que compra el bot es de **+10.000 en personal y +15.000 en business**,
por mes y por negocio.

---

## Cuánto cuesta atender ese plan

Medido con `medir_costo.py` sobre cuatro conversaciones completas, contando lo
que el proveedor factura:

    US$ 0,00176 por turno    (1.468 tokens de entrada, 58 de salida)

Un negocio que saque **400 turnos por mes** gasta **US$ 0,70 mensuales** en
modelo. Contra un salto de plan de 10.000 pesos, el costo del modelo es ruido.

Cómo se llegó ahí, porque no fue gratis:

| | por turno | mensajes que evitan el LLM |
|---|---:|---:|
| Al principio | US$ 0,0090 | 44% |
| Primera optimización | US$ 0,0052 | 71% |
| **Hoy** | **US$ 0,00176** | **88%** |

Las dos cosas que lo bajaron:

1. **El esquema de la salida estructurada era el 72% de la entrada** — 1.205 de
   1.677 tokens por llamada. No el prompt: el JSON Schema de las clases
   Pydantic, que viaja entero cada vez. Se le sacaron las descripciones
   duplicadas y un campo que no leía nadie.
2. **Las frases de siempre se resuelven en código.** "dale", "me da igual",
   "hablar con alguien" significan lo mismo todas las veces. Cada una que no
   llega al modelo ahorra la llamada completa. La conversación más común
   —alguien que toca los números— pasó de **3 llamadas al modelo a 1**.

---

## El costo real no es el modelo: es la entrega

Y acá está lo importante para el margen.

Una reserva son **8 mensajes** del bot. Con Twilio se paga cada uno. Con la API
de Meta, los **mensajes de servicio** —las respuestas dentro de la ventana de
24 h que abre el cliente— **no cuestan nada**, y este bot no manda otra cosa:
cada mensaje suyo responde a alguien que escribió primero.

    modelo .......... US$ 0,00176 por turno   (medido)
    entrega Twilio .. US$ 0,085   por turno   (17 mensajes × 0,005 — supuesto)
    entrega Meta .... 0                       (mensajes de servicio)

**La entrega por Twilio cuesta 48 veces lo que cuesta el modelo.**

Corrido con `precio.py`, el número que faltaba —hasta cuántos turnos aguanta
el precio del plan— sale así:

| Canal | 400 turnos/mes | margen contra +10.000 | techo del plan |
|---|---:|---:|---:|
| Twilio | US$ 48,70 | **−41,81** 🔴 | ninguno: pierde desde el turno 1 |
| Meta (hosting ÷ 20) | US$ 1,40 | **+5,49** (5×) 🟢 | **3.521 turnos/mes** |

Con Twilio el plan **no cierra a ningún volumen**: la entrega sola se come
varias veces el salto de precio. Con Meta cierra con margen de 3 a 8 veces y
sobra techo para el negocio más grande que uno pueda conseguir.

**Migrar a Meta no es una mejora técnica: es lo que vuelve vendible el
producto.** Está en `PENDIENTES.md` con el detalle de lo que implica.

    python precio.py 400            # con Twilio
    python precio.py 400 --meta --negocios 20

Lo tercero, el hosting, es fijo y se comparte entre todos los negocios: un plan
de Render que no duerma. No escala con la cantidad de clientes.

---

## Qué compra el negocio

No compra "un bot con IA". Eso no le mueve nada a una peluquería.

Compra **no perder el turno de alguien que escribió mientras estaba trabajando**.
El resto —que sea WhatsApp, que no haya que bajar una app, que no haya que
crear cuenta— es lo que hace que la persona del otro lado sí lo use.

Tres cosas que sostienen esa promesa y conviene decir en ese orden:

1. **La agenda es la misma.** Un turno sacado por WhatsApp aparece en el panel
   de aturno igual que uno sacado por la web. No hay dos agendas que conciliar.
   Verificado de punta a punta con `verificar_turno.py`.
2. **El bot no improvisa.** El modelo no redacta: entiende. Todo lo que la
   persona lee sale de plantillas fijas, y lo que el negocio no cargó, el bot
   dice que no lo sabe en vez de inventarlo.
3. **Siempre se puede hablar con una persona.** En cualquier punto, y sin
   perder nada de lo elegido.

---

## Lo que falta resolver antes de venderlo

- **Un número de WhatsApp por negocio.** Hoy el sandbox de Twilio da uno
  compartido y obliga a un "join". Con la API de Meta cada negocio usa el suyo.
  Sin esto no hay producto vendible, solo demo.
- **La seña.** Un servicio con depósito se reserva por WhatsApp sin cobrarlo.
  Toca plata: va primero.
- **Límite de uso por negocio.** Nada impide hoy que un negocio con mucho
  volumen consuma de más. Con estos números no es urgente, pero el plan debería
  decir hasta cuántos turnos incluye.
