# Roadmap

Un solo lugar para ver **qué está hecho, qué falta, si se puede, y qué puede
salir mal.** Los otros documentos son el detalle; éste es el mapa.

Última actualización: **21 de agosto de 2026.**

---

# El puntaje, y qué lo mueve

| | Hoy | Alcanzable | Con qué |
|---|---|---|---|
| **Cómo conversa** | **8** | 9 | Tarea 6 · sacar el turno en un mensaje |
| **Cuánto sabemos que funciona** | **5** | 8 | Tarea 7 · medir de verdad |
| **Confiabilidad técnica** | **7** | 9 | Tarea 8 · probar lo que nunca se probó |
| **Producto vendible** | **3** | 6 | Tarea 3 · varios negocios *(el resto es trámite)* |

**Por qué «cuánto sabemos» no llega a 9 sin usuarios reales.** Todo lo medido
hasta hoy lo escribí yo: mis 49 caminos, mis 56 casos, mis 18 trampas. Es una
medición de qué tan bien el bot resiste MIS ideas de cómo habla la gente. El
último punto lo ponen tres personas de verdad escribiéndole media hora.

**Por qué «vendible» no llega más allá de 6 con código.** El número de Twilio
obliga a cada persona a mandar un código de «join» antes de poder escribir — no
se le puede dar a un negocio. Eso se resuelve con Meta, y Meta con el
monotributo.

---

# Dónde estamos hoy

Todo esto es medible y se puede volver a medir con un comando.

| Qué | Valor | Cómo se mide |
|---|---|---|
| Caminos de conversación coherentes | **49 / 49** | `todos_los_caminos.py` |
| Acierto del clasificador (intención) | **92,9%** · 56 casos | `test_clasificador.py` |
| Invenciones que se le escapan al guardián | **0 de 18** | `probar_invencion.py` |
| Rechazo del guardián en preguntas normales | **13%** (techo 30%) | idem |
| Costo por turno | **0,00181 USD** · 88% de mensajes no llegan al modelo | `medir_costo.py` |
| Conversaciones resueltas sin humano | **medible, sin datos reales** | `/metricas` |
| Pecados de diseño que comete el bot | **0 de 8** | [INVESTIGACION.md](INVESTIGACION.md) |

**Lo que todavía no se puede saber**: containment real, satisfacción, turnos
perdidos. Esos números necesitan gente de verdad escribiéndole al bot. El
instrumento está puesto y calibrado; falta el uso.

**Lo que no está medido y debería**: qué tan bien el clasificador extrae los
DATOS (fecha, servicio, hora), no la intención. Son dos cosas distintas y sólo
tengo el número de la primera. Ver la tarea 6.1.

---

# ✅ Lo que ya está hecho

## Bugs de comportamiento, encontrados y arreglados

| | Qué pasaba | Cómo se encontró |
|---|---|---|
| ✅ | La seña se pagaba y el turno no se confirmaba | Lo reportaste vos |
| ✅ | Pedir un segundo turno devolvía un error técnico | Probando a mano |
| ✅ | El bot llamaba «Milagros» para siempre a quien reservó con el nombre de su madre | Lo reportaste vos |
| ✅ | «no soy Milagros» derivaba al menú de servicios | Lo reportaste vos |
| ✅ | Saludar después de reservar repetía «De nada» **para siempre** | Lo reportaste vos |
| ✅ | Tres listas vacías pedían elegir «un número» que no existía | Verificando las métricas |
| ✅ | «quiero otro turno» se clasificaba como «otro horario» | La matriz de confusión |
| ✅ | Con la búsqueda caída, el bot decía «no lo tengo cargado» (mentira) y le llenaba el panel al negocio con preguntas ya respondidas | Se agotó la cuota probando |

## Lo que la investigación pedía — cerrado

### Lo que hay que tener

| Hallazgo | Cómo se cumple | Dónde |
|---|---|---|
| ✅ LLM entiende, código decide | Grafo de tres nodos + tabla `ORDEN` | `flujo.py`, `estados.py` |
| ✅ Nunca decir lo que no se sabe | Enum cerrado + plantillas + guardián | `clasificador.py`, `redaccion.py` |
| ✅ Salida estructurada y validada | Esquema plano + `_a_clasificacion` | `clasificador.py` |
| ✅ Al fallar: explicar + ofrecer, no repetir | Dice qué SÍ entendió | `pista_de`, `no_entendi` |
| ✅ Medir la tarea terminada | containment, abandono por paso, turnos | `metricas.py`, `/metricas` |
| ✅ Conjunto dorado para lo probabilístico | 56 casos + matriz de confusión | `casos.jsonl` |
| ✅ Mensajes cortos, listas verticales | Regla del repo | `plantillas.py` |
| ✅ Adelantar lo importante, datos al final | El nombre va después del horario | `ORDEN` |

### Los ocho pecados — ninguno se comete

| # | Pecado | Estado |
|---|---|---|
| ✅ 1 | No dejar llegar a un humano | Nombrado en la apertura, en el error, y tras el segundo fallo |
| ✅ 2 | El bucle | Dos arreglados: «De nada» y las listas vacías |
| ✅ 3 | Hacer repetir | El checkpointer guarda todo; el panel recibe el contexto |
| ✅ 4 | No entender | Manejado con la estrategia que gana, y medido |
| ✅ 5 | Empatía falsa | Cero líneas, y hay un test que la rechaza |
| ✅ 6 | Fingir ser humano / prometer de más | «Soy el asistente de X» + el guardián |
| ✅ 7 | Pedir datos de más | Pide uno: el nombre. Las métricas guardan hash |
| ✅ 8 | Muchas preguntas antes de mostrar algo | Parcial — ver la tarea 6 |

## Que no suene a robot — cerrado

| | |
|---|---|
| ✅ | El bot contesta la pregunta que le hicieron, no vuelca la ficha |
| ✅ | El guardián verifica números, negaciones, vocabulario y empatía |
| ✅ | Con dos fragmentos del RAG no se redacta (se podrían fundir) |
| ✅ | Sin dato, dice de qué SÍ puede hablar — **sin llamar al modelo** |
| ✅ | El riesgo residual está medido: 0 de 18 |

## Herramientas nuevas

| | Para qué | Cuesta |
|---|---|---|
| ✅ `/metricas` | Cuántas conversaciones termina solo, y dónde se cae | gratis |
| ✅ `test_metricas.py` | Calibra el instrumento contra un patrón conocido | gratis |
| ✅ `test_redaccion.py` | El guardián contra 32 casos | gratis |
| ✅ `test_clasificador.py` | 56 casos + matriz de confusión | ~0,08 USD |
| ✅ `probar_invencion.py` | 18 preguntas que empujan a inventar | ~0,01 USD |

---

# 🔜 Lo que falta

## Tarea 1 · Reindexar los negocios de demo — **empezada, bloqueada**

Los archivos de demo estaban en el formato viejo: cada sección era un solo
pedazo que hablaba de cinco cosas, y por eso «aceptan tarjeta?» no encontraba
nada teniendo la respuesta escrita. Reescritos al formato del panel: la
peluquería pasó de 6 a **24** pedazos, el consultorio de 5 a **18**.

- [x] Reescribir `datos/demo-peluqueria.md` y `datos/demo-consultorio.md`
- [ ] **Reconstruir el índice** — necesita cuota de Google, agotada el 21/8
- [ ] Medir cuántas preguntas encuentra ahora contra las que encontraba antes

**No afecta a un negocio real**: el panel escribe el formato correcto solo, y
`aturno.md` ya está bien.

## Tarea 2 · Dos cosas que sólo podés hacer vos

- [ ] **2.1 · Sacar el secreto de Mercado Pago del código.**
  `~/Aturno/aturno/backend/server.js:7246`. Está escrito a mano en vez de salir
  de una variable de entorno. El repo es privado, así que no es urgente — pero
  queda en el historial de git para siempre y hoy no se puede cambiar sin tocar
  código.
- [ ] **2.2 · Borrar el texto de prueba desde el panel.** En el negocio
  `aturno`, bajo «Lo que siempre preguntan», quedó cargado *"Esta respuesta la
  cargó una prueba del formulario"*. Si alguien pregunta algo que caiga ahí, el
  bot se lo contesta.

## Tarea 3 · Que el bot soporte varios negocios

Hoy `TENANTS` es un diccionario escrito a mano en `config.py` con **un solo
número**. **Con esto no podés tener dos clientes**: el segundo necesita que
alguien edite el código y despliegue.

> **Con Meta esto no desaparece: se vuelve más necesario.** Meta le da a cada
> negocio su `phone_number_id`, pero algo tiene que saber qué número es de qué
> negocio.

- [ ] Traer la lista de negocios desde aturno al arrancar
- [ ] Que si aturno no contesta, se use la última lista conocida
      *(cero negocios = servicio caído; un negocio de menos = un negocio de menos)*
- [ ] Probarlo con dos negocios sobre el mismo número

**Riesgo:** bajo, con esa trampa. Se puede hacer y probar hoy, sin Meta.

## Tarea 4 · Dejar Meta a un cambio de bandera

El módulo `src/canal/` está escrito y probado (`test_canal.py` en verde), pero
**desconectado**. Verificado en el código: cero referencias desde el webhook.

- [ ] Enchufar el canal, empezando por un `CanalTwilio` que haga **exactamente
      lo de hoy** — si los 49 caminos dan igual antes y después, el refactor no
      cambió nada
- [ ] Aplicar la ventana de 24 h al enviar (`ventana_abierta` existe, sin usar)
- [ ] Repasar los mensajes que salen solos («entró el pago», «se venció la
      seña», el panel contestando)

**Riesgo:** medio. Toca el camino por donde pasan todos los mensajes: un error
acá no rompe una conversación, las rompe todas. Se hace y se prueba con Twilio.

## Tarea 5 · Meta — bloqueado por el monotributo

- [ ] Embedded Signup en aturno (necesita la app aprobada)
- [ ] Las plantillas de mensaje (las revisa Meta)
- [ ] Probar un envío real

Mientras tanto la demo se hace con Twilio, que alcanza para mostrar el producto
entero.

## Tarea 6 · Sacar el turno en un solo mensaje

> *"quiero un turno de corte el viernes a la tarde"* → que muestre los horarios
> del viernes a la tarde, sin volver a preguntar el servicio ni el día.

**Es la mejora más grande que queda para el usuario**, y no necesita que el
modelo haga más: **el clasificador ya extrae todo eso y el flujo lo tira.**
`_resolver` resuelve un paso, avanza uno, y descarta el resto.

- [ ] **6.1 · Medir cuánto acierta extrayendo DATOS**, no intenciones. Hoy sólo
      está medida la intención (92,9%). Es agregarle una columna a `casos.jsonl`
- [ ] 6.2 · Aplicar las entidades a todos los pasos que resuelvan sin
      ambigüedad, y frenar en el primero que no
- [ ] 6.3 · Que una franja («a la tarde») filtre la lista de horarios en vez de
      elegir por la persona
- [ ] 6.4 · Deducir el servicio desde el profesional **sólo si hace uno solo**,
      y sacando el dato de aturno, nunca del modelo

**Riesgos:**

| | |
|---|---|
| Avanzar sobre una suposición | **Ya cubierto**: si el nombre coincide con dos servicios, no avanza |
| Saltearse la confirmación | **No pasa**: el resumen sigue siempre. Es la red que hace todo esto seguro |
| Sentirse atropellado | Cubierto: el resumen muestra todo y «cambiar el día» funciona |
| **Un error del clasificador ahora mueve la conversación** | **Nuevo.** Hoy una fecha mal extraída se descarta; con esto, desvía. Por eso la 6.1 va primero |

## Tarea 7 · Saber que funciona, no creerlo

Los instrumentos están puestos y marcan todo en cero porque nadie los usó.
Esto es lo que se puede medir **sin usuarios reales**.

- [ ] **7.1 · Medir el buscador.** ¿Encuentra lo que el negocio tiene cargado?
      Hoy no lo sé. Necesita el reindexado de la tarea 1. *(Quedó corriendo en
      segundo plano el 21/8 — verificar si entró antes de rehacerlo.)*
- [ ] **7.2 · Medir la extracción de DATOS.** Está medido cuánto acierta
      entendiendo *qué* querés (92,9%). No está medido cuánto acierta sacando
      *la fecha, el servicio, la hora*. **Es el número que decide si la tarea 6
      es segura**, así que va antes que ella.
- [ ] **7.3 · El simulador de clientes.** La pieza grande.

### Sobre el simulador (7.3)

Se le pide a un modelo que **actúe de cliente**: con apuro, con dudas,
escribiendo mal, en argentino, cambiando de idea a mitad. Genera ~50
conversaciones distintas, se corren enteras contra el bot, y se mide:

| | |
|---|---|
| cuántas terminan en un turno | *goal completion* |
| en cuántos mensajes | los benchmarks dicen que menos es mejor, siempre |
| cuántas se traban en «no te entendí» | |
| cuántas piden un humano | *escalation rate* |

**Por qué importa más que todo lo demás que medí**: rompe el límite de que las
pruebas las escriba la misma persona que escribió el código. No es un usuario
real —sigue siendo un modelo imaginando gente— pero es lo más cerca que se
llega sin usuarios, y **sale un número que se le puede mostrar a un negocio**:
«de 50 conversaciones, 43 terminan en turno».

Cuesta plata: ~50 conversaciones × varios mensajes cada una. Se mide antes.

## Tarea 8 · Confiabilidad: probar lo que nunca se probó

Tres huecos. Ninguno se rompió todavía; ninguno se probó tampoco.

- [ ] **8.1 · Varias conversaciones a la vez.** Hay un candado
      (`_procesar_bajo_candado`) para que dos mensajes de la misma persona no se
      pisen. **Nunca se probó bajo carga.** Si tiene un bug, dos personas pueden
      mezclarse los turnos — **es el único fallo del que un negocio no te
      perdona**, así que va primero de los tres.
- [ ] **8.2 · Sobrevivir un reinicio.** Render reinicia en cada deploy. Alguien
      a mitad de reservar, ¿pierde lo que eligió? El checkpointer dice que no.
      Nadie lo verificó.
- [ ] **8.3 · Que aturno no conteste.** Parcialmente cubierto. Falta el repaso
      completo de qué ve la persona en cada caso.

---

# Los riesgos, ordenados por lo que cuestan

| Riesgo | Si pasa | Prob. | Mitigación | Estado |
|---|---|---|---|---|
| **Un solo negocio posible** | No podés tener un segundo cliente | 100% hoy | Tarea 3 | 🟡 abierto |
| **El bot inventa un dato** | Un cliente recibe información falsa | Baja — 0 de 18 medido | Guardián + vuelta al texto literal | 🟢 medido |
| **Cuota de Google: 1.000 búsquedas por día en total** | El bot no puede contestar preguntas | **Ya pasó dos veces** | Ahora avisa honestamente en vez de mentir. A futuro: pagar la cuota, o el buscador local (+805 MB) | 🟡 conocido |
| **El secreto de MP en el código** | Queda en el historial de git; no se rota sin deploy | Baja — repo privado | Tarea 2.1 | 🟡 abierto |
| **Twilio trial: 50 mensajes por día** | La demo se corta a la mitad | 100% en una demo larga | `TWILIO_MODO=consola` para probar; cuenta paga para mostrar | 🟢 conocido |
| **El sandbox obliga a un «join» que vence** | No le podés dar el número a un cliente real | 100% | Tarea 5 | 🟢 conocido |
| **La lista de verbos del guardián se queda corta** | Frena respuestas correctas: sale el texto literal | Media con un negocio nuevo | Cada rechazo se loguea con la palabra; el % se mide | 🟢 a la vista |
| **Deuda del clasificador: frases con «no»** | Nada visible | Baja | Medido, con piso en `test_clasificador.py` | 🟢 a la vista |

---

# El orden que propongo

| # | Qué | Por qué ahí | Cuánto |
|---|---|---|---|
| **1** | 8.1 · Varias conversaciones a la vez | Si el candado tiene un bug, todo lo demás importa menos | horas |
| **2** | 7.2 · Medir la extracción de datos | Es el número que decide si la tarea 6 es segura | horas |
| **3** | 1 + 7.1 · Reindexar y medir el buscador | Está a medias y desbloquea saber si encuentra | horas |
| **4** | 6 · Sacar el turno en un mensaje | Lo que más cambia lo que vive la persona | un día |
| **5** | 7.3 · El simulador de clientes | El número que se le muestra a un negocio | un día |
| **6** | 8.2 + 8.3 · Reinicio y aturno caído | Cerrar los huecos de confiabilidad | medio día |
| **7** | 3 · Varios negocios | El techo del producto. Sin esto no hay segundo cliente | un día |
| **8** | 4 · Enchufar el canal | Deja Meta a un cambio de bandera | un día |
| **9** | 5 · Meta | Cuando haya cliente y monotributo | trámite |

**Total hasta el punto 6: dos días y medio de trabajo.** Ahí el bot queda en
9 / 8 / 9 / 3.

La tarea 2 es tuya y no depende de mí.

---

# Cómo retomar esto

Para arrancar una sesión nueva sin releer todo:

1. **Leé este archivo** — es el mapa. Las casillas dicen qué falta.
2. **Corré el tablero** para saber de dónde partís. Toma un minuto y es gratis:
   ```bash
   python test_flujo.py && python test_bordes.py && python test_canal.py \
     && python test_demora.py && python test_metricas.py && python test_redaccion.py
   python todos_los_caminos.py    # tiene que decir 49 caminos, 0 para mirar
   python medir_costo.py          # tiene que dar ~0,00181 USD por turno
   ```
3. **[PLAN.md](PLAN.md)** tiene la bitácora: qué se planeó, qué salió distinto y
   qué se encontró en el camino. Ahí está el «por qué» de cada decisión rara.
4. **La forma de trabajar está en [CLAUDE.md](CLAUDE.md)**: el test primero, un
   commit por tarea, y el tablero después de cada uno.

**Lo que quedó corriendo el 21/8:** un reindexado de `demo-peluqueria` en
segundo plano, esperando que volviera la cuota de Google. Verificar si entró
—`Recuperador("demo-peluqueria").temas()` tiene que devolver más de 6
fragmentos— antes de rehacerlo.

---

# Lo que NO vamos a hacer, y por qué

- **Que el LLM redacte los pasos del turno.** *"Elegí el servicio: 1, 2, 3"* es
  idéntico byte a byte en cada conversación, y eso es parte de que el bot se
  sienta un producto. Sólo se aflojaron las preguntas.
- **Empatía, emojis, personalidad.** Medido: no compra confianza, y con alguien
  enojado la destruye.
- **Optimizar latencia.** WhatsApp es asincrónico; nadie mira el reloj.
- **Medir engagement.** Conversaciones largas y «lindas» son señal falsa.
- **Tocar la máquina de estados.** `ORDEN`, `AVANZA_CON` y `ELIGE_DE_LISTA` son
  lo que impide que el bot invente saltos.
- **Agregar sustantivos a la lista del guardián.** Los verbos son la forma de
  decir algo; los sustantivos son la cosa dicha, y ahí vive la invención.

---

# Dónde está el detalle

| Archivo | Qué tiene |
|---|---|
| [INVESTIGACION.md](INVESTIGACION.md) | Los estudios: qué hay que tener, qué odia la gente, con fuentes |
| [PLAN.md](PLAN.md) | Los planes ejecutados, con bitácora de lo que salió distinto |
| [PENDIENTES.md](PENDIENTES.md) | El detalle operativo: Render, Twilio, aturno |
| [SEGURIDAD.md](SEGURIDAD.md) | Todas las credenciales a rotar |
| [TODO-PANEL.md](TODO-PANEL.md) | Lo que falta del lado de aturno (otro repo) |
| [README.md](README.md) | Arquitectura y el porqué de cada decisión |
