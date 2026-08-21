# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Todo el proyecto —código, docstrings, docs y mensajes de commit— está en
español. Escribí en español acá también.

## Comandos

```bash
./run.sh                      # crea venv, instala, arma el índice del RAG y levanta uvicorn en :8000
docker compose up             # bot + Postgres + Phoenix
./venv/bin/python -m src.rag.indice   # reconstruir el índice de Chroma (recrear=True)
```

No hay pytest ni runner: **cada prueba es un script suelto** que sale con
código ≠ 0 si algo falla. Correr una sola es correr su archivo.

```bash
python test_flujo.py          # 5 invariantes de redacción y de orden de pasos
python test_bordes.py         # lo que pasa cuando la persona NO hace lo esperado (sin LLM)
python test_aislamiento.py    # el RAG no filtra datos entre negocios
python test_recuperacion.py   # relevancia del RAG, umbral 85%
python test_ataques.py        # inyección, fuga, abuso de flujo, costo
python test_demora.py         # aviso de demora y techo de tiempo
python test_conocimiento.py   # lo que carga el panel sobrevive un reinicio (usa embeddings reales)
python test_metricas.py       # el instrumento de /metricas, calibrado contra un patrón conocido
python test_observabilidad.py # 5 escenarios trazados (necesita `phoenix serve` + PHOENIX_HABILITADO=true)
python todos_los_caminos.py   # recorre las bifurcaciones e imprime lo que ve la persona
```

Contra el aturno **real** (escriben en la agenda de verdad; sin la flag corren
contra el doble en memoria):

```bash
python verificar_turno.py --real        # 4 conversaciones completas + busca el turno en la agenda
python probar_senia.py --real           # el servicio con seña queda en pending_deposit
python probar_aturno_real.py <slug>     # solo lee; con --reservar CREA un turno real
python revisar_negocio.py <slug>        # ¿este negocio está bien configurado para prender el bot?
```

Para iterar sin gastar el cupo de Twilio (50 mensajes/día en trial):

```bash
python chatear.py                       # conversar por terminal contra el doble
python chatear.py --negocio <slug>      # contra el aturno real
python medir_costo.py                   # tokens y costo por turno reservado
python simular_panel.py                 # los dos caminos bot ↔ panel, con un aturno de mentira
```

## Arquitectura

`README.md` tiene los diagramas y el porqué de cada decisión. Lo mínimo para
tocar código:

**Grafo LangGraph de tres nodos** ([src/agentes/flujo.py](src/agentes/flujo.py)):
`entender → avanzar → responder`. El checkpointer de Postgres guarda el estado
entre mensajes y es a la vez la sesión del producto.

- `entender` — si el mensaje es un número o un atajo de la tabla `ATAJOS`, se
  resuelve en código; solo el texto libre llega al LLM.
- `avanzar` — máquina de estados determinística. Nunca inventa saltos: consulta
  la tabla `ORDEN` de [src/agentes/estados.py](src/agentes/estados.py).
- `responder` — elige y renderiza una plantilla.

**El LLM solo clasifica.** [src/agentes/clasificador.py](src/agentes/clasificador.py)
es el único lugar que lo invoca; devuelve `Intencion` (enum cerrado) +
entidades, con `with_structured_output`. **Nunca redacta el texto que lee una
persona**: todo eso sale de [src/plantillas.py](src/plantillas.py). Si hace
falta un mensaje nuevo, es una función nueva en plantillas, no una instrucción
en el prompt.

**La lógica de turnos no se replica acá.** Disponibilidad, conflictos, bloqueos
y señas los decide el backend de aturno. El contrato es
[src/aturno/base.py](src/aturno/base.py), con dos implementaciones
intercambiables por `ATURNO_MODO`: `doble` (en memoria, [doble.py](src/aturno/doble.py))
y `api` ([api.py](src/aturno/api.py)). Las tools hablan solo con la interfaz,
nunca con `httpx` directo.

**El webhook contesta 200 vacío al instante** y manda la respuesta después como
mensaje nuevo por la API REST de Twilio ([src/api/webhook.py](src/api/webhook.py)).
Twilio corta y reintenta si el webhook demora, y la persona recibe todo dos
veces. No cambiar esto por TwiML.

**Puente con el panel de aturno** ([src/api/conversaciones.py](src/api/conversaciones.py)):
`bot → panel` avisa cada mensaje, `panel → bot` entra por los endpoints
`/panel/responder`, `/panel/tomar`, `/panel/devolver`, `/panel/reindexar`.
Autentican con un secreto compartido (`PANEL_SECRETO`), no con Firebase: este
servicio no tiene ni quiere credenciales de Firestore.

**aturno es la única fuente de verdad del conocimiento del negocio.** El panel
lo empuja por `/panel/reindexar`, y además el bot lo **trae al arrancar**
(`_traer_el_conocimiento`). Las dos vías hacen falta: `reindexar_negocio`
escribe en `datos/`, que en Render es disco efímero, así que sin el arranque
todo lo cargado por el panel se pierde en cada deploy. Los `datos/*.md` del repo
son semilla de desarrollo, no el estado real.

## Reglas que el diseño hace cumplir — no romperlas

- **Ningún archivo llama a `date.today()` ni a `datetime.now()`.** Todo pasa por
  [src/fechas.py](src/fechas.py), que fija el huso del negocio. El contenedor
  corre en UTC y sin esto "hoy" pasa a ser mañana después de las 21:00.
- **La aritmética de fechas va en código, nunca en el prompt.** Al modelo se le
  pasa una tabla de los próximos días ya resueltos.
- **El `business_id` va en el constructor de `Recuperador`**
  ([src/rag/indice.py](src/rag/indice.py)), no como parámetro de la búsqueda.
  Un `filtro=` opcional es exactamente el bug que este diseño hace imposible.
- **`business_id` == slug de aturno == nombre del archivo en `datos/`.** Un solo
  identificador para la API, el RAG y el ruteo por número.
- **`ELIGE_DE_LISTA` no es cosmético.** Solo en esos pasos un "3" se resuelve
  como renglón. Agregar `ESPERANDO_CONFIRMACION` ahí hacía que contestar "no"
  al resumen reservara el turno igual. `RECHAZAR` ≠ `CANCELAR`: el primero no
  borra lo elegido.
- **Ningún nombre de modelo, URL ni credencial hardcodeado.** Todo sale de
  [src/config.py](src/config.py), validado con pydantic-settings al importar.
- **Las plantillas no llevan markdown** (WhatsApp no lo renderiza) y los
  listados son verticales con `\n` real, un ítem por línea.
- **Lo secundario no puede tumbar lo principal.** [arranque.sh](arranque.sh) no
  usa `set -e` a propósito: si el índice del RAG no se puede construir, el bot
  arranca igual y contesta "ese dato no lo tengo". Lo mismo con Phoenix, que
  falla blando.
- **Duplicación conocida y única:** el cálculo de horarios candidatos en
  [src/aturno/api.py](src/aturno/api.py) es un port de `generateTimeSlots` /
  `getStaffSpecificTimeRanges` de `aturno/src/components/BookingCalendar.jsx`.
  Si allá cambian las reglas de horarios, hay que tocar acá.

## El clasificador tiene su propio test, y cuesta plata

```bash
python test_clasificador.py   # 56 casos contra `casos.jsonl`. NO es gratis (~0,08 USD)
```

Es el único que llama al modelo, así que **no va en el tablero de siempre**: se
corre cuando se toca el prompt, el `ESQUEMA`, la tabla `ATAJOS` o el enum
`Intencion`. Pregunta antes de gastar y respeta `TOPE_DIARIO_USD`.

Dos reglas que lo mantienen útil:

- **Cada bug del clasificador entra a `casos.jsonl` ANTES de arreglarse.** Es lo
  que hace que el conjunto crezca solo y que un bug arreglado no vuelva.
- **Lo que hay que leer es la matriz de confusión, no el porcentaje.** Dice con
  qué se confunde cada intención, que es donde estuvieron los cuatro bugs de
  agosto. Un 100% global no es una buena noticia: significa que los casos son
  demasiado fáciles y el archivo sale con ≠ 0 avisándolo.

`_sin_modelo()` en ese archivo **replica el orden de `entender`**. Si `entender`
gana un escalón nuevo, hay que agregarlo allá también: la primera versión medía
el clasificador suelto y marcó como fallas cuatro casos que el bot resuelve bien.

## Verificar contra el estado real, no contra lo que el bot dice

Un bot que anuncia un turno sin reservarlo es peor que uno que falla. Las
pruebas que valen consultan aturno después con el código que se le dio a la
persona. `todos_los_caminos.py` imprime y `test_bordes.py` afirma: el peor bug
del proyecto salía en pantalla en cada corrida de la primera y nadie lo vio.

## Docs del repo

| Archivo | Qué es |
|---|---|
| [README.md](README.md) | Arquitectura, diagramas y el porqué de cada decisión de diseño |
| [PENDIENTES.md](PENDIENTES.md) | Qué falta, qué está a medias y qué se dejó afuera a propósito |
| [AUDITORIA_BOT.md](AUDITORIA_BOT.md) | Auditoría de comportamiento, caso por caso |
| [SEGURIDAD.md](SEGURIDAD.md) | Credenciales a rotar antes de un cliente real |
| [TODO-PANEL.md](TODO-PANEL.md) | Lo que falta del lado de aturno (otro repo) |
| [ROADMAP.md](ROADMAP.md) | **Empezá acá**: qué está hecho, qué falta, si se puede, y qué puede salir mal |
| [INVESTIGACION.md](INVESTIGACION.md) | Qué dice la investigación sobre chatbots, y qué le falta a éste |
| [PLAN.md](PLAN.md) | El plan de mejoras derivado de esa investigación, con bitácora de lo que se encontró |

## Commits

Mensajes en español, en una línea, contando **qué cambió para el usuario o para
el sistema** — no el archivo tocado. Sin prefijos tipo `feat:`. Ejemplos del
historial: "Si el proveedor del modelo se cae, contesta otro", "El indice caido
ya no tumba el servicio".
