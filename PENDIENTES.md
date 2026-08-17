# Pendientes

Lo que falta, lo que está a medias y lo que se decidió dejar afuera. Ordenado
por lo que más duele.

---

## 🔴 Bloquea la entrega

### 1. Las credenciales en Render están mal cargadas

**Síntoma:** el bot desplegado recibe los mensajes y no contesta. En los logs
aparecen dos errores 401 seguidos — uno del clasificador, otro al enviar por
Twilio.

**Causa probable:** el `TWILIO_ACCOUNT_SID` y el `TWILIO_AUTH_TOKEN` quedaron
cruzados al pegarlos. El error de Twilio dice *"invalid username"*, y en Twilio
el username es el Account SID. Miden 34 y 32 caracteres, van uno debajo del
otro en el formulario y es fácil equivocarse.

**Cómo verificarlo ahora:** abrir `/diagnostico` en el servicio desplegado.
Dice cuál credencial falla y por qué, sin exponer ningún valor.

**Los valores correctos:**

| Variable | Largo | Empieza | Termina |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | 108 | `sk-ant-` | `vwAA` |
| `TWILIO_ACCOUNT_SID` | 34 | `AC126c1` | `a0c9` |
| `TWILIO_AUTH_TOKEN` | 32 | `9156503` | `b6ed` |
| `GEMINI_API_KEY` | 53 | `AQ.Ab8R` | `ZVlw` |
| `PUBLIC_URL` | — | `https://aturno-whatsapp.onrender.com` | |

### 2. El servicio se duerme

Render free apaga el contenedor a los 15 minutos sin uso, y despertarlo lleva
50 segundos o más. Twilio abandona el webhook a los ~15 segundos, así que
**el primer mensaje después de una pausa se pierde entero** — la persona
escribe y no recibe nada.

Para el jurado esto es fatal: prueba una vez, no pasa nada, se va.

Opciones: un ping externo cada 10 minutos (gratis, cron-job.org), o pasar a
un plan que no duerma.

### 3. No hay video

25 de los 100 puntos de la CoderCUP son "Claridad: ¿tu video explica el qué y
el cómo?". Además la consigna aclara que el primer filtro se hace sobre la
explicación del proyecto.

---

## 🟡 Deuda que conviene saldar

### 4. Habla con un doble, no con aturno

`AturnoDoble` guarda los turnos en memoria. Funciona y está testeado, pero un
turno sacado por WhatsApp **no aparece en la agenda real del negocio**.

El contrato ya está definido (`src/aturno/base.py`) y la implementación contra
la API real es la pieza que falta. Los endpoints que hacen falta son públicos
en aturno: `POST /api/bookings/check-availability` y `POST /api/bookings`.

Se activa con `ATURNO_MODO=api` + `ATURNO_API_URL`.

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

La cuenta trial de Twilio corta a los 50 mensajes diarios. Entre pruebas y
jurado se agota rápido, y cuando pasa el bot deja de responder sin decir nada.
Verificar la cuenta lo levanta.

---

## 🟢 Mejoras del bot

Salieron de usarlo, no de imaginarlo.

- **No cancela ni reprograma.** Se dejó afuera a propósito; aturno ya lo
  resuelve y un bot que hace una cosa bien vale más que uno que hace cinco a
  medias.
- **No maneja señas.** Los servicios con depósito previo (coloración pide 30%)
  se reservan sin cobrarlo.
- **"Ver más horarios"** está en la plantilla pero la intención `VER_MAS` no
  está cableada: si alguien pide "más", cae en desconocido.
- **La sesión no vence.** Si alguien deja una conversación a medias y vuelve a
  la semana, sigue en el mismo paso. Debería reiniciarse a los 30 minutos.
- **El RAG no cita la fuente.** Contesta con el fragmento pero no dice de qué
  sección salió; para el negocio sería útil saber qué parte de su documento se
  está usando.
- **Sin límite de tasa por teléfono.** Nada impide que alguien mande cien
  mensajes y gaste el saldo de la API.

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
