# Lo que falta — bot de WhatsApp + panel de aturno

Lista para ejecutar. Cada punto dice **dónde** y **qué**, para que se pueda
tomar sin más contexto que este archivo.

Dos repos:

- **`~/Aturno-WhatsApp`** — el bot (Python, LangGraph, se deploya en Render).
- **`~/Aturno/aturno`** — aturno (React + Express + Firestore). Rama
  `limpieza-estructura`, que es la que deploya Vercel y Render.

---

## A · Panel de conversaciones — lo visual

Componentes: [`ConversacionesWhatsApp.jsx`](../Aturno/aturno/src/components/ConversacionesWhatsApp.jsx),
su `.css`, y el contenedor [`SeccionWhatsApp.jsx`](../Aturno/aturno/src/components/SeccionWhatsApp.jsx).

### A1 · Rehacer la UI con los MCP de componentes

**MCP que están conectados en esta sesión y se pueden usar ya:**

| MCP | Para qué sirve acá |
|---|---|
| `shadcn` | La base: `sidebar`, `scroll-area`, `tabs`, `badge`, `avatar`, `input`, `textarea`, `switch`, `separator`, `tooltip`, `sonner` (toasts). Es lo que da la estructura. |
| `magicui` | Los detalles con movimiento: `animated-list` para la lista de chats, `blur-fade` al abrir un hilo, `shimmer-button`, `number-ticker` para el contador de "esperando". |
| `21st` (21st.dev) | Buscar variantes de chat/inbox ya armadas antes de escribir una desde cero: `mcp__21st__search` con "chat inbox", "conversation list", "messenger". |

**Los otros dos que pediste NO están conectados como MCP en esta sesión:**

- **React Bits** — no aparece en la lista de servidores. Hay que agregarlo
  (`claude mcp add`) o copiar los componentes a mano desde reactbits.dev.
- **Deca Icons** — tampoco. Mientras tanto, aturno ya usa **lucide-react**;
  conviene no mezclar dos sets de íconos en la misma pantalla.

Antes de empezar: `mcp__shadcn__get_project_registries` para ver qué registries
tiene configurado el proyecto, y `mcp__shadcn__get_audit_checklist` al terminar.

**Ojo con dos cosas del proyecto:**

- `.dashboard-container` apaga todo `transform` y toda `animation` con
  `!important` (`Dashboard.css:6869`). Cualquier componente animado que se
  renderice **adentro** del dashboard va a quedar quieto. Los de Radix se
  salvan porque van en un portal. Si Magic UI no anima, es esto.
- El panel ya tiene un sistema de estilos propio. No meter Tailwind global sin
  ver cómo convive.

### A2 · Orden de la lista: lo más nuevo arriba

No está hardcodeado. El backend
([`server.js:11631`](../Aturno/aturno/backend/server.js)) ordena así:

```js
if (!!a.necesitaHumano !== !!b.necesitaHumano) return a.necesitaHumano ? -1 : 1;
return String(b.ultimoMomento).localeCompare(String(a.ultimoMomento));
```

O sea: **primero los que piden una persona**, y recién después por fecha. Lo
que ves arriba es `+5491155667788`, del 17/08, con `necesitaHumano: true`
colgado desde entonces. Por eso un chat viejo le gana a uno de hoy.

- [ ] Decidir el criterio. Recomendado: ordenar **siempre por
      `ultimoMomento` descendente**, y marcar los que esperan una persona con
      un badge rojo + un filtro "Esperando (N)" arriba de la lista. Que el
      orden signifique una sola cosa —qué pasó último— y la urgencia se vea,
      no se cuele en el orden.
- [ ] Que `necesitaHumano` se apague solo después de X horas sin actividad, o
      cuando el negocio abre el chat. Hoy queda prendido para siempre.

### A3 · Borrar los números de prueba

En Firestore, `whatsapp_conversations/1lNTQH2bZAMbzmEwQcFPijSyoyN2`, campo
`resumenes`. Hay tres entradas y dos son inventadas:

- `+5491155667788` — "hola? me leen?"
- `+5491100000000` — "prueba de conexión"

La única real es `+5491130032002` (tu número).

- [ ] Borrar esas dos claves de `resumenes` y sus mensajes en la subcolección
      del hilo.
- [ ] Mientras existan, la lista va a seguir mostrando el chat del 17 arriba.

### A4 · El chat abre en el último mensaje

[`ConversacionesWhatsApp.jsx:194`](../Aturno/aturno/src/components/ConversacionesWhatsApp.jsx#L194):

```js
const estabaAbajo = caja.scrollHeight - caja.scrollTop - caja.clientHeight < 80;
if (estabaAbajo) caja.scrollTop = caja.scrollHeight;
```

Eso está bien **para los mensajes que llegan mientras mirás** (no te arrastra
si estás leyendo más arriba). El problema es que al **abrir** un hilo la caja
arranca en 0, `estabaAbajo` da `false`, y te deja en el primer mensaje de la
conversación.

- [ ] Separar los dos casos: al cambiar de conversación, saltar al fondo
      **siempre** (sin animación, en el mismo frame, para que no se vea el
      salto). Al llegar un mensaje nuevo, seguir con la regla actual.
- [ ] No usar `scrollIntoView` — mueve el documento entero y ya nos hizo
      scrollear la página sola una vez.

### A5 · "Tomar control" siempre visible

Hoy el botón está atado a que la persona haya pedido un humano.

- [ ] Que el botón esté **siempre** en la cabecera del chat abierto.
- [ ] Que la **notificación** (la del navegador + el sonido) siga saliendo
      sólo cuando alguien pide hablar con una persona. Eso ya funciona y está
      bien: se avisa de lo que empieza a esperar mientras mirás, no de lo
      viejo al abrir el panel.
- [ ] Que se vea claro en qué estado está la conversación. Son **dos**
      banderas distintas y confundirlas ya rompió el panel una vez:
      `necesitaHumano` = el cliente está esperando; `enManosHumanas` = el bot
      se calló y la conversación es del negocio. El botón "Devolver al bot"
      depende de la segunda.

### A6 · Que quede lindo

- [ ] Burbujas diferenciadas por autor (cliente / bot / negocio) — hoy el bot
      y el negocio se confunden.
- [ ] Hora en cada mensaje y separador por día.
- [ ] Avatar o inicial por número; nombre del cliente cuando se conoce.
- [ ] Estado vacío decente ("todavía no te escribió nadie") en vez de una
      lista en blanco.
- [ ] Skeletons mientras carga, no un salto.
- [ ] Que ande en celular: hoy la lista y el hilo conviven en dos columnas.

---

## B · Tab nuevo: el formulario que le enseña al bot

Los tabs se declaran en
[`SeccionWhatsApp.jsx:22`](../Aturno/aturno/src/components/SeccionWhatsApp.jsx#L22).
Hoy hay dos: `conversaciones` y `asistente`.

- [ ] Agregar un tercero: **"Qué contesta"** (o "Respuestas").

**Qué tiene que hacer.** Cuando alguien le pregunta algo al bot y el bot no
sabe, contesta *"ese dato no lo tengo cargado"* y esa pregunta se pierde. La
idea es que no se pierda: que quede en una bandeja y el dueño la conteste una
vez.

- [ ] **Bandeja de preguntas sin responder.** Cada vez que el RAG no alcanza
      el umbral de relevancia (0.60), guardar la pregunta con el teléfono y la
      fecha. Ordenadas por cuántas veces la preguntaron.
- [ ] **Responder desde el panel.** Por cada pregunta: escribir la respuesta,
      o marcarla como "no aplica". Lo que se responde se agrega a la base de
      conocimiento del negocio.
- [ ] **Preguntas de sí/no/otro.** El formulario que hablamos: en vez de
      escribir un documento, el dueño responde una lista corta —"¿Aceptás
      tarjeta?", "¿Hay estacionamiento?", "¿Se puede ir con chicos?"— con
      sí / no / otro + texto libre. Cada respuesta se convierte en un
      fragmento del RAG.
- [ ] **Vista previa.** Antes de guardar, mostrar cómo le va a contestar el
      bot con eso cargado.
- [ ] **Del lado del bot:** al guardar, reindexar Chroma para ese negocio.
      Ver `src/rag/` en `~/Aturno-WhatsApp`. Ojo con el cupo de embeddings
      (abajo, punto D3).

---

## C · Del lado del bot — lo que falta funcionalmente

De `PENDIENTES.md`. Ordenado por lo que más duele.

### C1 · Bloquea la entrega

- [ ] **`ATURNO_WEB_URL`** no está cargada en el servicio `aturno-whatsapp` de
      Render. Es la única variable que falta.
- [ ] **Deployar el backend de aturno** con el arreglo de `availableResources`
      que ya está commiteado en `limpieza-estructura`.
- [ ] **El video de 2 minutos** para la CoderCUP (23/8). Guion en
      `GUION-VIDEO.md`.

### C2 · Lo que el bot todavía no hace

- [ ] **No cancela ni reprograma** por WhatsApp. Se dejó afuera a propósito
      (aturno ya lo hace por código), pero es lo primero que va a pedir
      cualquiera que lo use.
- [ ] **No maneja señas.** Los servicios con `requiresDeposit: true` se
      reservan igual, sin cobrar nada. El turno queda en `pending_deposit` y
      nadie avisa.
- [ ] **La sesión no vence.** Alguien deja una conversación a medias, vuelve a
      los tres días y el bot sigue en el mismo paso como si no hubiera pasado
      nada.
- [ ] **Sin límite por teléfono.** Nada impide que un mismo número mande cien
      mensajes y queme el cupo de Twilio y de Gemini.
- [ ] **El RAG no cita de dónde sacó la respuesta.**

### C3 · Higiene

- [ ] `todos_los_caminos.py --real` deja **2 turnos** en la agenda (de los 49
      caminos, dos terminan confirmando) y no los cancela. Agregarle la
      limpieza, igual que `verificar_turno.py`.
- [ ] Crear una **segunda cuenta de aturno para pruebas** (slug tipo
      `pruebas`) y apuntar ahí los scripts. Escribir en la agenda que vas a
      mostrar en el video es pedir problemas.
- [ ] Decidir si se revierte el código de notificación por mail — está escrito
      pero inerte (falta `RESEND_API_KEY`) y dijiste que no lo ibas a usar.
- [ ] Correr `test_aislamiento.py` cuando se reponga el cupo de Gemini.

---

## D · Techos conocidos

- [ ] **Twilio Sandbox:** 50 mensajes por día, y cada persona tiene que mandar
      un "join" que vence a los 3 días. Para producción hay que migrar a la
      API de Meta.
- [ ] **Un solo número** de WhatsApp para todos los negocios.
- [ ] **Embeddings de Gemini:** 1.000 por día para *todo el proyecto*, no por
      negocio. Salidas: plan pago, modelo local (`EMBEDDINGS_MODO=local`,
      +805 MB) o cachear las preguntas frecuentes.

---

## E · Seguridad — antes de que lo use alguien de verdad

Está todo en `SEGURIDAD.md`. Lo urgente:

- [ ] **Rotar la clave de Firebase.** `serviceAccount.json` quedó en el
      historial de git de `Mati2108/Aturno` (commit `769d505`). El repo es
      privado, pero la clave hay que rotarla igual desde la consola.
- [ ] Rotar la API key de Gemini, el auth token de Twilio y la de Anthropic:
      las tres pasaron por un chat.
- [ ] Rotar el secreto compartido del panel.
