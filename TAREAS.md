# Tareas

Ordenado por lo que más duele. Cada punto dice **dónde**, **qué**, **por qué** y
**cómo se verifica**, para que se pueda tomar sin más contexto que este archivo.

Dos repos:

- **`~/Aturno-WhatsApp`** — el bot (Python, se deploya en Render).
- **`~/Aturno/aturno`** — aturno (React + Express + Firestore), rama
  `limpieza-estructura`.

---

## 🔴 1 · Pagás la seña y el turno no se confirma

El síntoma: la persona paga, vuelve a `/payment-success`, la página dice
"Estamos terminando de confirmarlo" y no termina nunca. La reserva queda en
`pending_deposit`, y el bot —que pregunta por ese mismo estado— cree que nunca
pagó.

La causa no estaba en el bot: el webhook de Mercado Pago del backend de aturno
nunca llegaba a marcar la reserva. Eran dos bloqueos independientes.

### 1a · ✅ El webhook buscaba `external_reference` donde Mercado Pago no lo manda

**Dónde:** `~/Aturno/aturno/backend/server.js`

El aviso de Mercado Pago para `type: "payment"` trae **solo** `data: { id }`.
`external_reference` es un campo del *pago*, que se lee pidiendo
`GET /v1/payments/{id}`. El handler lo buscaba en el cuerpo del aviso, así que
`bookingId` era `undefined` **siempre** y salía por un 400 antes de tocar la
reserva. Fallaba el 100% de las veces.

Para pedirle el pago a Mercado Pago hace falta el token del negocio, y para
saber el negocio hacía falta la reserva: un círculo. Se cortó mandando el
`businessId` en la `notification_url` de la preferencia, que Mercado Pago
conserva. El handler ahora va negocio → token → pago → `external_reference` →
reserva, y verifica que la reserva sea de ese negocio antes de escribir.

De paso, el token ahora sale de `tokenDeMercadoPago()` y no de
`mercadoPago.access_token` crudo. Era la versión silenciosa del mismo bug: el
token dura 180 días, y a los seis meses de conectar los pagos de ese negocio
dejaban de confirmarse con un 401 que nadie miraba.

**Verificado:** `node src/webhookDePagos.test.js` — 8 en verde. El endpoint no
tenía **ni un test**; ahora cubre el pago aprobado, el rechazado y el aviso sin
firma. Las otras 17 suites del backend siguen en verde.

### 1b · ⚠️ El secreto del webhook — esto lo tenés que hacer vos

**Sin esto, el arreglo de 1a no sirve para nada.**

**Dónde:** variables de entorno de Render, servicio del backend de aturno.

Si `MERCADOPAGO_WEBHOOK_SECRET` está vacío, la verificación de firma falla, el
handler responde **200** y no hace nada. El 200 es deliberado —para que Mercado
Pago no reintente un aviso ajeno— pero convierte "me falta una variable" en "los
pagos desaparecen sin ruido".

En `backend/.env.local` la variable **no está**. En Render no lo puedo ver desde
acá.

**Qué hacer:** cargarla en Render con el valor de Mercado Pago (panel de MP →
Webhooks → clave secreta). Después redeployar.

**Cómo confirmar cuál de los dos era tu caso:** buscá en el log de Render el
momento en que pagaste.

| Lo que dice el log | Qué era |
|---|---|
| `🚫 WEBHOOK MP rechazado: no hay secreto configurado` | Falta la variable (1b) |
| `❌ WEBHOOK: No se encontró external_reference` | El bug de 1a |
| Nada del webhook | Mercado Pago no está llamando: revisar la `notification_url` |

### 1c · ✅ El bot ahora se recupera solo

**Dónde:** `src/agentes/flujo.py`, `src/agentes/estados.py`

Había dos agujeros, y los dos se veían exactamente como se vio:

1. El vigilante que consulta el pago es una tarea suelta dentro del proceso, con
   quince minutos de presupuesto. Si Render reinicia o redeploya en el medio,
   muere y nadie vuelve a preguntar nunca.
2. Escribir "ya pagué" no disparaba ninguna consulta: cualquier mensaje que no
   fuera un saludo se leía como "esta persona empieza un pedido nuevo" y recibía
   la lista de servicios encima de un turno que seguía esperando el pago.

Ahora, estando en `esperando_senia`, **cualquier mensaje entrante consulta el
pago antes de decidir nada**. Si entró, confirma el turno y lo anuncia con la
misma plantilla que usa el vigilante. Si no entró, "ya pagué" —y sus variantes—
reciben el recordatorio en vez del menú.

Esa consulta es la red de seguridad: aunque el webhook de aturno vuelva a fallar
algún día, la persona que escribe "ya pagué" recibe su confirmación.

**Verificado:** `python test_bordes.py`, caso [10].

### 1d · ⚠️ Tu turno, el de verdad

Con 1a y 1b arreglados, la reserva que ya pagaste **no se confirma sola**: el
aviso de Mercado Pago de ese pago ya pasó y se descartó. Hay que confirmarla a
mano desde el panel.

---

## 🔴 2 · ✅ El bot ya no se olvida lo que cargás en el panel

Era, casi seguro, por qué no contestaba lo de los colectivos aunque lo hubieras
cargado.

**Dónde:** `src/api/webhook.py` (`_traer_el_conocimiento`)

El panel EMPUJA bien: cuando guardás una respuesta, aturno llama a
`/panel/reindexar` y el bot la indexa. El problema era dónde quedaba.
`reindexar_negocio` escribe `datos/<negocio>.md` en el disco del contenedor, y
**en Render el disco es efímero**: en el deploy siguiente el índice se
reconstruía desde los `.md` commiteados en el repo —una foto vieja— y todo lo
cargado por el panel desaparecía.

El síntoma no parece un error, y por eso es el peor: cargás cómo llegar, lo
probás, anda, y una semana después el bot dice "ese dato no lo tengo cargado"
sin que nadie haya tocado nada.

Ahora, **al arrancar, el bot le pide a aturno el conocimiento de cada negocio y
lo reindexa**. aturno pasa a ser la única fuente de verdad y los `.md` del repo
quedan como semilla de desarrollo. Si el contenido no cambió, no recalcula
embeddings (el plan gratuito da 1.000 por día para todo el proyecto). Y falla
blando: si aturno no contesta, el bot arranca igual — va a poder sacar turnos y
a las preguntas va a contestar que no tiene el dato.

**Verificado:** `python test_conocimiento.py` (nuevo). Arranca desde cero y
pregunta con las palabras que usa la gente: *"qué bondi me deja cerca"*,
*"hay subte cerca"*, *"puedo pagar con débito"*, *"sobre qué calle están"*. Las
cuatro encuentran la respuesta cargada.

---

## 🔴 3 · ✅ Que encuentre la respuesta escrita, y conteste sólo eso

**Dónde:** `src/rag/indice.py` (`_por_pregunta`, `contexto`, `MARGEN`)

Medido contra el conocimiento **real** de tu negocio, bajado de aturno. Antes:

```
PERSONA: dónde quedan            → (el bot dice: no lo tengo cargado)
PERSONA: puedo pagar con débito  → (el bot dice: no lo tengo cargado)
PERSONA: qué colectivos paran cerca
   Cómo llegar / Roque Saenz Peña 668 / Alado de libertador / 168 /
   Tenemos estacionamiento propio. / enfrente de mi casa solo para 1. /
   en fernandez espiro / pero hay camino empredado, hay que tener cuidado
```

O sea: **no encontraba** cosas que estaban cargadas con esas palabras exactas
escritas como sinónimo, y cuando encontraba **contestaba la sección entera**.

**La causa:** el índice cortaba por `##`, pero el panel escribe muchas preguntas
dentro de una misma sección. "Cómo llegar" son seis respuestas distintas. Un
fragmento con seis temas no se parece lo bastante a ninguno de los seis —por eso
"dónde quedan" no recuperaba nada— y cuando pegaba, venían los seis.

**El arreglo:** la unidad no es la sección, es **una pregunta y su respuesta**, que
es justo lo que la línea `>` delimita. Además: no se repite el nombre del negocio
ni el título de sección en cada bloque, y el segundo fragmento sólo se manda si
viene pegado al primero (`MARGEN`, calibrado con puntajes medidos — el caso
"estacionamiento" son dos respuestas que empatan en 0.665 y 0.661, y las dos
sirven).

Ahora, mismo contenido:

```
PERSONA: qué colectivos paran cerca     → Cómo llegar / 168
PERSONA: puedo pagar con débito         → Pagos / efectivo, mercado pago, transferencia.
PERSONA: dónde quedan                   → Cómo llegar / Roque Saenz Peña 668
PERSONA: con cuánta anticipación        → Turnos / Conviene sacarlo con 5 días.
```

Las secciones sin líneas `>` (los `.md` escritos a mano) siguen funcionando
igual que antes: no hay nada que partir ahí.

**Verificado:** `python test_conocimiento.py`.

**Lo que te queda a vos:** las respuestas son tuyas y el bot las manda tal cual.
"168" contesta lo que le preguntaron, pero se lee mejor "Te deja el 168 en la
puerta". Lo mismo con "Alado de libertador" y "pero hay camino empredado". Eso
se edita en el panel, no en el código.

---

## 🟡 3b · La medición de fondo

**El instrumento estaba roto:** `test_recuperacion.py` se caía con un
`IndexError` en la primera pregunta sin respuesta, o sea que la evaluación
entera moría exactamente cuando había algo que medir. Arreglado (una línea).

**Lo que mide hoy:**

```
top-1: 9/12    top-3: 9/12     ✗ NO PASA el umbral de 85% (75%)
  ✗ puedo pagar con crédito → nada
  ✗ dónde quedan            → nada
  ✗ atienden OSDE           → nada
```

Las tres que fallan son, textualmente, las que vos nombraste: sobre qué calle
está y qué medios de pago acepta.

**Por qué fallan, y cómo se arregla:** la clave es la línea `>` de sinónimos que
el panel escribe en cada sección. Es lo que hace que "¿dónde quedan?" se parezca
a una dirección con la que no comparte una sola palabra. Los archivos de demo
(`datos/demo-*.md`) **no tienen ninguna** línea `>`; el que escribe el panel
(`datos/aturno.md`) sí. O sea: el mecanismo funciona, a los fixtures viejos les
falta.

1. Correr `python test_recuperacion.py` con las preguntas reales de tus
   negocios, con las palabras con las que la gente pregunta de verdad.
2. Lo que falle se arregla primero en **la línea de sinónimos de esa sección, en
   el panel**, y recién si no alcanza, en el `UMBRAL` de `src/rag/indice.py`.

**Ojo con el umbral:** bajarlo hace que encuentre más, y también que conteste la
dirección cuando le preguntan por el estacionamiento. Está puesto para preferir
"no lo tengo" antes que la respuesta de al lado.

---

## 🟢 4 · ✅ Que la respuesta salga prolija

Se resolvió junto con el punto 3: al ser un fragmento por pregunta, la respuesta
que llega es la respuesta a lo que preguntaron y nada más. No hizo falta la
opción B ni la C — el modelo sigue sin redactar una sola palabra de lo que lee
la persona.

## 🟢 6 · ✅ Que el crédito de la API rinda más

Medido con `medir_costo.py` antes y después, y con `count_tokens` de la API.

| | Antes | Después |
|---|---:|---:|
| por turno reservado | 0,00432 USD | **0,00176 USD** |
| tokens de entrada por turno | 3.700 | 1.468 |
| mensajes que nunca llegan al modelo | 76% | **88%** |
| mil turnos por mes | 4,32 USD | **1,76 USD** |
| chequeo de salud | 0,88 a 17,54 USD/mes | centavos |

Tres cosas, en orden de sorpresa:

**a) `/salud` llamaba al modelo en cada ping.** Lo pinchan el cron de GitHub cada
10 minutos y Render como `healthCheckPath`, con su propia cadencia. Cada ping
costaba 0,000203 USD, y 39 de esos 47 tokens eran el modelo contestando *"Hello!
It seems like you've sent just a period…"* a un `"."` — una respuesta que
`_llm_responde` tira, porque sólo mira si hubo excepción. Con volumen bajo el
chequeo costaba **más que atender gente**. Ahora: `max_tokens=1` y caché de 5
minutos, con `?profundo=1` para forzarlo.

**b) El esquema de salida era el 70% de cada llamada.** 1.391 de 1.998 tokens.
Lo inflaban los docstrings de las clases —Pydantic los serializa como
`description`, y el más caro era justo el párrafo que explicaba por qué no hay
que pagar descripciones— y los `anyOf: [string, null]` con `$defs` de
`str | None`. Ahora se manda un esquema plano escrito a mano (`ESQUEMA`) y se
valida a la vuelta con `Clasificacion.model_validate`: **724 tokens**.

**c) El paso del nombre no tenía atajo.** Todo cliente nuevo pagaba una llamada
para sacar "Ana" de "soy Ana", y en la conversación más común —la que toca sólo
números— era la única llamada que quedaba. Con `nombre_propio()` esa
conversación pasó a costar **0,00 USD**. Se rinde ante cualquier duda: un turno
a nombre de «No Gracias» cuesta más que la llamada que ahorra.

**Lo que NO se hizo, a propósito:** prompt caching. Es el descuento más grande
del catálogo, pero el prefijo estable quedó en ~1.100 tokens contra un mínimo
cacheable de 1.024, escribir la caché cuesta 1,25× —y la conversación más común
ahora tiene CERO llamadas, así que no habría nada que reusar— y `cache_control`
choca con el respaldo de Gemini. Está analizado en el plan; la decisión se toma
con números nuevos si el volumen crece.

**Ojo:** el README dice "Costo por turno reservado US$ 0,035" en la tabla de
observabilidad. Ese número es de otra medición y quedó viejo por partida doble.

---

## 🔴 7 · ✅ Los 5 dólares en un día: era el chequeo de salud

`/salud` llamaba al modelo en **cada request**, y Render lo pincha como
`healthCheckPath` **cada 5 a 10 segundos** — su documentación dice que no es
configurable. Corría 24 horas, escribiera alguien o no.

| Quién | Llamadas/día | USD/día | USD/mes |
|---|---:|---:|---:|
| Render cada 5 s | 17.280 | **3,51** | 105,24 |
| Render cada 10 s | 8.640 | **1,75** | 52,62 |
| cron de GitHub | 144 | 0,03 | 0,88 |

Entre el 35% y el 70% de los 5 dólares. El resto eran las pruebas.

**Verificado en producción después del deploy:** 10 pings seguidos a `/salud`
cuestan **cero** llamadas nuevas al modelo, y el endpoint bajó de ~1,1 s a
~0,3 s. Ahorro: **~105 USD/mes**.

Descartado por el camino, para que no quede la duda: el backend de aturno no usa
Anthropic; la clave no está exportada en el shell, así que no la gasta Claude
Code; el panel de conversaciones pincha Firestore, no al bot; y hay tope por
teléfono y por minuto.

### Lo que se agregó para que no vuelva a pasar

**`/gasto`** — el servicio ahora sabe lo que gasta. Cuenta lo que informa cada
respuesta, no una estimación, desglosado por motivo (`clasificar`, `salud`). Se
engancha en `construir_modelo`, el único lugar por el que pasan todos los
caminos al modelo, así que uno nuevo queda contado sin que nadie se acuerde.

Esa era la causa de fondo: Phoenix mide esto y está apagado en producción, así
que en el único lugar donde el gasto importa no había una sola métrica. Por eso
hubo que deducirlo leyendo código.

**`TOPE_DIARIO_USD`** (3 USD por defecto) — pasado el techo, el clasificador
deja de llamar al modelo y todo cae en `DESCONOCIDO`. El bot **no se cae**: el
88% de los mensajes ya se resuelve sin modelo, así que quien contesta con
números saca su turno igual. Degradar es la falla correcta: la alternativa ya se
probó sola cuando se acabó el crédito y ningún cliente nuevo pudo reservar.

**`/salud` dejó de bloquearse contra Anthropic** — vencida la caché contesta con
lo último que sabe y refresca por atrás. Render corta a los 15 s y reinicia la
instancia si falla 60, así que acoplarle el liveness a que un tercero conteste
rápido convertía un mal minuto del proveedor en un reinicio del bot.

---

## 🟢 5 · Encontrado de paso

Ninguno lo causaron los cambios de arriba: los verifiqué contra el árbol limpio.

- **`test_bordes.py` [6] falla, y es un bug real del bot.** Después de confirmar
  un turno, decir *"quiero otro turno"* deja la conversación en `confirmado` en
  vez de arrancar un pedido nuevo. Alguien que ya reservó no puede pedir el
  segundo turno hablando normal. No lo toqué para no mezclarlo con lo de la
  seña.
- **`datos/aturno.md` tiene texto de prueba.** La sección "Lo que siempre
  preguntan" contesta *"Esta respuesta la cargó una prueba del formulario.
  Borrala desde el panel."* Un cliente real puede recibir eso. Borrala desde el
  panel — con la tarea 2 hecha, ya no vuelve en cada deploy.
- **`README.md` documenta mal una flag.** Dice que `verificar_turno.py` corre
  contra el aturno real y que `--doble` lo desactiva. Es al revés: la flag es
  `--real` y sin ella corre contra el doble.
