# Guion del video — 2 minutos

Un solo video para dos públicos que no miran lo mismo:

- **Jurado CoderCUP** — puntúa Problema real, Ejecución, Originalidad y
  Claridad, 25 puntos cada uno. Quiere ver que *funciona* y entender *qué es*.
- **Spark Cloud (incubadora)** — no puntúa código. Quiere saber qué problema,
  de qué tamaño, y por qué vos.

Lo que sirve para los dos: **el problema primero, la demo real en el medio, el
negocio al final**. Lo técnico entra solo donde explica una decisión, nunca
como lista de tecnologías.

---

## Regla de oro del tiempo

Dos minutos hablados en español son **270-300 palabras**, no más. Este guion
tiene ~280 y deja aire para que se escuche el celular. Si al grabarlo te
apurás, sobra guion: cortá de la sección técnica, nunca de la demo.

---

## Estructura

| Tiempo | Qué se ve | Qué se dice |
|---|---|---|
| 0:00-0:18 | Vos, o un WhatsApp de peluquería real con mensajes sin responder | El problema |
| 0:18-1:05 | **Pantalla del celular, reserva completa** | Casi nada. Que se vea |
| 1:05-1:30 | Phoenix o el diagrama del README | La decisión de diseño |
| 1:30-2:00 | aturno / cifras | Por qué esto es un negocio |

---

## El texto

### 0:00 — El problema (18 s)

> Una peluquería que atiende por WhatsApp pierde turnos por una razón tonta:
> mientras le está cortando el pelo a alguien, no puede contestar. Cuando
> agarra el teléfono, pasaron dos horas y la persona ya reservó en otro lado.
>
> Existen las apps de reservas. El problema es que nadie se baja una app para
> cortarse el pelo.

**Por qué así:** nombra un problema que el jurado puede verificar contra su
propia experiencia, y le pone el dedo a por qué las soluciones que ya existen
no alcanzan. Eso último es lo que separa "problema real" de "problema
inventado".

### 0:18 — La demo (47 s)

> Esto es un turno sacado por WhatsApp, sin descargar nada y sin crear cuenta.

Y después **te callás**. Que se vea la conversación: servicio, profesional,
día, horario, nombre, confirmación. El QR al final.

Un solo comentario en el medio, cuando aparezcan los días:

> Los días cerrados no tienen número. No podés elegir algo que no existe.

**Por qué así:** 47 segundos de producto andando valen más que cualquier
explicación. Es el único tramo del video que prueba "Ejecución", y es lo único
que un inversor mira dos veces.

### 1:05 — La decisión de diseño (25 s)

> Acá el modelo de lenguaje no escribe ni una palabra de lo que la persona lee.
> Solo clasifica la intención. Todo el texto sale de plantillas fijas.
>
> Cuando dejé que el modelo redactara, el saludo cambiaba en cada conversación,
> las listas salían desordenadas y una vez filtró los identificadores internos
> del sistema. En un negocio de verdad eso no se puede.
>
> Y hay algo mejor: el cuarenta y cuatro por ciento de los mensajes ni siquiera
> llegan al modelo. Si alguien contesta "3", eso lo resuelve el código.

**Por qué así:** esto es "Originalidad" y va a contramano de lo que va a
presentar todo el resto —que va a ser un agente conversacional suelto. Va con
la evidencia pegada: no es una opinión de diseño, es lo que pasó cuando probé
lo otro. Y el 44% es un número medido, no una estimación.

### 1:30 — El negocio (30 s)

> Esto no es un bot suelto: se apoya en aturno, la plataforma de reservas que
> ya vengo desarrollando. La agenda, los servicios y los profesionales ya
> están; WhatsApp es la puerta de entrada que faltaba.
>
> Cada reserva cuesta tres centavos de dólar en procesamiento. Migrando a la
> API de Meta, la entrega de los mensajes pasa a costar cero.
>
> [UNA FRASE TUYA SOBRE TRACCIÓN — ver abajo]

**El cierre:**

> El negocio no cambia nada de cómo trabaja. La persona no se baja nada. Y el
> turno queda cargado en la misma agenda de siempre.

---

## Lo que tenés que completar vos

**La frase de tracción.** No la escribo yo porque son datos tuyos y a una
incubadora no se le inventan números — si preguntan y no cierra, perdés más que
el pitch. Poné lo que sea cierto:

- Si aturno tiene negocios usándolo: *"hoy lo usan N negocios"*.
- Si todavía no: *"lo estoy poniendo a prueba con las primeras peluquerías del
  barrio"* — honesto y suficiente. Una incubadora financia gente que ya empezó,
  no gente que ya llegó.

---

## Cómo grabarlo

1. **Antes de todo, abrí `/cupo`.** Si no quedan al menos 8 mensajes, la demo
   se corta a la mitad y no te vas a dar cuenta hasta verla.
2. Grabá la pantalla del celular, no la cámara apuntando al celular.
3. **Hacé la reserva completa una vez antes de grabar**, para que el contenedor
   esté despierto y para saber qué vas a tocar.
4. Grabá la demo **entera y de un saque**, sin cortes. Un corte en el medio de
   la conversación le hace pensar al jurado que algo no anduvo.
5. Grabá el audio aparte y montalo encima. Hablar mientras tocás el celular
   suena mal y te apura.

## Lo que NO va

- La lista de tecnologías. A nadie le suma que digas "LangGraph, Pydantic,
  Chroma". Va en el README, que el jurado también lee.
- Mostrar código. Dos minutos no alcanzan y no prueba nada.
- Pedir disculpas por lo que falta. Si no lo nombrás, nadie lo extraña.
