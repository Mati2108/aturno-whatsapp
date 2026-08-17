# Pendientes

Lo que falta, lo que está a medias y lo que se decidió dejar afuera. Ordenado
por lo que más duele.

---

## 🔴 Bloquea la entrega

### 1. La clave de Anthropic en Render está cortada

**Síntoma:** el bot desplegado recibe los mensajes y no contesta.

**Causa, ya confirmada con `/diagnostico`:** al pegar la `ANTHROPIC_API_KEY` en
Render se perdió el primer carácter. Quedó con 107 caracteres empezando en
`k-ant-` cuando tiene que tener 108 y empezar en `sk-ant-`. Falta una sola
letra y eso alcanza para un 401 en cada mensaje.

Lo de Twilio (SID y token cruzados) ya está corregido: ese chequeo da ok.

**Cómo verificarlo:** abrir `/diagnostico` en el servicio desplegado. Dice cuál
credencial falla y por qué, sin exponer ningún valor. Las tres tienen que decir
`"valida": true`.

**Los valores correctos:**

| Variable | Largo | Empieza | Termina |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | 108 | `sk-ant-` | `vwAA` |
| `TWILIO_ACCOUNT_SID` | 34 | `AC126c1` | `a0c9` |
| `TWILIO_AUTH_TOKEN` | 32 | `9156503` | `b6ed` |
| `GEMINI_API_KEY` | 53 | `AQ.Ab8R` | `ZVlw` |
| `PUBLIC_URL` | — | `https://aturno-whatsapp.onrender.com` | |

### 2. El servicio se duerme — resuelto, falta verificar

Render free apaga el contenedor a los 15 minutos sin uso. Medido: despertarlo
tardó **87 segundos**. Twilio abandona el webhook a los ~15, así que **el
primer mensaje después de una pausa se perdía entero** — la persona escribía y
no recibía nada. Para alguien que prueba una sola vez, eso es un bot roto.

Solución puesta: `.github/workflows/despertador.yml` le pega a `/salud` cada 10
minutos desde GitHub Actions. Gratis en repos públicos y sin cuentas nuevas.

Queda por confirmar que GitHub lo esté disparando (pestaña Actions del repo).
Y anotado para dentro de unos meses: GitHub desactiva los cron de un repo sin
actividad por 60 días.

### 3. No hay video

25 de los 100 puntos de la CoderCUP son "Claridad: ¿tu video explica el qué y
el cómo?". Además la consigna aclara que el primer filtro se hace sobre la
explicación del proyecto.

---

## 🟡 Deuda que conviene saldar

### 4. La integración con aturno está escrita pero sin probar contra un negocio real

`src/aturno/api.py` implementa el contrato contra el backend real, usando solo
endpoints públicos — los mismos que usa la página de reservas para alguien sin
cuenta. Sin service account, sin token de admin, sin clave de Firebase.

Se activa con `ATURNO_MODO=api` + `ATURNO_API_URL`.

**Lo verificado contra el negocio real `aturno`:** lee bien servicios
(Dentista), staff (Juan Demo, Matias Calo) y el horario del negocio, incluido
que los viernes está cerrado. **Lo que falta:** crear un turno, bloqueado por
lo de abajo.

#### El backend desplegado está atrasado

`aturno-backend` en Render sirve un commit anterior al **59a0315 (6/8/2026)**.
Comprobado: responde a `check-availability`, que no existe en `main`, y no
responde a `/ocupacion` ni a `/horarios-ocupados`, que sí están en
`limpieza-estructura`. O sea que Render ya sigue la rama correcta y quedó
clavado en un commit viejo. **No hay que mergear nada**: alcanza con
"Manual Deploy → Deploy latest commit".

Mientras tanto, el deploy viejo tiene la versión rota de `check-availability`
—la que arrancaba `available` en `false` y contestaba "no disponible" para
todos los horarios de todos los servicios, según el comentario del propio
código—. Nadie lo había notado porque el frontend no llama a ese endpoint: este
bot es su primer consumidor real.

Dos cosas más que salieron de ahí:

- `serviceId` tiene que viajar como **string**. Con el id numérico el backend
  tira 500 (`db.collection(...).doc(number)`). El adaptador ya manda string.
- El negocio `aturno` tiene `timeZone: "America/New_York"`. De ahí salen los
  eventos de Google Calendar y los recordatorios: hay que corregirlo en el
  panel o los avisos van a llegar con horas de diferencia.

El adaptador funciona igual contra un backend viejo: si falta
`/horarios-ocupados`, pregunta horario por horario con `check-availability`
(concurrencia limitada a 6).

    python probar_aturno_real.py <slug>              # solo lee
    python probar_aturno_real.py <slug> --reservar   # crea un turno real

El negocio tiene que tener servicios y horarios cargados. Si el staff no tiene
horario propio, aturno lo trata como que no atiende — y el bot hace lo mismo.

Falta también un `datos/<slug>.md` para el RAG: los archivos de conocimiento se
llaman como el `business_id`, que ahora es el slug de aturno.

### 5. Un solo número de WhatsApp

El ruteo por negocio está hecho —sale del campo `To` del webhook— pero el
sandbox de Twilio da un único número compartido. Para tener un número por
negocio hay que verificar la cuenta con Meta, que tarda días.

Mientras tanto, el aislamiento entre negocios está probado a nivel de RAG y de
estado, pero no se puede demostrar en vivo con dos números.

### 6. El sandbox obliga a un "join" y vence

Cada persona que quiera probar el bot tiene que mandar `join light-trail`
primero, y la sesión vence a los pocos días de inactividad. Si el jurado
prueba una semana después, tiene que volver a unirse.

Hay que incluir esa instrucción en el formulario de entrega, bien visible.

### 7. Cupo de 50 mensajes por día

La cuenta trial de Twilio corta a los 50 mensajes **por ventana móvil de 24
horas** — no se repone a medianoche: cada mensaje se libera al cumplir 24h.

Cuando se agota, el síntoma es el peor posible: el bot procesa todo bien y la
persona no recibe nada. Desde afuera es indistinguible de un bot caído. Es lo
que pasó el 17/8 con 59 mensajes en la ventana.

`GET /cupo` dice cuánto margen queda antes de filmar o de mandarle el link a
alguien, traducido a reservas completas (8 mensajes del bot cada una).

Se levanta pasando la cuenta de Twilio a paga (~USD 20). Twilio avisa que
tarda 3-4 horas en propagar, así que no dejarlo para el día de filmar.

### 8. Migrar a la API de Meta — el paso que borra el costo de entrega

En la Cloud API de Meta los **mensajes de servicio** (respuestas dentro de la
ventana de 24h que abre el cliente) son ilimitados y no cuentan para ningún
tope. Este bot no manda otra cosa: cada mensaje suyo responde a alguien que
escribió primero. O sea que ahí el tope directamente no existe, y el costo por
reserva baja a solo el del LLM (US$ 0,0349).

No se hizo antes de la entrega a propósito: requiere cuenta de Meta Business,
app, un número que no esté ya en WhatsApp y token permanente, más reescribir el
adaptador de envío y la validación de firma. Nada de eso suma puntos en la
consigna, y el número de prueba de Meta solo puede hablar con destinatarios
pre-registrados — inservible para que un jurado lo pruebe.

Se pierde el indicador de "escribiendo…": Meta no lo expone (verificado en su
documentación). Twilio sí.

Descartado en el camino: **CloudTalk**. Es un call center en la nube, no un
proveedor de WhatsApp Business API. Conectarlo exige pegamento de terceros
(Latenode, Pipedream), o sea una capa más que cobra y que puede fallar.

---

## 🟢 Mejoras del bot

Salieron de usarlo, no de imaginarlo.

- **No cancela ni reprograma.** Se dejó afuera a propósito; aturno ya lo
  resuelve y un bot que hace una cosa bien vale más que uno que hace cinco a
  medias.
- **No maneja señas.** Los servicios con depósito previo (coloración pide 30%)
  se reservan sin cobrarlo.
- **La sesión no vence.** Si alguien deja una conversación a medias y vuelve a
  la semana, sigue en el mismo paso. Debería reiniciarse a los 30 minutos.
- **El RAG no cita la fuente.** Contesta con el fragmento pero no dice de qué
  sección salió; para el negocio sería útil saber qué parte de su documento se
  está usando.
- **Sin límite de tasa por teléfono.** Nada impide que alguien mande cien
  mensajes y gaste el saldo de la API.

---

---

## ✅ Arreglado, anotado para que no vuelva

Los tres salieron de usar el bot desde la terminal (`chatear.py`), no de leer
el código. Ninguno lo habrían encontrado los tests: los tres pasaban.

| Qué pasaba | Por qué |
|---|---|
| A las 12:55 ofrecía las 09:00 **de hoy**, y las aceptaba | El doble filtraba días pasados pero no horas pasadas |
| Después de pedir "más", la pantalla decía 17:00 y el "1" guardaba las 13:00 | El número se resolvía contra la lista completa y no contra la mostrada |
| "más" caía en "no entendí" | La plantilla lo ofrecía y la intención no estaba cableada |

Y la causa de fondo del primero: **todo el proyecto usaba `date.today()` sin
huso**. El contenedor corre en UTC, así que desde las 21:00 de Argentina el bot
creía que era mañana y adelantaba tres horas. Ahora hay un solo lugar que
resuelve el tiempo (`src/fechas.py`: `hoy()` y `ahora()`) y nadie más llama a
`date.today()`.

---

## ⚪ Decisiones tomadas, no pendientes

Acá para que no se replanteen sin motivo.

| Decisión | Por qué |
|---|---|
| El LLM no redacta | Con el modelo redactando el saludo cambiaba, los listados salían horizontales y se filtraron los ids internos |
| Embeddings por API por defecto | El modelo local suma 805 MB y no entra en un plan chico |
| La aritmética de fechas en código | El modelo resolvió "el lunes que viene" como un domingo y reservó igual |
| Los días cerrados no llevan número | Numerarlos ofrece algo que al tocarlo rebota |
| El webhook contesta vacío | Twilio corta si demorás y la persona recibe todo dos veces |
| Python y no Node | El Capstone exige LangGraph y Pydantic. Portarlo a Node es una opción post-entrega |
