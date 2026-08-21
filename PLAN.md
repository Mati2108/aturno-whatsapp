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
| containment | sin medir — Paso 2 |
| acierto del clasificador | sin medir — Paso 3 |

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

# Próximo: Paso 2 · Las métricas

Sin backfill. Tabla nueva, fila al cerrar, `/metricas`, y la tarea 2.4
—calibrar el instrumento contra conversaciones de forma conocida— que es la que
decide si el paso está terminado.
