# Roadmap

Un solo lugar para ver **qué ya está, qué falta, si se puede, y qué puede salir
mal.** Lo que estaba desparramado en cinco archivos.

Los otros documentos siguen siendo el detalle; éste es el mapa.

---

# Dónde estamos hoy

Todo esto es medible y se puede volver a medir con un comando.

| Qué | Valor | Cómo se mide |
|---|---|---|
| Caminos de conversación coherentes | **49 / 49** | `todos_los_caminos.py` |
| Acierto del clasificador | **92,9%** (56 casos) | `test_clasificador.py` |
| Costo por turno | **0,00181 USD** · 88% de mensajes no llegan al modelo | `medir_costo.py` |
| Conversaciones resueltas sin humano | **medible, sin datos reales todavía** | `/metricas` |
| Pecados de diseño que comete el bot | **0 de 8** | [INVESTIGACION.md](INVESTIGACION.md) |
| Suites de prueba en verde | 6 sin costo + 1 con costo | ver `CLAUDE.md` |

Lo que **todavía no se puede saber**: containment real, satisfacción, turnos que
se pierden. Esos números necesitan gente de verdad escribiéndole al bot. El
instrumento ya está puesto y calibrado; falta el uso.

---

# Parte 1 · Lo que la investigación pedía: cerrado

Ésta es la lista que armamos leyendo los estudios. **Está completa.**

### Lo que hay que tener

| Hallazgo | Cómo se cumple | Dónde |
|---|---|---|
| LLM entiende, código decide | Grafo de tres nodos + tabla `ORDEN` | `flujo.py`, `estados.py` |
| Nunca decir lo que no se sabe | El LLM no redacta: enum cerrado + plantillas | `clasificador.py`, `plantillas.py` |
| Salida estructurada y validada | Esquema plano + `_a_clasificacion` | `clasificador.py` |
| **Al fallar: explicar + ofrecer, no repetir** | ✅ **hecho** — dice qué sí entendió | `pista_de`, `no_entendi` |
| **Medir la tarea terminada** | ✅ **hecho** — containment, abandono por paso | `metricas.py`, `/metricas` |
| **Conjunto dorado para lo probabilístico** | ✅ **hecho** — 56 casos, matriz de confusión | `casos.jsonl` |
| Mensajes cortos, listas verticales | Regla del repo | `plantillas.py` |
| Adelantar lo importante, datos al final | El nombre va después del horario | `ORDEN` |

### Lo que NO hay que tener — los ocho pecados

| # | Pecado | Estado |
|---|---|---|
| 1 | No dejar llegar a un humano | ✅ nombrado en la apertura, en el error, y **ahora también tras el segundo fallo** |
| 2 | El bucle | ✅ **dos arreglados**: el de «De nada» y el de las listas vacías |
| 3 | Hacer repetir | ✅ el checkpointer guarda todo; el panel recibe el contexto |
| 4 | No entender | ✅ manejado con la estrategia que gana, y **ahora medido** |
| 5 | Empatía falsa | ✅ cero líneas — ver la advertencia del bloque 1 |
| 6 | Fingir ser humano / prometer de más | ✅ «Soy el asistente de X» en el primer mensaje |
| 7 | Pedir datos de más | ✅ pide uno: el nombre. Las métricas guardan hash, no teléfonos |
| 8 | Muchas preguntas antes de mostrar algo | ✅ el nombre va después del horario |

**Cuatro bugs se encontraron y arreglaron en el camino**, tres de ellos por los
instrumentos nuevos y no a mano.

---

# Parte 2 · Lo que falta

Cinco bloques. Para cada uno: qué es, si se puede, qué puede salir mal.

---

## Bloque 1 · Que no suene a robot

**Qué es.** Hoy, cuando alguien pregunta algo, el bot imprime el texto que el
negocio cargó, tal cual. Suena a FAQ pegada porque **es** una FAQ pegada.

```
👤 che, aceptan tarjeta? porque no tengo efectivo
🤖 Aceptamos efectivo, transferencia y débito.
```

Lo que querés:

```
🤖 Sí, débito sí — no hace falta el efectivo.
```

Y el caso que hoy es un muro:

```
👤 tienen estacionamiento cerca?
🤖 Ese dato no lo tengo. ¿Querés que te saque un turno?
```

Contestable sin inventar nada:

> *"De estacionamiento no tengo el dato. Lo que sí te puedo decir es que estamos
> en Roque Sáenz Peña 668 — si querés le pregunto a alguien del local."*

**¿Se puede?** Sí, pero es **el cambio más riesgoso de todos los que hicimos**,
y hay que decirlo con todas las letras.

> ### ⚠️ El riesgo
>
> Hoy la garantía de que el bot no inventa es **estructural**: el LLM no tiene
> ningún camino hacia el texto que lee una persona. No es que le pedimos que no
> invente — es que no puede.
>
> Si lo dejamos redactar, esa garantía desaparece. Y *"le pusimos en el prompt
> que no invente"* no es una garantía, es una esperanza.
>
> Un bot que inventa el precio de un servicio, o un horario de atención, le hace
> perder plata y clientes a un negocio que confió en vos. Es peor que cualquier
> bug que hayamos tenido.

**Cómo se mitiga.** Cambiando la garantía por otra **igual de verificable**:

> Nada de lo que diga el bot puede contener un número, un precio, una hora, una
> dirección o un nombre propio que no esté en el texto que cargó el negocio.

Eso se chequea **en código**, sobre cada respuesta, antes de mandarla. Si el
modelo agrega algo que no está en la fuente, se descarta la versión
conversacional y sale el texto literal de siempre. **El peor caso es el bot de
hoy**, nunca uno peor.

Se prueba con casos adversarios, igual que ya se prueba la inyección en
`test_ataques.py`.

**El segundo riesgo, más sutil.** Que "sonar humano" derive hacia la cordialidad
—*"¡Qué bueno que preguntes!"*, *"Entiendo que no tengas efectivo…"*—. La
investigación mide que eso **baja** la percepción de competencia (USF, *MIS
Quarterly*). Es el pecado 5, y hoy el bot no lo comete.

La línea, escrita para que no se borre: **más humano en la comprensión, no en la
cordialidad.** Se protege con un test que rechaza las frases de empatía.

**Qué cuesta.** Una llamada más al modelo por pregunta. No lo voy a estimar: se
mide con `medir_costo.py` antes de decidir. *(La última vez que estimé un costo
me equivoqué por cien.)*

---

## Bloque 2 · Tres cosas que sólo podés hacer vos

No las puedo hacer yo: una es una credencial, otra es el panel, la tercera
necesita que pagues.

### 🟡 2.1 · Rotar el secreto de Mercado Pago

`~/Aturno/aturno/backend/server.js:7246`

```js
clientSecret: process.env.MERCADO_PAGO_CLIENT_SECRET || 'ygNk6O8ES9iKSRLWpagb2AeHvx3Go7bm',
```

**El secreto de producción está escrito en el código.** Es la llave que le
permite a tu aplicación cobrar a través de Mercado Pago en nombre de los
negocios.

**Corrección de lo que dije antes:** yo lo marqué en rojo dando a entender que
estaba expuesto públicamente. **El repo `Mati2108/Aturno` es privado**, así que
sólo lo ve quien ya tiene acceso. Es mucho menos urgente de lo que planteé.

Sigue valiendo la pena arreglarlo, por dos motivos concretos: queda en el
historial de git para siempre —si el repo alguna vez se hace público, o entra un
colaborador, el secreto viaja con él— y hoy no hay forma de rotarlo sin tocar
código.

El `||` con valor por defecto es lo que lo hace fácil de pasar por alto: parece
que sale del entorno, y sólo sale si la variable está puesta.

**Cómo:** rotarlo en el panel de Mercado Pago, ponerlo como variable en Render, y
sacar el literal del código dejando que falle si falta. Lo mismo con las otras
credenciales listadas en [SEGURIDAD.md](SEGURIDAD.md).

### 🟡 2.2 · Borrar el texto de prueba

`datos/aturno.md`, bajo **"Lo que siempre preguntan"**:

> *"Esta respuesta la cargó una prueba del formulario. Borrala desde el panel."*

Si alguien pregunta algo que caiga en esa sección, el bot se la contesta. **Se
borra desde el panel de aturno**, no desde el repo — el archivo de acá es
semilla de desarrollo, no el estado real.

### ✅ 2.3 · La seña — verificado en producción

El bug que arrancó todo esto —pagaste una seña y el turno no se confirmó— está
arreglado **y probado de punta a punta**. Cerrado.

---

## Bloque 3 · Un negocio, un número

> **Con Meta esto no desaparece: se vuelve más necesario.** Meta le da a cada
> negocio su propio `phone_number_id`, pero **algo tiene que saber qué número es
> de qué negocio**, y hoy eso es un diccionario escrito a mano en el código.
>
> Sin esto, cada negocio que conecte su número necesita que alguien edite
> `config.py` y despliegue. Con esto, se da de alta en aturno, conecta su número
> y el bot lo atiende sin que nadie toque nada — que es exactamente el producto
> integral que querés vender.

**Qué es.** Hoy `TENANTS` es un diccionario fijo, en el código, con **un solo
número de WhatsApp** apuntando a **un solo negocio**.

**Por qué importa más de lo que parece.** No es preparación para Meta: es el
techo del producto. **Con esto no podés tener dos clientes.** El segundo negocio
que quiera el bot necesita un deploy con el código cambiado.

**¿Se puede?** Sí, y sin depender de Meta. El bot ya trae el conocimiento de
aturno al arrancar (`_traer_el_conocimiento`); los negocios se traen igual.

**Riesgo.** Bajo, con una trampa: si la lista de negocios se trae al arrancar y
aturno no contesta, el bot arranca sin ningún negocio y **no atiende a nadie**.

**Mitigación.** La regla que el repo ya aplica en `arranque.sh`: lo secundario no
puede tumbar lo principal. Si aturno no contesta, se usa la lista de la última
vez, o la del código como piso. Un negocio de menos es un negocio de menos; cero
negocios es el servicio caído.

---

## Bloque 4 · Dejar Meta a un cambio de variable

**Qué es.** Twilio cobra por mensaje y su sandbox obliga a cada persona a
escribir un código de "join" que vence. La API de Meta borra ese costo y esa
fricción. Pero Meta exige verificación de empresa, y eso implica monotributo.

**La estrategia:** dejar el código a un cambio de variable de distancia, para que
el día que aparezca el cliente la migración sea de días y no de semanas.

**Estado real, verificado en el código:**

| | |
|---|---|
| `src/canal/` — el contrato y la implementación de Meta | ✅ escrito |
| `test_canal.py` — firma, webhook anidado, ecos, ventana de 24 h | ✅ **en verde** |
| Enchufado al webhook | ❌ **cero referencias** |
| La ventana de 24 h aplicada al enviar | ❌ `ventana_abierta` existe, sin usar |
| Los mensajes que salen solos, revisados | ❌ |

**O sea: el módulo está escrito y probado, pero desconectado.** Fue a propósito
—se despriorizó cuando Meta quedó bloqueado— pero nunca quedó dicho, y por eso
parecía que faltaba menos de lo que falta.

**¿Se puede sin Meta?** Sí. Todo esto se hace y se prueba con Twilio.

**Riesgo.** Medio. Es plomería que toca el camino por donde pasan todos los
mensajes: un error acá no rompe una conversación, las rompe todas.

**Mitigación.** El primer paso es un `CanalTwilio` que hace **exactamente lo de
hoy**, detrás del contrato. Si `todos_los_caminos.py` y una conversación real por
WhatsApp dan igual antes y después, el refactor no cambió nada — que es
justamente lo que tiene que pasar.

---

## Bloque 5 · Lo que espera a Meta

Bloqueado por el monotributo, y el monotributo espera al primer cliente. No se
puede adelantar:

- Embedded Signup en aturno (necesita la app aprobada por Meta)
- Las plantillas de mensaje (las revisa Meta)
- Probar un envío real

**Mientras tanto la demo se hace con Twilio**, que alcanza para mostrar el
producto entero.

---

# Parte 3 · Los riesgos, ordenados por lo que cuestan

| Riesgo | Si pasa | Prob. | Mitigación | Estado |
|---|---|---|---|---|
| **El secreto de MP está en el código** | Queda en el historial de git para siempre; no se puede rotar sin deploy | Baja — el repo es privado | Moverlo a variable de entorno · bloque 2.1 | 🟡 abierto |
| **El bot inventa un dato del negocio** | Un cliente recibe información falsa. Lo peor que puede pasar | Alta si se hace sin baranda | Invariante verificable + vuelta al texto literal + tests adversarios · bloque 1 | 🟡 se ataca con el bloque 1 |
| **Un solo negocio posible** | No podés tener un segundo cliente | 100% hoy | `TENANTS` dinámico · bloque 3 | 🟡 abierto |
| **Cuota de Gemini: 1.000 preguntas por día en total** | El RAG deja de contestar preguntas | Alta con volumen | Medir primero. Hoy no se sabe cuántas se usan por día | 🟡 sin medir |
| **Twilio trial: 50 mensajes por día** | La demo se corta a la mitad | 100% en una demo larga | `TWILIO_MODO=consola` para probar; cuenta paga para mostrar | 🟢 conocido |
| **El sandbox obliga a un «join» que vence** | No le podés dar el número a un cliente real | 100% | Meta · bloque 5 | 🟢 conocido |
| **Deuda del clasificador: frases con «no»** | Nada visible: el flujo cae en el pedido del paso | Baja | Medido y con piso en `test_clasificador.py` | 🟢 a la vista |

---

# Parte 4 · El orden, y por qué

| # | Qué | Por qué ahí |
|---|---|---|
| **1** | Rotar el secreto de MP | Es lo único donde el daño ya puede estar ocurriendo. Diez minutos |
| **2** | Que no suene a robot | Es lo que hace que un negocio quiera esto. Todo lo demás es infraestructura |
| **3** | Borrar el texto de prueba · probar la seña | Dos cosas tuyas, chicas, que bloquean una demo honesta |
| **4** | Un negocio, un número | El techo del producto. Sin esto no hay segundo cliente |
| **5** | Enchufar el canal | Deja Meta a un cambio de variable. Se puede hacer sin Meta |
| **6** | Meta | Cuando haya cliente y monotributo |

Los pasos 1 y 3 son tuyos y no dependen de mí. El 2 arranca cuando digas.

---

# Parte 5 · Lo que NO vamos a hacer, y por qué

- **Que el LLM redacte los pasos del turno.** *"Elegí el servicio: 1, 2, 3"* es
  idéntico byte a byte en cada conversación, y eso es parte de que el bot se
  sienta un producto. El bloque 1 afloja **sólo** las preguntas y las respuestas
  sin dato.
- **Empatía, emojis, personalidad.** Medido: no compra confianza, y con alguien
  enojado la destruye.
- **Optimizar latencia.** WhatsApp es asincrónico; nadie mira el reloj.
- **Medir engagement.** Conversaciones largas y "lindas" son señal falsa. Lo que
  cuenta es la tarea terminada en la menor cantidad de turnos.
- **Tocar la máquina de estados.** `ORDEN`, `AVANZA_CON` y `ELIGE_DE_LISTA` son
  lo que impide que el bot invente saltos. Si un cambio "necesita" moverlas, el
  cambio está mal pensado.

---

# Dónde está el detalle

| Archivo | Qué tiene |
|---|---|
| [INVESTIGACION.md](INVESTIGACION.md) | Los estudios: qué hay que tener, qué odia la gente, con fuentes |
| [PLAN.md](PLAN.md) | El plan de los cuatro pasos ya hechos, con bitácora de lo que salió distinto |
| [PENDIENTES.md](PENDIENTES.md) | El detalle operativo: Render, Twilio, aturno |
| [SEGURIDAD.md](SEGURIDAD.md) | Todas las credenciales a rotar |
| [TODO-PANEL.md](TODO-PANEL.md) | Lo que falta del lado de aturno (otro repo) |
| [README.md](README.md) | Arquitectura y el porqué de cada decisión |
