# Plan · De acá al mejor chatbot posible

Derivado de [INVESTIGACION.md](INVESTIGACION.md). Cada paso tiene:

- **un diagnóstico ANTES**, con un número
- **tareas chiquitas**, una por commit
- **qué puede salir mal y cómo lo atrapamos a mitad de camino**
- **una verificación DESPUÉS**, con el mismo número

Si el número no se movió, el paso no sirvió. Eso es todo el método.

---

## Cómo vamos a trabajar

### Las cinco reglas

**1 · El test primero, siempre.**
Se escribe el caso, se lo ve fallar, se arregla, se lo ve pasar. Si el test pasa
antes de tocar nada, el test está mal, no el código. Ya evitó cuatro regresiones.

**2 · Un commit por tarea, y que ande solo.**
Nada de "arreglé tres cosas". Si el commit 7 rompe algo, se revierte el 7 y el
resto queda. Cada tarea de este plan es un commit.

**3 · Medir antes de tocar.**
Ningún paso arranca sin el número de partida. Es la única forma de no discutir
después si mejoró.

**4 · Si hay que tocar la máquina de estados, parar.**
`ORDEN`, `AVANZA_CON` y `ELIGE_DE_LISTA` son la parte que hace que el bot no
invente saltos. Si un cambio "necesita" moverlas, el cambio está mal pensado.
Volvemos a plantearlo.

**5 · Lo secundario no puede tumbar lo principal.**
Métricas, trazas, evaluación: si fallan, el bot contesta igual. Ya es la regla de
`arranque.sh` y de Phoenix.

### El tablero después de cada tarea

Estas cuatro corridas, siempre. Toman menos de un minuto y no cuestan plata:

```bash
python test_flujo.py          # 5 invariantes de redacción y orden
python test_bordes.py         # todo lo que la persona hace mal
python todos_los_caminos.py   # 49 caminos → "0 cosas para mirar"
python test_canal.py          # el canal, sin credenciales
```

---

# Paso 0 · El termómetro

**No cambia una línea de comportamiento.** Construye con qué medir, para que los
pasos 1 a 3 tengan contra qué compararse.

Va primero porque es la regla 3, y porque sin esto el Paso 1 termina en "a mí me
parece que quedó mejor".

### 0.1 · `diagnostico_reparacion.py`

Un script que, para **cada paso** de la conversación, manda un mensaje
imposible de entender e imprime lo que contesta, marcando tres cosas:

| Marca | Pregunta |
|---|---|
| **¿reconoce?** | ¿nombra algo de lo que la persona sí dijo? |
| **¿ofrece?** | ¿da opciones concretas y accionables? |
| **¿nombra la salida?** | ¿dice cómo llegar a una persona? |

Sale un número: **cuántos de los N pasos cumplen las tres.**

Corre sin LLM (mensajes basura → `DESCONOCIDO` sin llamar al modelo), así que es
gratis y repetible.

> **El número esperado hoy: 0 de 6 reconocen.** Si el diagnóstico dice otra
> cosa, el diagnóstico está mal — y hay que arreglarlo *antes* de seguir. Un
> termómetro que miente es peor que no tener termómetro.

### 0.2 · `diagnostico_abandono.py`

El mismo espíritu para el Paso 2. Recorre las conversaciones del checkpointer y
cuenta en qué estado quedó cada una. No calcula todavía containment — solo
demuestra que **se puede leer** el estado final desde afuera, que es el supuesto
sobre el que se apoya todo el Paso 2.

Si este script no puede leer el checkpointer, el Paso 2 cambia de diseño antes
de escribirse. Ese es exactamente el error que queremos atrapar temprano.

### 0.3 · Anotar la línea de base

Un bloque al final de este archivo con los números de hoy, fechado. Sin esto, en
dos semanas nadie se acuerda de cuál era el punto de partida.

**Verificación del Paso 0:** los dos scripts corren, imprimen números, y no
tocan ni una plantilla. `git diff` sobre `src/` tiene que estar **vacío**.

---

# Paso 1 · La reparación

> **Pecado 4** (no entender): 35% de los pedidos de escalar, 73% de la
> frustración. **CHI 2019**: ganan *opciones* y *explicaciones*; *repeat* pierde.
> Hoy el primer escalón es *repeat*.

### El diagnóstico

`python diagnostico_reparacion.py` → **0 de 6 reconocen.**

### Las tareas

**1.1 · La plantilla nueva.**
`no_entendi_con_pista(pista: str, reintento: str)` en `plantillas.py`.

```
Entendí que querés algo para el jueves, pero no qué servicio.

Elegí el servicio:
1. Corte de pelo — 30 min — $8.000
…
```

Test primero en `test_bordes.py`: la plantilla nombra la pista, incluye el CTA
completo, no lleva markdown, y separa bloques con `\n\n`.

**1.2 · Traducir entidades a texto humano.**
Una función `pista_de(entidades, conv) -> str | None` que devuelve algo legible
**sólo** si puede.

Ésta es la tarea delicada y va sola en su commit. Ver el riesgo abajo.

**1.3 · La rama en `avanzar`.**
Donde hoy hay `return {"sin_entender": fallas, "_plantilla": "no_entendi"}`:
si `pista_de(...)` devuelve algo → plantilla nueva; si no → la de siempre.

Una rama, un `if`. Sin tocar `ORDEN`.

**1.4 · Nombrar la salida en el fallo.**
Hoy `no_entendi` no dice cómo llegar a una persona. Es el pecado 1 —87% dice que
es esencial— y el momento en que más se busca es justo después de un fallo.

Una línea al final de la plantilla.

**1.5 · Re-diagnóstico.**

### ⚠️ El riesgo, y cómo lo atrapamos

**El peligro real de este paso es empeorar el bot.** Si "reconocer lo que sí
entendí" se hace mal, el bot pasa a **afirmar que entendió cosas que no
entendió** — que es el pecado 6 (prometer de más), peor que el pecado 4 que
estamos arreglando.

Tres barandas, y las tres son tests:

| Baranda | El test |
|---|---|
| **Sólo se refleja lo renderizable** | Una fecha ya resuelta por `fechas.py`, un nombre de servicio que salió de la lista de aturno. **Nunca texto crudo del modelo.** Test: entidades con basura adentro → `pista_de` devuelve `None` |
| **Sólo lo de ESTE mensaje** | Reflejar algo que la persona dijo hace cuatro mensajes se lee como que el bot se colgó. Test: entidades vacías en este turno → sin pista |
| **Basura nunca produce pista** | Test: `"!!!!"`, `"..."`, emojis, y un texto largo random → los cuatro caen en `no_entendi` de siempre |

**La cuarta baranda no es un test, es un número:** `python medir_costo.py` antes
y después. Este paso **no debe agregar ni una llamada al modelo** — las
entidades ya vienen en el estado (`Conversacion.entidades`, línea 228 de
`flujo.py`), no hay que ir a buscarlas. Si el costo por conversación sube, algo
se hizo mal aunque los tests estén verdes.

### La verificación

```bash
python diagnostico_reparacion.py   # el número tiene que subir
python test_bordes.py              # casos 1.1 a 1.4 en verde
python medir_costo.py              # MISMO costo que antes
python todos_los_caminos.py        # sigue en 0
python chatear.py                  # y leerlo como cliente, a mano
```

> **El número que dice si funcionó: de 0/6 a 6/6 reconocen.**
> Y el costo por conversación, idéntico.

---

# Paso 2 · Las métricas de verdad

> **Gartner:** solo el 24% de las inversiones en IA de cara al cliente muestra
> retorno positivo. Sin números, este bot es una anécdota — para vos y para el
> negocio que lo pague.

### El diagnóstico

No hay ninguno. El punto de partida es **cero números**.

### La decisión de diseño, antes de escribir

**Los contadores en memoria no sirven acá.** Render reinicia en cada deploy y se
pierde todo — es exactamente el problema que ya tuvo el índice del RAG y que
obligó a `_traer_el_conocimiento`. Y el uso principal de estos números es
mostrárselos a un negocio, o sea que tienen que sobrevivir semanas.

**Va a Postgres.** Ya hay conexión (`cfg.database_url`, el checkpointer se abre
una sola vez al arrancar en `webhook.py:136`). Una tabla nuestra, **no** leer la
del checkpointer: el esquema de LangGraph es de ellos y puede cambiar sin aviso.

### Las tareas

**2.1 · La tabla.**
Una fila por conversación cerrada: `business_id`, `telefono_hash`, `desenlace`,
`estado_final`, `turnos`, `abierta_en`, `cerrada_en`.

`desenlace` es un enum chico: `reservado` · `escalado` · `abandonado` ·
`solo_consulta`.

Teléfono **hasheado**, no en claro: es el pecado 7 y no hace falta el número
para contar.

**2.2 · Escribir la fila.**
En los tres momentos en que una conversación se cierra: al confirmar, al
escalar, y al vencer la sesión. Los tres puntos ya existen en `flujo.py`.

**Envuelto en `try/except` que traga todo y loguea.** Regla 5: si la métrica
falla, la persona recibe su turno igual.

**2.3 · `/metricas`.**
Mismo patrón que `/gasto` (`webhook.py:771`). Devuelve:

| Número | Cuenta |
|---|---|
| **containment** | reservado ÷ total |
| **escalación** | escalado ÷ total |
| **abandono por paso** | en qué estado se cayeron los abandonados |
| **turnos hasta reservar** | mediana |
| **costo por turno resuelto** | `/gasto` ÷ containment |

**2.4 · Calibrar el instrumento.** ← la tarea más importante de este paso

Un `test_metricas.py` que corre **N conversaciones de forma conocida** contra el
doble —3 que reservan, 2 que abandonan en el horario, 1 que escala— y después
afirma que `/metricas` dice exactamente `containment = 50%`, `abandono en
esperando_horario = 2`, etc.

> **Un número que nadie verificó es peor que ningún número**, porque se toman
> decisiones con él y se le muestra a un cliente. Los instrumentos de medición
> se calibran contra un patrón conocido. Éste es el patrón.

### ⚠️ El riesgo, y cómo lo atrapamos

| Riesgo | Cómo se atrapa |
|---|---|
| **La escritura de métricas rompe el flujo** | `test_bordes.py` corre con la tabla caída a propósito: un caso nuevo que apunta a una base inexistente y verifica que el turno se reserva igual |
| **Los números mienten** | Tarea 2.4. Sin ella, este paso no está terminado |
| **"Abandonado" se confunde con "todavía escribiendo"** | Una conversación no está abandonada hasta que vence. Se cuenta al vencer, no al dejar de escribir. Test explícito |
| **Se filtra el teléfono** | Test: ninguna fila contiene un `+549…`. Grep sobre la tabla |

### La verificación

```bash
python test_metricas.py      # el instrumento, calibrado
python test_bordes.py        # incluye el caso de "métricas caídas"
curl localhost:8000/metricas # los cinco números
python simular_panel.py      # que el puente con el panel siga andando
```

> **El número que dice si funcionó:** que `test_metricas.py` prediga
> `/metricas` **exacto**. No aproximado.

**Y recién ahí** se puede contestar la pregunta de venta: *¿cuánto sale un turno
resuelto?* Benchmark contra el que compararse: **65–85% de containment** para
un bot transaccional de alcance angosto.

---

# Paso 3 · El conjunto dorado

> Los tres bugs de esta semana fueron los tres del clasificador. Los tres los
> encontraste vos, a mano, en producción.

### El diagnóstico

El clasificador no tiene ninguna prueba. Acierto medido: **desconocido.**

### Las tareas

**3.1 · `casos.jsonl`.**
Una línea por caso: `{"mensaje": …, "estado": …, "espera": …}`.

Se siembra de tres lados:

1. **Los bugs conocidos** — las 22 formas de negar el nombre (test 19),
   `"quiero otro turno"`, `"no soy Milagros"`, `"hola"` después de reservar.
2. **Las tablas de atajos** — ya son pares mensaje→intención escritos a mano.
3. **Casos difíciles a propósito** — ver el riesgo abajo.

**3.2 · `test_clasificador.py`.**
Corre los casos, imprime **acierto global, acierto por intención, y una matriz
de confusión**. La matriz es lo que importa: dice *con qué* se confunde cada
intención, que es donde estuvieron los tres bugs.

**3.3 · La baranda de plata.**
Antes de correr, imprime cuánto va a costar y pide confirmación. Respeta
`TOPE_DIARIO_USD`. Si el proveedor no contesta, sale con un mensaje claro, no
con un stack trace.

**3.4 · El piso.**
Un umbral por intención. El script sale con ≠ 0 si alguna baja de ahí. Se fija
después de la primera corrida — no antes, porque no sabemos el número.

**3.5 · El ritual.**
Una línea en `CLAUDE.md`: **cada bug del clasificador entra a `casos.jsonl`
antes de arreglarse.** Es lo que hace que el conjunto crezca solo.

### ⚠️ El riesgo, y cómo lo atrapamos

**El riesgo grande es que el conjunto dorado sea un sello de goma.** Si se
siembra sólo con casos que ya funcionan, da 100% en la primera corrida y no
informa nada nunca más.

**Cómo se evita:** meter a propósito casos que **hoy fallan o son ambiguos**, y
anotar la respuesta actual como línea de base, no como esperada. Ejemplos para
arrancar:

- `"el martes que viene a la tarde"` — dos entidades en un mensaje
- `"cuánto sale y cuándo tenés lugar"` — dos intenciones a la vez
- `"dale"` en un paso que no es la confirmación
- `"no"` a secas en cada uno de los seis pasos
- `"mi vieja no puede el jueves, yo sí"` — negación anidada

> **Si la primera corrida da 100%, el conjunto está mal armado.**
> Un conjunto dorado sano arranca entre 80% y 90%.

### La verificación

```bash
python test_clasificador.py   # acierto + matriz de confusión
```

> **El número que dice si funcionó:** que exista un número. Hoy no hay.
> Y que la matriz de confusión muestre al menos una confusión real — si está
> perfectamente diagonal, volver a 3.1.

---

# Cómo sabemos que rompimos algo

Las señales, y qué significa cada una:

| Señal | Qué pasó | Qué hacer |
|---|---|---|
| `todos_los_caminos.py` baja de **49 caminos** | Se rompió una rama del flujo | Revertir el commit. No "arreglarlo encima" |
| Aparece **"cosas para mirar" > 0** | Un camino quedó sin CTA o con un error técnico | Ídem |
| `test_flujo.py` en rojo | Se rompió un invariante de redacción o de orden | Ídem — son los que protegen la voz del producto |
| `medir_costo.py` **sube** | Algo llama al modelo de más | Buscar el atajo que se rompió. Casi siempre es una tabla que dejó de matchear |
| `test_bordes.py` rojo en un caso **viejo** | Regresión clásica | Revertir |
| El bot contesta bien pero **`/metricas` no cuenta** | La escritura falla en silencio (regla 5 funcionando) | Mirar los logs. No urgente: el bot anda |
| **El diagnóstico no se mueve** | El cambio no hizo nada | El más peligroso de todos, porque todo está en verde. Volver al Paso 0 |

### La regla del tercer intento

Si un arreglo falla **tres veces**, el problema no es el arreglo: es el diseño.
Parar, no intentar un cuarto, y replantear. Ya pasó con el nombre esta semana —
el segundo intento hizo que "no me llamo Milagros" contestara "listo, te anoto
como Milagros", peor que el bug original.

---

# Lo que no se toca

- **El grafo de tres nodos.** `entender → avanzar → responder`.
- **La máquina de estados.** `ORDEN`, `AVANZA_CON`, `ELIGE_DE_LISTA`.
- **Que el LLM no redacte.** Es lo que hace imposibles los pecados 5 y 6 —
  la empatía falsa y prometer de más. Es la mejor decisión del repo y no está
  en discusión.
- **El adaptador de aturno.** La lógica de turnos vive allá.
- **El webhook contestando 200 al instante.**

---

# El orden, y por qué

| # | Paso | Por qué ahí |
|---|---|---|
| **0** | El termómetro | Sin número de partida, el resto es opinión |
| **1** | La reparación | El pecado más frecuente, hoy manejado con la estrategia peor puntuada. Es lo que más cambia lo que vive la persona |
| **2** | Las métricas | Lo único que dice si el paso 1 sirvió. Y lo que necesitás para vender |
| **3** | El conjunto dorado | El único que **evita** bugs. Va último porque cuesta plata y necesita que lo demás esté quieto |

Los pasos 1 y 2 se pueden hacer en cualquier orden. El 0 va primero y el 3 va
último.

---

# Línea de base

**20 de agosto de 2026.**

| Métrica | Valor |
|---|---|
| pasos que reconocen al fallar | **0 / 6 → 6 / 6** ✅ Paso 1 |
| salida a un humano tras un fallo | **no → sí, desde el 2º** ✅ Paso 1 |
| costo por conversación | **0,00176 USD** · 88% de mensajes no llegan al modelo |
| caminos coherentes | 49 / 49, 0 para mirar |
| containment | **medido** ✅ Paso 2 · `/metricas` |
| listas vacías que piden un número | **3 → 0** ✅ |
| acierto del clasificador | **92,9%** ✅ Paso 3 · 56 casos |

---

# Bitácora

## Paso 0 · El termómetro — hecho

**0.1 se disolvió.** Iba a ser un script que imprimiera "0 de 6". El propio repo
tiene la doctrina en contra: *"un guion que no afirma nada no protege nada"*.
Fue como afirmaciones en `test_bordes.py [20]`, que se ponen en rojo solas.

**0.2 corrió, y encontró lo que tenía que encontrar.**

Pregunta: ¿se puede leer el estado final de las conversaciones desde afuera del
checkpointer? De eso dependía todo el diseño del Paso 2.

| | |
|---|---|
| **Se puede leer** | ✅ `checkpoint->'channel_values'->>'estado'` en el jsonb. SQL crudo y la API de LangGraph dan **el mismo resultado**, así que el dato es confiable |
| **Pero no sirve de historia** | ⚠️ De 45 conversaciones en la base, **38 no tienen `estado`** — son de versiones viejas del grafo, con otro esquema. Y **ninguna** está en `confirmado` |

**Consecuencia para el Paso 2: no hay backfill.** Los números arrancan de cero
el día que se escriba la tabla. Eso no cambia el diseño —la tarea 2.2 ya era
escribir una fila propia al cerrar— pero mata la idea de recuperar historia.

Barato de descubrir ahora, caro de descubrir después de escribir la tabla, el
endpoint y sus tests.

## Paso 1 · La reparación — hecho

Las cuatro tareas, en un commit. Verde en el tablero completo.

**El riesgo apareció, y era el que el plan anticipaba.** Escribiendo la tarea
1.2 salió el caso que convertía el arreglo en algo peor que el bug:

> `"no quiero el jueves"` trae `fecha=jueves` **exactamente igual** que
> `"quiero el jueves"`. El clasificador extrae la entidad, no el signo.

Sin baranda, el bot le contestaba *"entendí que querés algo para el jueves"* a
alguien que acababa de decir lo contrario — y con cara de haberlo entendido,
que es el pecado 6 y es peor que el pecado 4 que estábamos arreglando.

Se resolvió con `hay_negacion()` en `estados.py`, y **los seis casos quedaron
en el test**. Es la baranda que el plan pedía: *sólo se refleja lo renderizable*.

**El control de tokens pasó:** `medir_costo.py` da idéntico antes y después.
Las entidades ya venían en la misma clasificación; no se agregó ni una llamada.

---

## Paso 2 · Las métricas — hecho

`src/metricas.py` + `/metricas` + `test_metricas.py`. Tres decisiones salieron
distinto de lo planeado:

**El abandono no se escribe, se calcula al leer.** Nadie avisa que abandonó. Si
se esperara a marcarlo haría falta un barrendero periódico, y una conversación
que nunca vuelve no se marcaría jamás. Una conversación abierta y callada hace
más de dos horas **es** un abandono, y eso se decide con un `where`.

**El hilo se guarda hasheado.** Lleva el teléfono adentro, y para contar no hace
falta saber de quién es la conversación. Guardarlo sería el pecado 7 cometido
del lado nuestro. Hay un test que lo verifica.

**Las ventanas se alinearon.** `gasto.py` lleva la cuenta del día; las métricas,
de siempre. Dividir una por otra daba un número que parecía un costo y no lo
era — y que encima bajaba solo, lo cual lo hacía peor: parecía una mejora. De
ahí `resumen(solo_hoy=True)` y el bloque `hoy` aparte.

### La calibración encontró dos cosas

**1 · Un cast que faltaba.** El instrumento escribía bien y leía cero. La regla
de "nunca levanta" se lo tragó y lo dejó en un `logger.warning`:
`could not determine data type of parameter $1`. Sin `test_metricas.py`
afirmando números exactos, `/metricas` habría devuelto ceros para siempre y
nadie se habría enterado — que es justo lo que la tarea 2.4 existía para evitar.

**2 · Un bug del bot, de los que ve un cliente.** Verificando de punta a punta
apareció esto:

```
Elegí el servicio:

Respondé con el número.
```

Un negocio sin servicios cargados —el estado normal de cualquiera recién dado
de alta— recibía una lista vacía y un pedido de elegir un número que no existe.
Y como ninguna respuesta podía ser válida, era además un bucle: el pecado 2.
Pasaba en **dos de las tres listas** (servicios y horarios); la de días daba un
no sin ninguna puerta.

`apertura` ya se protegía de esto desde que pasó en producción. El resto, no.
Arreglado en su propio commit, con `test_bordes.py [22]` cubriendo las tres.

### Verificado contra el estado real

Tres conversaciones por el webhook de verdad, con los números comprobables a
mano:

| | |
|---|---|
| 3 cerradas (1 reserva + 2 escaladas) | `containment: 0.3333` = 1/3 ✅ |
| 7 mensajes hasta reservar | `turnos_hasta_reservar: 7` ✅ |
| gasto del día ÷ 1 reserva | `usd_por_turno_resuelto: 0.00342` ✅ |

Y el costo por turno sigue en **0,00176 USD**: las métricas no llaman al modelo.

---

## Paso 3 · El conjunto dorado — hecho

`casos.jsonl` (56 casos) + `test_clasificador.py`. **89,3% → 92,9%** en la
misma sesión, porque el conjunto encontró un bug y el bug se arregló.

### El costo estaba mal en el plan, por cien

El plan decía *"~100 casos ≈ US$ 0,002"*. Ese es el costo de **una** llamada, no
de cien. Cien casos cuestan ~US$ 0,18; los 56 de hoy, **US$ 0,079**. Corregido
en el archivo, que además imprime el número antes de gastar y pide confirmación.

### Lo que encontró, en orden

**1 · Mi propio evaluador estaba mal.** La primera corrida dio 83,9% y marcó
como fallas cuatro casos que el bot **resuelve bien**: «no soy Milagros», «ese
no es mi nombre», «mejor otro servicio». Los resuelven las tablas de código
—`correccion_de_nombre`, `pedido_de_cambio`— antes de que el modelo vea nada, y
el evaluador llamaba al clasificador derecho.

Un evaluador que mide una pieza suelta en vez del camino real **inventa bugs
que no existen y esconde los que sí**. Corregido: `_sin_modelo()` replica el
orden de `entender`, y hay una nota en `CLAUDE.md` para que se mantengan
sincronizados. Con eso, 89,3% — y `dar_nombre` al 100%, o sea que los arreglos
de esta semana funcionan.

**2 · Un bug real, y con dato.** La matriz de confusión mostró
`elegir_servicio → ver_mas ×2`:

```
quiero otro turno          →  ver_mas   (esperaba elegir_servicio)
quiero sacar otro turno más →  ver_mas
```

La regla del prompt era *«"más", "otro horario", "más tarde" -> ver_mas»* y el
modelo generalizaba de **otro horario** a **otro turno**, que son lo contrario:
uno pide más de la lista que está viendo, el otro empieza de cero.

La conversación terminaba bien igual, pero **por casualidad** —el reinicio de
CONFIRMADO la lleva a la apertura antes de que la intención equivocada haga
daño—. Andar de casualidad es lo que este repo resuelve con tablas.

Arreglado en los dos lados: una entrada nueva en `ATAJOS` para `CONFIRMADO` (que
además lo hace gratis) y la regla del prompt angostada. `elegir_servicio` pasó
de 71% a **100%** y el global a **92,9%**.

### La deuda que queda, a la vista

Los cuatro errores restantes son todos frases con "no" que el modelo lee como
`rechazar`: «no» suelto en el paso del día y del horario, «el jueves no puedo»,
«mi vieja no puede el jueves, yo sí».

**No se ve en pantalla**: el flujo sólo honra `RECHAZAR` en el resumen, y fuera
de ahí cae en el pedido del paso — lo mismo que haría con `desconocido`. Pero
está, y ahora se mide. Queda como piso explícito en `PISO`, no bajado para que
pase: dejado como está para que se note el día que empeore.

### El piso ya juzga

Fijado después de la primera corrida, con tres niveles y un motivo cada uno:
**1.00** donde fallar es inaceptable (`hablar_con_persona`, `rechazar`,
`confirmar`), **0.85** donde hoy da 100% pero depende del modelo, y el número
actual redondeado para abajo en las dos que arrastran deuda.

---

# Lo que queda

Los cuatro pasos del plan están hechos. Lo que sigue no es de este plan:

- **Vía B** — enchufar `src/canal/` en el webhook, `TENANTS` dinámico, la
  ventana de 24 h. Se puede hacer y probar con Twilio, sin cuenta de Meta.
- **La deuda del clasificador** — las frases con "no" que caen en `rechazar`.
- **Esperando a Meta** — Embedded Signup, plantillas, envío real.

---

# Bloque 1 · Que no suene a robot — hecho

Cinco pasos, cinco commits. El bot contesta la pregunta que le hicieron en vez
de volcar la ficha del negocio, y nada sale al aire sin pasar por un guardián.

## El antes y el después

```
👤 abren los sábados?

ANTES  🤖 Lunes a viernes de 9:00 a 18:00. Sábados de 9:00 a 13:00.
          Domingos y feriados permanecemos cerrados.

AHORA  🤖 Sí, abrimos los sábados de 9:00 a 13:00.
```

```
👤 cuánto sale el corte?

ANTES  🤖 Corte de pelo: 30 minutos, $8.000        ← un renglón de base de datos
AHORA  🤖 El corte de pelo sale $8.000 y tarda 30 minutos.
```

```
👤 hacen depilación láser?          (el negocio no cargó esa respuesta)

ANTES  🤖 Ese dato no lo tengo cargado. ¿Querés que te saque un turno?

AHORA  🤖 Ese dato no lo tengo cargado y no quiero mandarte cualquier cosa.

          De lo que sí te puedo contar:
          · Servicios y precios      · Formas de pago
          · Horarios de atención     · Dónde estamos y cómo llegar
          · Cómo sacar y cancelar un turno
```

## La decisión que sostiene todo

**El guardián se escribió y se probó ANTES que la redacción.** Un guardián
escrito después termina ajustado para dejar pasar lo que el modelo ya produce, y
ahí deja de guardar nada.

Al terminar el paso 2 el guardián existía, atrapaba las 22 invenciones de
`casos_invencion.jsonl` y no frenaba ninguna de las 8 respuestas legítimas —
**y todavía no había nadie que redactara**.

## Los números

| | |
|---|---|
| Preguntas normales redactadas | **13 de 15** · rechazo **13%** (techo 30%) |
| Preguntas adversarias frenadas | **13 de 18** · rechazo **72%** |
| **Invenciones que se escaparon** | **0 de 18** |
| Costo por pregunta redactada | **0,00047 USD** — 4× más barato que clasificar |
| Costo por turno | **0,00181 USD, sin cambios** |
| Llamadas al modelo en el camino «no tengo el dato» | **0** |

El contraste 13% / 72% es lo que dice que el guardián **discrimina** en vez de
rechazar al azar: frena lo que hay que frenar y deja pasar lo que hay que dejar.

## Lo que salió distinto del plan

**El paso 3 no necesitó modelo.** Estaba planeado que el modelo *eligiera* una
sección adyacente con salida estructurada. Escribiéndolo apareció algo mejor:
los títulos de las secciones son los `##` que cargó el negocio y salen del
índice con un filtro por metadato — sin embeddings, sin modelo, sin costo, y sin
posibilidad de nombrar un tema que no exista.

**Apareció una puerta que no estaba en el plan.** Con DOS fragmentos del RAG, la
fuente son dos respuestas a dos preguntas distintas pegadas, y una reescritura
las puede fundir en algo que no dice ninguna —"el corte se paga en efectivo"—
con todas las palabras viniendo de la fuente. `verificar` no lo vería. Por eso
sólo se redacta con un fragmento.

## Lo que encontró la medición

**El guardián frenó una respuesta que suena impecable:**

```
👤 puedo pagar con Mercado Pago?
🤖 (el modelo escribió) "No aceptamos Mercado Pago. Las formas de pago
    disponibles son efectivo, transferencia y tarjeta de débito."
```

Se lee perfecto y **es una afirmación sin respaldo**: la fuente no dice que no lo
acepten, simplemente no lo menciona. Negar algo que no está es inventar igual
que afirmarlo.

**Y frenó tres respuestas correctas por sinónimos** —«tarda», «local»,
«sirven»—. Los tres entraron a `casos_invencion.jsonl` con su caso.

## El desgaste que hay que vigilar

La regla del vocabulario se apoya en una lista de verbos que **no está completa
ni puede estarlo**. Con un negocio nuevo van a aparecer verbos nuevos, y cada
uno frena una respuesta correcta.

Lo que lo hace sostenible: frenar de más no rompe nada (sale el texto literal),
cada rechazo se loguea con la palabra exacta, y el porcentaje se mide. **Si el
rechazo sube mucho, la función está degradada aunque nada esté "roto"** — ese es
el momento de revisarla.

La regla que no se negocia: **nunca se agrega un sustantivo a esa lista.** Los
verbos son la forma de decir algo; los sustantivos son la cosa dicha, y ahí es
donde vive la invención.

## Lo que este guardián no puede atrapar

Una verificación léxica no entiende el sentido. La regla de negación cubre la
forma frecuente ("no aceptamos X" → "sí, X"), no todas las formas posibles.

Las 18 preguntas adversarias no encontraron ninguna, pero 18 no es una prueba:
es la evidencia que hay. `probar_invencion.py` está para volver a correrla con
preguntas nuevas cada vez que haya una duda.

## Encontrado de paso, sin arreglar

**El RAG no encuentra la sección de pagos con «aceptan tarjeta?»** — devuelve
cero fragmentos teniendo la respuesta cargada. Es un problema de relevancia
anterior a este bloque: `datos/demo-peluqueria.md` no tiene las líneas `>` con
las preguntas que el panel escribe para que la búsqueda enganche. Anotado.
