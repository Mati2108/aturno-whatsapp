# Auditoría del bot de WhatsApp

Diagnóstico, no implementación. Nada de lo que sigue está arreglado.

**Fecha:** 18/08/2026 · **Repo:** `~/Aturno-WhatsApp` (el bot vive acá, no en
`~/Aturno`) · **Commit auditado:** el árbol de trabajo actual.

> **Estado al 18/08/2026, después de arreglar.** Los hallazgos A-1 a A-13 están
> corregidos y verificados por `test_bordes.py` (40 aserciones, todas sin LLM).
> Quedan abiertos: la falta de crédito en Anthropic (sección 0, no es código),
> A-14 (`TENANTS` a mano) y el read receipt (2.7, depende del proveedor). Cada
> hallazgo dice abajo cómo quedó.
>
> **Y una corrección: A-10 estaba MAL.** Lo di como "red faltante" y era una
> función deliberada; lo cuenta el propio hallazgo, abajo.

Cada hallazgo dice cómo se verificó:

| Marca | Qué significa |
|---|---|
| 🧪 | **Ejecutado**: se reprodujo corriendo código contra el doble en memoria |
| 📖 | **Leído**: se afirma leyendo el código, sin ejecutarlo |
| ❓ | **No verificable acá**: hace falta Twilio real o un despliegue; se propone cómo probarlo |

---

## 0. Lo primero, porque cambia todo lo demás

### 🔴 El bot está degradado EN PRODUCCIÓN ahora mismo

🧪 `GET /diagnostico` del servicio desplegado contesta:

```
anthropic: {"valida": false, "detalle": "Error code: 400 ... invalid_request"}
twilio:    {"valida": true}
gemini:    {"valida": true}
```

Localmente el mismo error sale completo: **`Your credit balance is too low to
access the Anthropic API`**. O sea que el clasificador falla en *todos* los
mensajes y cae en `DESCONOCIDO` ([clasificador.py:138-140](src/agentes/clasificador.py#L138)),
que es el fallback correcto — pero significa que hoy **el bot solo entiende
números y las frases de la tabla `ATAJOS`**.

Consecuencia concreta, reproducida 🧪: **un cliente nuevo no puede sacar
turno**. El paso del nombre es obligatorio para quien no reservó antes, y
`DAR_NOMBRE` sale únicamente del LLM (no hay atajo ni resolución por código).
El bot repite "necesito tu nombre y apellido", a los dos intentos cuenta que se
trabó y lo deriva a una persona ([flujo.py:397-399](src/agentes/flujo.py#L397)).
Todo el camino feliz muere ahí.

### 🔴 Y `/salud` dice que está todo bien

📖 El `/salud` desplegado responde `{"estado":"ok", ...}` **sin los campos
`puede_responder` ni `detalle`** — o sea que el build en Render es anterior al
chequeo `_llm_responde()` ([webhook.py:591-622](src/api/webhook.py#L591)) que
existe justamente para esto. El comentario de esa función describe el escenario
exacto que está pasando hoy, palabra por palabra.

Mientras no se redespliegue, el panel de aturno lee ese "ok" y le muestra
"conectado" al dueño con el bot sin poder pensar.

**Acción:** cargar crédito, redesplegar, y confirmar que `/salud` devuelva
`puede_responder: true`.

---

# FASE 1 · El árbol de estados y todos los caminos

## 1.1 La máquina

Definida en [estados.py:35-98](src/agentes/estados.py#L35). Nueve estados; seis
forman el formulario (`ORDEN`), tres están fuera de él.

```
                    ┌──────────────────────────────────────────┐
                    │  APERTURA  (nadie se queda acá: el 1er   │
                    │  mensaje siempre sale con la apertura)    │
                    └───────────────────┬──────────────────────┘
                                        ▼
   ESPERANDO_SERVICIO ──► ESPERANDO_STAFF ──► ESPERANDO_DIA ──►
   ESPERANDO_HORARIO ──► ESPERANDO_NOMBRE ──► ESPERANDO_CONFIRMACION ──► CONFIRMADO
        │  (se saltea STAFF si hay ≤1 persona; NOMBRE si el teléfono es conocido)
        │
        └──► EN_MANOS_HUMANAS  ◄── desde CUALQUIER paso, y vuelve al mismo
```

Reglas estructurales, todas 📖 verificadas en el código:

| Regla | Dónde |
|---|---|
| Nunca salta hacia adelante; solo avanza uno o saltea los que no aplican | [`siguiente()`](src/agentes/estados.py#L101) |
| Retroceder sí está permitido, y limpia lo elegido después de ese paso | [flujo.py:427-434](src/agentes/flujo.py#L427), [`_limpiar_desde`](src/agentes/flujo.py#L475) |
| El texto que ve la persona SIEMPRE sale de una plantilla, nunca del LLM | [flujo.py:805-975](src/agentes/flujo.py#L805) |
| El LLM solo clasifica intención + entidades, con enum cerrado | [clasificador.py:53-62](src/agentes/clasificador.py#L53) |
| `CONFIRMADO` + mensaje nuevo = pedido nuevo, conservando quién sos | [flujo.py:306-311](src/agentes/flujo.py#L306) |

Esto está muy bien resuelto y es la razón por la que la mayoría de la matriz de
abajo sale en verde. Los problemas están en los bordes, no en el diseño.

## 1.2 Los tipos de mensaje: se resuelven ANTES del flujo, no por estado

Hallazgo de forma, y es bueno: el tipo de mensaje **no depende del paso**. Se
decide en el webhook antes de tocar la máquina de estados
([webhook.py:679-701](src/api/webhook.py#L679)), así que la matriz "qué pasa si
mando un audio en el paso X" tiene la misma respuesta para los nueve estados.
Por eso va una sola tabla y no nueve.

| Lo que manda | Qué llega de Twilio | Qué hace hoy | ¿Gasta LLM? | Estado |
|---|---|---|---|---|
| Texto que matchea la lista ("3", "Lean", "10:30") | `Body` | Resuelve por código contra `opciones` | **No** | ✅ [flujo.py:213-227](src/agentes/flujo.py#L213) |
| Frase fija ("dale", "cancelar", "una persona") | `Body` | Tabla `ATAJOS`, cerrada y por paso | **No** | ✅ [estados.py:152-213](src/agentes/estados.py#L152) |
| Texto libre | `Body` | Va al clasificador | Sí (~1.677 tokens) | ✅ correcto que vaya |
| **Más de 12 en un minuto** | `Body` | Se avisa una vez y se deja de atender hasta que pase la ventana | **No** | ✅ **A-12 arreglado** |
| **Audio / nota de voz** | `NumMedia=1`, `audio/ogg` | "Todavía no puedo escuchar audios. ¿Me lo escribís?" | **No** | ✅ [plantillas.py:544-567](src/plantillas.py#L544) |
| **Imagen / foto** | `NumMedia=1`, `image/*` | "Todavía no puedo ver imágenes" | **No** | ✅ |
| **Video** | `NumMedia=1`, `video/*` | "Todavía no puedo ver videos" | **No** | ✅ |
| **Documento / PDF** | `NumMedia=1` | "Todavía no puedo abrir archivos" | **No** | ✅ |
| **Sticker** | `NumMedia=1`, `image/webp` | Cae en la rama de imagen | **No** | ✅ |
| **Contacto (vCard)** | `NumMedia=1`, `text/vcard` | "no puedo abrir archivos" | **No** | ✅ ❓ tipo sin confirmar en vivo |
| **Ubicación (pin)** | `Latitude`/`Longitude`, **`NumMedia=0`** | "Me llegó tu ubicación, pero todavía no la puedo usar" | **No** | ✅ **A-3 arreglado** |
| Adjunto **con** epígrafe | `Body` + `NumMedia=1` | Procesa el texto; **el adjunto se ignora sin decirlo** | Sí | ⚠️ sigue abierto |
| Emoji solo ("👋") | `Body` | «No te entendí» + el pedido del paso | **No** | ✅ **A-7 arreglado** |
| Solo espacios ("   ") | `Body` | Ídem, sin gastar modelo | **No** | ✅ **A-7 arreglado** |
| Mensaje vacío real | `Body=""`, sin media | "No me llegó ningún texto. ¿Me lo escribís?" | **No** | ✅ **A-3 arreglado** |
| Mensaje muy largo | `Body` de 10.000 chars | Se recorta a 400 antes del prompt | Sí (acotado) | ✅ [flujo.py:241-243](src/agentes/flujo.py#L241) |
| Mensaje > 4096 chars | `Body` | Se recorta en el borde y se atiende | Sí (acotado) | ✅ **A-8 arreglado** |

## 1.3 Estado por estado

Solo las columnas que cambian por paso. "Respuesta mala" = texto que no
corresponde a ese paso.

### APERTURA
- **Espera:** cualquier cosa. Es la única pregunta abierta del flujo, a propósito ([flujo.py:595-599](src/agentes/flujo.py#L595)).
- **Bien / mal:** da igual — el primer mensaje **siempre** contesta la apertura y avanza a `ESPERANDO_SERVICIO` ([flujo.py:402-406](src/agentes/flujo.py#L402)). Lo que trajo no se pierde: se reinterpreta en el mensaje siguiente. ✅
- ✅ **A-9 arreglado**: un "gracias" o un "hola" después de reservar ya no dispara la bienvenida entera; cierra en una línea y el turno queda como estaba. Un pedido de verdad sí reabre el flujo.

### ESPERANDO_SERVICIO
- **Espera:** número, nombre del servicio, o una pregunta.
- **Bien:** avanza. Match parcial solo si es **inequívoco** ✅ ([estados.py:329-338](src/agentes/estados.py#L329)).
- **Mal:** repite la lista, **precedida de «No te entendí»** (A-4 arreglado).
- Número fuera de rango ("9" con 3 servicios) → `numero_elegido` devuelve `None` → repite la lista ✅.

### ESPERANDO_STAFF
- **Espera:** número, nombre, o "me da igual".
- "Me da igual" tiene atajo sin LLM ✅. Se saltea el paso entero si hay ≤1 persona ✅.
- 🔴 **A-10 era un error MÍO, no del código**: el renglón siguiente al último nombre **es** "Me da igual", así que ese índice tenía que valer. El detalle está en el hallazgo. Sí se agregó la guarda para índices realmente fuera de la lista.

### ESPERANDO_DIA
- **Espera:** número, "el jueves", "mañana", "el 19".
- Todo eso se resuelve **sin LLM** ✅ ([`_dia_por_texto`](src/agentes/estados.py#L228)).
- Un día que el modelo inventa **se verifica contra los días con cupo** antes de guardarlo ✅ ([flujo.py:637-650](src/agentes/flujo.py#L637)) — arreglo importante y bien hecho.
- Día cerrado / completo → explica el motivo, no dice "no hay" ✅.

### ESPERANDO_HORARIO
- **Espera:** número, "10:30", "más".
- El número se resuelve contra **la lista mostrada**, no contra la recalculada ✅ ([flujo.py:653-662](src/agentes/flujo.py#L653)) — bug ya arreglado y documentado.
- Hora inexistente → se verifica contra los libres y se ofrece la más parecida si es un tipeo (±15 min) ✅.
- "más" pagina de a 8; si se pasa del final vuelve a 0 ✅.

### ESPERANDO_NOMBRE
- **Espera:** un nombre.
- **Depende 100% del LLM.** No hay atajo, ni resolución por código, ni `opciones`. 🔴 Con el clasificador caído, **este paso es un muro** (ver sección 0).
- El nombre se limpia de HTML/links/URLs en dos capas ✅ ([schemas.py:77-97](src/schemas.py#L77)) — buena defensa, y el comentario nombra correctamente el agujero de fondo en aturno (el mail HTML sin escapar).

### ESPERANDO_CONFIRMACION 🔴
- **Espera:** "sí", o un nombre distinto si el turno es para otra persona.
- ✅ **A-1 arreglado**: "no", "NO", "Nop", "1" y "2" ya no reservan. Ver el hallazgo.
- Corregir el nombre acá escribe `nombre_del_turno` y no `nombre` ✅ — distinción bien pensada y bien comentada.

### CONFIRMADO
- Cualquier mensaje siguiente arranca un pedido nuevo, conservando la identidad ✅.
- "cancelar" acá dice honestamente que no puede cancelar un turno ya sacado ✅ ([flujo.py:314-332](src/agentes/flujo.py#L314)) — muy buena decisión: prometerlo sería peor.

### EN_MANOS_HUMANAS
- El bot **se calla** ([flujo.py:286-294](src/agentes/flujo.py#L286)) ✅, el mensaje igual queda en el hilo para quien atienda ✅.
- Vuelve solo a los 45 min o con "seguir con el bot" ✅.
- Al volver, retoma el mismo paso y **regraba `opciones`** ✅ ([flujo.py:1008-1011](src/agentes/flujo.py#L1008)) — detalle fácil de olvidar, está.

---

# FASE 2 · Los seis puntos puntuales

### 2.1 Mensaje de bienvenida — ✅ Resuelto (era ⚠️)

📖🧪 Sale una sola vez por conversación nueva y es **idéntico siempre** (lo
garantiza `test_flujo.py` t1). No se duplica ni se saltea en el arranque.

Pero se vuelve a disparar entero después de reservar: cualquier mensaje en
`CONFIRMADO` resetea a `APERTURA` y un "gracias" cae en la rama
`DESCONOCIDO|SALUDO` con `estado == APERTURA` → apertura completa
([flujo.py:375-378](src/agentes/flujo.py#L375)). 🧪 Reproducido: después de
confirmar, "gracias" devolvía el saludo + los tres servicios + "¿querés sacar un
turno?": una pregunta que la persona no hizo, justo después de la que sí.

**Arreglado** (A-9): un saludo estando en CONFIRMADO cierra en una línea y no
reabre el menú. Cualquier otro mensaje sí arranca un pedido nuevo.

### 2.2 Menú de servicios — ✅ Resuelto

📖 Las opciones que se muestran y las que se resuelven salen de **la misma
lista** en la misma vuelta ([flujo.py:917-919](src/agentes/flujo.py#L917)) y se
guardan en el estado. No hay forma de que el "2" apunte a otra cosa. Los días
cerrados no llevan número, a propósito ✅.

### 2.3 "Hablar con un humano" — ⚠️ Parcial, y la mitad importante ya no es la que dice el código

📖 La detección es buena: 11 frases con atajo sin LLM
([estados.py:174-179](src/agentes/estados.py#L174)) + la intención del
clasificador. Funciona desde cualquier paso y **no se pierde nada de lo
elegido** ✅.

Lo que la persona recibe mientras espera: *"Listo, le avisé a X. Te responden
por acá mismo. Mientras tanto no toco nada de lo que veníamos armando"*
([plantillas.py:437-466](src/plantillas.py#L437)). Correcto — y si el aviso NO
llegó, lo dice y pasa el contacto en vez de prometer una respuesta que no va a
venir ✅. Muy bien resuelto.

Dos cosas a corregir:

1. 📖 **`ESCALACION_WEBHOOK` está vacío**, así que `notificar()` devuelve
   `False` ([escalacion.py:76-77](src/escalacion.py#L76)). El aviso llega
   igual **por el panel**, porque `_escalar` cuenta `panel_url + panel_secreto`
   como canal válido ([flujo.py:570-572](src/agentes/flujo.py#L570)) y los dos
   están puestos en producción (confirmado por `/configuracion`). O sea:
   **funciona, pero por un camino distinto del que documenta
   `escalacion.py`**, cuyo encabezado sigue diciendo que la otra mitad "todavía
   no existe". La doc está vieja, no el código.
2. ❓ **Que el panel efectivamente haga ruido** con `necesita_humano: true` no
   se puede verificar desde acá: es la otra punta, en el repo de aturno
   (`POST /api/whatsapp/bot/evento`). Ver el plan de prueba manual.

### 2.4 Formulario / consulta general — ✅ El reparto está bien trazado

📖 Esta es la pregunta mejor resuelta del proyecto. **Ningún dato estructurado
depende de que el LLM adivine**:

| Dato | Quién decide | Verificación |
|---|---|---|
| Servicio | código, contra la lista | match único obligatorio |
| Profesional | código | ídem |
| **Fecha** | el LLM la extrae, **el código la valida** contra los días con cupo | [flujo.py:637-650](src/agentes/flujo.py#L637) |
| **Hora** | el LLM la extrae, **el código la valida** contra los horarios libres | [flujo.py:664-674](src/agentes/flujo.py#L664) |
| Nombre | el LLM lo extrae, el código lo **limpia** y exige ≥2 chars | [schemas.py:256-272](src/schemas.py#L256) |
| Aritmética de fechas | **siempre código**, nunca el modelo | decisión documentada |
| Redacción | **siempre plantilla** | `test_flujo.py` lo verifica |

Donde el LLM SÍ hace falta y está bien que lo haga: entender texto libre
("¿tenés algo el jueves a la tarde?"), detectar frustración, y clasificar
preguntas de información.

Donde el bot NO inventa y debería seguir así: si el RAG no encuentra el dato,
contesta "ese dato no lo tengo cargado" en vez de rellenar
([plantillas.py:609-627](src/plantillas.py#L609)) ✅, y además le manda la
pregunta al panel para que el negocio la cargue ✅.

**Riesgo residual** 📖: el texto que devuelve el RAG sale casi tal cual
(`respuesta_info` solo limpia markdown). Si el negocio carga algo equivocado en
el cuestionario, el bot lo repite con total confianza. Es un riesgo de producto,
no un bug — pero conviene decidir si el conocimiento se revisa antes de indexar.

### 2.5 Timeouts — ✅ Los cuatro (eran tres de cuatro)

| Timeout | Existe | Dónde |
|---|---|---|
| **Respuesta lenta** → aviso a los 10s, y la respuesta real llega igual | ✅ | [webhook.py:826-844](src/api/webhook.py#L826) |
| **Techo duro** a los 30s → mensaje con salida, no "reintentá" | ✅ | [webhook.py:852-866](src/api/webhook.py#L852) |
| **Escalación sin respuesta** → el bot retoma a los 45 min | ✅ | [flujo.py:531-545](src/agentes/flujo.py#L531) |
| **Sesión inactiva** → reiniciar el flujo | ✅ | `sesion_minutos` (30) — ver A-2 |

Los tres primeros están cubiertos por `test_demora.py` 🧪 (con reloj falso, sin
esperar 30s de verdad). El cuarto era el hueco y ahora también está cubierto y probado
(`test_bordes.py` [7], envejeciendo el sello a mano igual que `test_demora.py`
hace con el suyo).

### 2.6 Indicador de "escribiendo…" — ✅ Resuelto (con un costo)

📖 Se usa el endpoint correcto, `POST messaging.twilio.com/v3/Indicators/Typing`
([webhook.py:751-783](src/api/webhook.py#L751)). El comentario documenta que el
SDK no lo expone y que la versión anterior fallaba en silencio desde el primer
día — bien encontrado.

Dos observaciones:
- Sale **una vez por mensaje entrante**, antes de procesar. No sale antes del
  aviso de demora ni de los mensajes que manda el negocio desde el panel. Es
  suficiente, pero no es "antes de cada respuesta del bot".
- 📖 **A-5**: es un `httpx.post` **sincrónico con timeout=4** dentro de una
  función `async`. Bloquea el event loop hasta 4 segundos, y con él a todas las
  demás conversaciones en curso.

### 2.7 Doble check azul / marcar como leído — ❌ No está

📖 No hay ninguna llamada de read receipt en el código: `grep` sobre `src/`
no encuentra nada que marque el mensaje entrante como leído.

❓ **Y probablemente no se pueda hoy**: Twilio Programmable Messaging no expone
un endpoint para marcar leído un mensaje entrante de WhatsApp; la Cloud API de
Meta sí (`POST /messages` con `status: "read"`). Si eso es así, esto se
desbloquea con la migración a Meta que ya está en `PENDIENTES.md` (#8) — que
además, según ese mismo documento, **pierde** el indicador de "escribiendo…".
Hay que confirmarlo en la documentación de los dos proveedores antes de
prometerlo.

---

# FASE 3 · Checklist general de chatbot

## Debe tener

| # | Práctica | Estado | Detalle |
|---|---|---|---|
| 1 | **Anti-loop** | ✅ | Cuenta hasta 2 y escala si la conversación avanzó; y al cuarto sin avanzar ofrece link + persona sin notificar al negocio, que era el callejón (A-11). La condición de `_hubo_avance` se conserva: es lo que evita que cualquiera le haga sonar el teléfono al dueño |
| 2 | **Escalar ante frustración** | ✅ | Por frases explícitas (sin LLM), por intención del clasificador, y automáticamente a los 2 mensajes sin entender |
| 3 | **Límite de reintentos** | ✅ | Dos con avance → persona; cuatro sin avance → salida sola |
| 4 | **Reiniciar / volver al menú** | ✅ | "cancelar", "dejalo", "olvidalo" → vuelve a APERTURA y limpia lo elegido ([estados.py:180-182](src/agentes/estados.py#L180)). Y "volver" retrocede un paso |
| 5 | **Persistencia de contexto** | ✅ | Checkpointer de Postgres, hilo por `negocio:teléfono` ([flujo.py:1031](src/agentes/flujo.py#L1031)). Sobrevive reinicios del servicio |
| 6 | **Errores claros y accionables** | ✅ | Los de sistema ya estaban bien (`no_pudo_contestar` y `demorado` ofrecen salidas en vez de pedir reintentar); los de comprensión ahora dicen «No te entendí» antes de repetir el pedido (A-4) |
| 7 | **Rate limiting** | ✅ | 12 por minuto y por teléfono, avisando una vez (A-12). Es por proceso: con varias instancias el tope efectivo se multiplica |
| 8 | **Logs para depurar una conversación** | ✅ | Entrada, clasificación, envío, escalación, todo con teléfono y paso. Además el panel recibe la conversación completa, no solo las escalaciones |
| 9 | **Confirmar antes de lo irreversible** | ✅ | El "no" ya no confirma, y un número suelto en el resumen tampoco se interpreta (A-1) |

## No debe tener

| # | Anti-práctica | Estado | Detalle |
|---|---|---|---|
| 1 | Loops infinitos sin salida | ✅ | Cerrado con A-11 |
| 2 | Gastar LLM en lo que resuelven reglas | ✅ | Ya estaba muy optimizado; se sumaron el "no" del resumen, las tres frases de "qué cambiar", los mensajes sin contenido y los números sueltos en la confirmación |
| 3 | Prometer lo que no puede cumplir | ✅ | Ejemplar: no dice que cancela turnos, no manda links si no hay URL configurada, no promete respuesta humana si el aviso no salió |
| 4 | Estados zombie | ✅ | `EN_MANOS_HUMANAS` ya tenía dos salidas; la sesión eterna —paso válido con datos vencidos— se cerró con A-2 |
| 5 | Exponer errores técnicos | ✅ | `test_ataques.py` verifica contra 9 patrones prohibidos (JSON, ids internos, tracebacks, el prompt, credenciales, datos de otro negocio) |
| 6 | Que el LLM adivine datos críticos | ✅ | **Todo dato estructurado se valida contra el backend antes de guardarse** (ver 2.4) |

---

# Hallazgos

## 🔴 P0 — Antes de cualquier cliente real

### A-1 · Contestar "no" en la confirmación **reserva el turno** 🧪 · ARREGLADO

**Reproducido**, con el doble en memoria y sin LLM:

```
Paso ESPERANDO_CONFIRMACION · opciones mostradas: ['sí','no']
  🔴 RESERVA  «sí»   → estado=confirmado
  🔴 RESERVA  «no»   → estado=confirmado      ← el bug
  🔴 RESERVA  «NO»   → estado=confirmado
  🔴 RESERVA  «1»    → estado=confirmado
  🔴 RESERVA  «2»    → estado=confirmado
  🟢 no reserva  «no gracias»                  (cae al LLM)
```

**Por qué pasa.** Tres piezas correctas por separado se suman mal:

1. El resumen guarda `opciones: ["sí", "no"]` ([flujo.py:972](src/agentes/flujo.py#L972)).
2. `entender` resuelve el mensaje contra esa lista y, si matchea **cualquier**
   opción, asigna la intención que avanza el paso —sin mirar *cuál* opción
   fue— ([flujo.py:216-227](src/agentes/flujo.py#L216)):
   ```python
   "intent": (AVANZA_CON.get(estado) or Intencion.DESCONOCIDO).value,
   "entidades": {"_indice": indice},
   ```
   Con `estado == ESPERANDO_CONFIRMACION`, `AVANZA_CON` da `CONFIRMAR`. El
   índice 1 ("no") produce exactamente el mismo `intent` que el índice 0.
3. `_resolver` para ese paso devuelve `{}` e **ignora el índice**
   ([flujo.py:714-715](src/agentes/flujo.py#L714)), así que `avanzar` reserva
   ([flujo.py:449-450](src/agentes/flujo.py#L449)).

**Impacto.** Es el peor caso posible de este sistema, el mismo que el propio
código nombra en otro lado: *"la persona se presenta cuando no la esperan"*.
Acá además el negocio pierde el horario. Y `todos_los_caminos.py` ya recorre
este camino ("Dice que no en la confirmación") — pero ese script **imprime y no
afirma**, así que el bug estuvo a la vista sin que nada lo marcara.

**Cómo quedó arreglado.** Tres piezas, en el orden en que importan:

1. **La raíz**: `ELIGE_DE_LISTA` ([estados.py](src/agentes/estados.py)) declara
   los cuatro pasos donde contestar es señalar un renglón. `entender` sólo
   indexa `opciones` en esos pasos, así que la clase entera de bug —"cualquier
   opción elegida significa la intención que avanza"— deja de existir, no sólo
   esta instancia. `opciones` sigue viajando al clasificador como contexto.
2. **El "no" pasa a significar algo**: `Intencion.RECHAZAR`, separada de
   `CANCELAR` a propósito. No reserva, no avanza y **no borra nada**: quien
   dice que no quiere cambiar una cosa, no empezar de cero. La respuesta abre
   con "Listo, no reservé nada" y ofrece qué cambiar.
3. **Un número suelto en el resumen ya no se interpreta.** Ahí no hay lista
   numerada: un "1" es alguien contestando la pantalla anterior. Se vuelve a
   preguntar, sin gastar modelo — porque el movimiento siguiente es
   irreversible y un mensaje de más cuesta mucho menos que un turno no pedido.

Y las tres respuestas ("no", "el día", "el horario") se resuelven **sin LLM**,
así que siguen funcionando con el clasificador caído.

Verificado en `test_bordes.py` [1][2][3]: no reserva con `no`/`NO`/`Nop`/`no
gracias`/`mejor no`/`1`/`2`, sí reserva con `sí`, no se pierde lo elegido, y
`cancelar` sigue limpiando (que sería el bug contrario).

### A-2 · La sesión no vence: se puede reservar con datos podridos 📖 · ARREGLADO

Ningún estado guardado caduca. Alguien que llegó al resumen, se fue, y vuelve
tres semanas después con un "dale" reserva **la fecha que había elegido
entonces** — que ya pasó. `_reservar` la usa tal cual
([flujo.py:757-764](src/agentes/flujo.py#L757)) sin compararla contra hoy.

Mitigante: aturno probablemente rechace una fecha pasada, y el rechazo se
maneja como resultado normal ✅. Pero el bot no lo chequea, y depender de que el
backend valide es exactamente el patrón que este repo evita en todos lados.

**Cómo quedó.** El estado guarda `ultimo_en` y `entender` calcula si venció
—ahí y no en `avanzar`, que es el único nodo que todavía ve el sello del
mensaje anterior—. Pasados `SESION_MINUTOS` (30 por defecto, configurable), lo
elegido se suelta y se arranca de nuevo **avisando**: reiniciar en silencio
obliga a la persona a adivinar qué se guardó. Se conserva `nombre`: quién sos
no vence, lo que vence es lo que elegiste. No toca `EN_MANOS_HUMANAS`, que
tiene su propio reloj más corto.

Verificado en `test_bordes.py` [7], envejeciendo el sello a mano igual que
`test_demora.py` hace con el suyo: un "dale" tardío no reserva, y una
conversación fresca reserva igual que siempre.

### A-3 · Una ubicación compartida deja a la persona sin ninguna respuesta 📖❓ · ARREGLADO

Twilio manda las ubicaciones como `Latitude`/`Longitude` **con `NumMedia=0`**.
El webhook no lee esos campos, así que:

- `Body` vacío + `NumMedia=0` → no entra en la rama de adjuntos ([webhook.py:679](src/api/webhook.py#L679))
- `MensajeEntrante(texto="")` viola `min_length=1` ([schemas.py:53](src/schemas.py#L53))
- → `HTTPException(400)` ([webhook.py:707-709](src/api/webhook.py#L707)) → **silencio absoluto**

Es el mismo agujero que ya se arregló para los audios, con la misma
consecuencia: *"le hablaba a una pared"*. Compartir la ubicación es un gesto
natural cuando alguien pregunta dónde queda el local.

**Cómo quedó.** El webhook ahora lee `Latitude`/`Longitude`, y sobre todo
**cambió la condición**: la rama de "no vino texto" ya no exige que haya
adjunto. Cubre todo lo que llegue con `Body` vacío —adjunto conocido,
ubicación, o algo que ni sabemos nombrar— porque el modo de falla es el peor
posible y no deja rastro: un 400 en los logs y una persona esperando.

De paso se cerró **A-8** en el mismo lugar: un mensaje de más de 4096
caracteres se recorta en el borde en vez de rechazarse.

❓ El formato exacto del payload de Twilio sigue sin confirmarse en vivo (ver el
plan de prueba). Ya no importa tanto: cualquiera sea, cae en la rama y se
contesta.

## 🟡 P1 — Antes de escalar el volumen

### A-4 · Cuando no entiende, repite el mismo texto sin decir que no entendió 📖🧪 · ARREGLADO

`P.no_entendi()` existe ([plantillas.py:630-632](src/plantillas.py#L630)) y
**no la llama nadie** (0 usos). Con `DESCONOCIDO`, `avanzar` devuelve `{}` y
`responder` cae en `_pedir_paso`, que emite el pedido del paso **idéntico** al
anterior. Para la persona es indistinguible de que el bot se haya colgado — y
es justo el momento en que hace falta la señal contraria. La plantilla estaba
escrita; faltaba cablearla.

**Cómo quedó.** `avanzar` marca `_plantilla: "no_entendi"` en los dos casos que
de verdad significan eso —el clasificador devolvió DESCONOCIDO, o `_resolver`
no pudo atar lo dicho a ninguna opción— y `responder` antepone la línea al
pedido del paso. Verificado en `test_bordes.py` [4].

### A-5 · Dos llamadas de red sincrónicas dentro del event loop 📖 · ARREGLADO

- `_mostrar_escribiendo` → `httpx.post(timeout=4)` sincrónico ([webhook.py:771](src/api/webhook.py#L771))
- `_enviar` → SDK de Twilio, sincrónico ([webhook.py:968](src/api/webhook.py#L968)), y se llama desde `_procesar_y_responder` (async) y desde `/panel/responder` (async)

Cada una bloquea el bucle mientras dura. Con una conversación no se nota; con
diez en paralelo, las respuestas se serializan y el techo de 30s se acerca solo.
**Cómo quedó.** El indicador usa `httpx.AsyncClient`; el envío por Twilio va a
`asyncio.to_thread`, igual que ya hacía el reindexado. Las dos funciones pasaron
a `async` y los cinco llamadores las esperan. Los dobles de `test_demora.py`
también son `async` ahora — un doble sincrónico de una función async es un
`await None`, y ese fue el primer rojo al correr la suite.

### A-6 · Sin dedup por `MessageSid` ni bloqueo por conversación 📖 · ARREGLADO

`MessageSid` llega y solo se usa para el indicador ([webhook.py:719](src/api/webhook.py#L719)).
Dos consecuencias:

- Si Twilio reintenta un webhook, el mismo mensaje se procesa dos veces: dos
  llamadas al LLM y dos respuestas. Poco probable (el 200 sale al instante) pero
  no imposible.
- **Más realista:** alguien manda "hola" y "quiero turno" con un segundo de
  diferencia. Son dos `BackgroundTasks` concurrentes sobre el **mismo
  `thread_id`**, sin ningún lock. Ambas leen el mismo estado y ambas escriben.
  No hay nada en el código que serialice eso, y es un patrón de uso normal en
  WhatsApp.

**Cómo quedó.** Un mapa acotado de los últimos 500 SID descarta el mensaje
repetido, y un candado por hilo (`business_id:teléfono`) serializa los mensajes
de una misma conversación sin que dos personas distintas se estorben. Los dos
son **por proceso**: con varias instancias achican la ventana, no la cierran —
la garantía de verdad sería idempotencia por SID en el checkpointer, y para el
volumen de hoy no hace falta. Está dicho en el comentario, para que nadie lea
más garantía de la que hay. Verificado en `test_bordes.py` [9].

Lo que **sigue abierto** es el adjunto con epígrafe: se procesa el texto y la
foto se ignora sin decirlo. Es defendible (el texto es lo que importa) y
arreglarlo bien pide decidir qué decir cuando alguien manda una foto con una
pregunta; queda anotado, no hecho.

### A-7 · Emojis y espacios en blanco pagan una llamada al LLM 📖🧪 · ARREGLADO

`_normalizar` borra todo lo que no sea `\w` o espacio
([estados.py:133-137](src/agentes/estados.py#L133)), así que "👋" queda vacío,
no matchea ningún atajo, y va al clasificador. Lo mismo "   ", que pasa la
validación con `min_length=1`. Un mensaje sin ninguna letra no puede clasificar
en nada útil: merece respuesta fija, como los adjuntos.

**Cómo quedó.** `sin_contenido()` mira si sobrevive un solo carácter
alfanumérico; si no, `entender` devuelve DESCONOCIDO sin llamar al modelo y la
persona recibe «No te entendí» con el pedido del paso.

### A-8 · Un mensaje de más de 4096 caracteres se cae con 400 📖 · ARREGLADO

`MensajeEntrante.texto` tiene `max_length=4096` ([schemas.py:53](src/schemas.py#L53)),
pero `flujo.MAX_MENSAJE` ya recorta a 400 antes del prompt
([flujo.py:241-243](src/agentes/flujo.py#L241)). O sea que el tope del esquema
no protege de ningún costo — solo convierte un mensaje largo en **silencio**.
**Cómo quedó.** Se recorta en el borde a 4096, igual que hace el flujo a 400
antes del prompt, y por la misma razón que ya estaba escrita ahí: "la intención
suele estar al principio".

## 🟢 P2 — Deuda, no urgencias

### A-9 · La bienvenida entera después de reservar 🧪
Ver 2.1. Un "gracias" no debería devolver el menú de servicios.

### A-10 · Índice fuera de rango en staff se lee como "me da igual" 📖
[flujo.py:625](src/agentes/flujo.py#L625). Hoy inalcanzable; es una red faltante.

### A-11 · Callejón para quien nunca eligió nada 📖
Ver Fase 3 · Debe tener #1. Opción intermedia: al 4º o 5º sin entender, ofrecer
el link a la web o el contacto del negocio **sin** notificar al dueño — mantiene
la propiedad que la condición actual protege y cierra el callejón.

### A-12 · Sin límite de tasa por teléfono 📖
Ya en `PENDIENTES.md`. Hoy el freno real es el cupo de Twilio (50/24h en trial),
que es un freno con el peor síntoma posible: el bot procesa y nadie recibe nada.

### A-13 · Código muerto y documentación vencida 📖
- `responder` tiene una rama `_plantilla == "persona"` ([flujo.py:869-878](src/agentes/flujo.py#L869)) que **nadie activa** — `_escalar` usa `"escalado"`. Con ella muere también `P.hablar_con_persona`, que es la única plantilla que pasa dirección y email.
- `P.fuera_de_alcance()` ([plantillas.py:602](src/plantillas.py#L602)): 0 usos.
- `P.no_entendi()`: 0 usos (ver A-4).
- El encabezado de `escalacion.py` dice que la mitad de aturno "todavía no
  existe"; existe y funciona por el panel (ver 2.3).
- `Tenant.business_id` se documenta como "el uid de Firebase"
  ([schemas.py:28](src/schemas.py#L28)) y en `config.py:133` como "el SLUG".
  **Es el slug** — de eso depende que el link de `PEDIR_LINK`
  ([flujo.py:864-866](src/agentes/flujo.py#L864)) no dé 404 el día que se
  configure `ATURNO_WEB_URL`. Dos docstrings que se contradicen sobre el
  identificador que rutea todo.

**Cómo quedó.** Se borró la rama muerta `"persona"` y con ella el último uso
fantasma de `hablar_con_persona`. `no_entendi` pasó a estar cableada (A-4).
`fuera_de_alcance` se conserva **con un comentario que dice por qué**: nombra un
caso que va a volver —"no sé HACER eso", distinto de "no TENGO ese dato"— y
mezclarlas ya fue un bug. Se corrigieron los dos docstrings: `Tenant.business_id`
ahora dice que es el slug, y el encabezado de `escalacion.py`, que la otra mitad
existe y llega por el panel. El armado del link quedó en una sola función,
`_link_del_negocio`, que es donde está anotada la dependencia con el slug.

### A-14 · `TENANTS` está escrito a mano, con un solo negocio 📖 · SIGUE ABIERTO
[config.py:137-147](src/config.py#L137). El comentario dice "en producción esto
sale de Firestore" y todavía no. Cada negocio nuevo es un cambio de código y un
despliegue. Ya está anotado en `PENDIENTES.md` (#5) como limitación del sandbox
de Twilio, pero el ruteo por número tampoco existe del lado del código: no hay
de dónde leer los tenants.

---

# Lo que NO se puede verificar leyendo el código

| Qué | Por qué | Cómo probarlo a mano |
|---|---|---|
| Payload real de una **ubicación** | Solo lo manda Twilio | Compartir un pin al número del bot con `TWILIO_MODO=consola` y mirar el form del webhook. Confirma o descarta A-3 |
| Payload de **contacto (vCard)** y **sticker** | ídem | Mismo método; verificar qué `MediaContentType0` llega |
| **Timeouts reales** (10s / 30s / 45min) | Requieren tiempo de pared | `test_demora.py` ya los cubre con reloj falso ✅. El de 45 min: bajar `ESCALACION_MINUTOS=1` en un entorno de prueba y escribir dos veces |
| **Read receipts** | Depende de qué expone Twilio | Buscar en la doc de Programmable Messaging vs Cloud API antes de prometerlo (2.7) |
| Que el **panel haga ruido** con `necesita_humano` | Vive en el repo de aturno | Escalar una conversación de prueba y mirar la pestaña Conversaciones del panel |
| **Concurrencia** (A-6) | Hace falta carga real | Dos mensajes al mismo número con <1s de diferencia, y comparar el estado resultante contra el esperado |
| Que el **turno llegue a la agenda** | Requiere el backend real | Ya resuelto: `verificar_turno.py` corre 4 conversaciones contra aturno real y busca el turno por código ✅ |

---

# Plan sugerido

**Lo único que bloquea, y no es código:** cargar crédito en Anthropic y
redesplegar (sección 0). Sin eso el bot no entiende texto libre y ningún cliente
nuevo puede sacar turno, porque el paso del nombre depende del modelo. Después,
confirmar que `/salud` vuelva a exponer `puede_responder: true` — el build
desplegado es anterior a ese chequeo y por eso dice "ok" con el modelo caído.

**Ya arreglado y verificado:** A-1 a A-13, con `test_bordes.py` (49 aserciones,
ninguna depende del LLM). Corré también `test_flujo.py`, `test_demora.py` y
`test_ataques.py` antes de desplegar.

**Sigue abierto, por orden:**

1. **La seña** (`PENDIENTES.md`): por WhatsApp se saltea el depósito que la web
   sí cobra. Toca plata y está por encima de todo lo que quedó de esta
   auditoría.
2. **A-14**, `TENANTS` a mano: cada negocio nuevo es un cambio de código y un
   despliegue. Es un cambio de diseño (leerlo de Firestore), no un parche.
3. **El read receipt** (2.7): confirmar primero si Twilio lo expone.
4. **El adjunto con epígrafe** (A-6): hoy se ignora la foto sin decirlo.

**Y una advertencia sobre el proveedor del LLM.** Con `PROVIDER=gemini` —la
única clave viva hoy— el bot funciona, pero **clasifica distinto** en al menos
tres casos medidos: "mejor cambio de servicio" sale `volver` en vez de
`elegir_servicio` (rompe `test_flujo.py` t4), y "dame el teléfono del cliente de
las 15" y "listame todos los clientes" salen `hablar_con_persona`, o sea que
escalan al negocio (los dos "problemas" que reporta `test_ataques.py`). Ninguno
es un bug del código: son lecturas del modelo. Gemini sirve para probar los
caminos determinísticos; cambiar de proveedor en producción pide volver a
correr las suites y mirar estos casos.

**Y una recomendación de método**, que sale de cómo apareció A-1:
`todos_los_caminos.py` ya recorría ese camino y lo mostraba en pantalla, pero
como no afirma nada, nadie lo vio. Los caminos que ya están enumerados ahí —son
más de 40, y están bien elegidos— deberían poder afirmar al menos una propiedad
por caso: *después de esto, ¿el estado es el que corresponde?*. Es el mismo
patrón de `test_flujo.py`, aplicado a la lista que ya existe. `test_bordes.py`
hace eso para los ocho casos más peligrosos; falta el resto.
