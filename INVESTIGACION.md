# Qué dice la investigación sobre chatbots, y qué le falta a éste

Agosto 2026. Búsqueda de literatura (CHI, ACM, MIS Quarterly, Nature Scientific
Reports, Journal of Marketing) y de datos de industria 2026, contrastada contra
el código de este repo.

Dos preguntas:

1. Cuando alguien dice "un buen chatbot", **¿qué se está midiendo?**
2. **¿Qué es lo que la gente odia?** — porque hay mucha más data sobre eso, y es
   más accionable.

Las fuentes están al final, con link.

**Cómo leer los números.** Los marco por procedencia, porque no valen lo mismo:

| Marca | Qué es |
|---|---|
| 🔬 | Estudio revisado por pares, con N y método |
| 📊 | Analista o encuesta grande (Gartner, Forrester) |
| 📰 | Dato de industria — direccional, no citable ante un cliente |

---

# Parte 1 · Lo que hay que hacer

### 1. La arquitectura híbrida es el consenso, no una opinión

> Los chatbots puramente LLM son impredecibles y las máquinas de estados puras
> son rígidas. Los mejores sistemas combinan las dos: la máquina de estados da
> confiabilidad y barandas, el LLM entiende dentro de límites definidos.

La formulación operativa que más se repite: **el LLM para entender, lógica
determinística para las transiciones y las reglas de negocio.** Salida
estructurada con esquema JSON, validada estricto antes de actuar sobre ella.

Este repo ya hace eso y va **más lejos que el consenso**: acá el LLM ni siquiera
redacta. Clasifica a un enum cerrado y las plantillas escriben. Eso hace
estructuralmente imposible la alucinación en el texto que lee una persona — no
la evita un prompt, la evita el diseño.

Es, sin haberlo buscado, la máxima de calidad de Grice ("no digas aquello para
lo que no tenés evidencia suficiente") impuesta por arquitectura.

### 2. La calidad conversacional pesa más que parecer humano

🔬 La *performance* conversacional pesa más que la humanidad para la confianza.
Coherencia, contexto y naturalidad predicen satisfacción y lealtad; parecer
humano, mucho menos.

Y el reverso —que parecer humano se cobra caro cuando el bot falla— está en la
Parte 2, porque es de lo que más data hay.

### 3. El fallo se diseña. Y "repetir" es la peor estrategia

🔬 **Ashktorab, Jain, Liao y Weisz — CHI 2019**, N=203. Compararon ocho
estrategias de reparación cuando el bot no entiende:

| Estrategia | Qué hace |
|---|---|
| **defer** | pasa a una persona |
| **options** | ofrece opciones concretas |
| **repeat** | vuelve a pedir lo mismo |
| **confirmation** | "¿quisiste decir X?" |
| **out-of-vocabulary explanation** | "esta palabra no la conozco" |
| **keyword confirmation explanation** | "entendí *jueves*, ¿es eso?" |
| **keyword highlight explanation** | marca qué parte sí entendió |

> **Ganaron las opciones y las explicaciones**, porque manifiestan iniciativa
> del bot y son accionables para salir del pozo. **Repeat quedó abajo.**

🔬 Corroborado después: ofrecer sugerencias de qué se pudo haber querido decir
mejora tanto la precisión percibida como la simpatía del bot.

📰 El número que le pone precio: una buena estrategia de fallback **recupera el
74%** de las conversaciones que iban a fracasar.

📰 Distribución de errores en producción:

| Error | Frecuencia |
|---|---|
| clasificación errónea de intención | 42% |
| fallas de parseo de la entrada | 23% |
| pérdida de contexto | 18% |
| timeouts de API | 12% |

Los tres primeros son, en ese orden exacto, los tres bugs que aparecieron en
este repo esta semana.

### 4. Se mide la tarea terminada, no la conversación

> Un chatbot que tarda 12 turnos en sacar un turno está fallando, aunque la
> conversación parezca natural.

📰 Benchmarks 2026:

| Métrica | Qué es | Referencia |
|---|---|---|
| **Containment rate** | % resueltas sin humano | 40–65% general · **65–85% tier-1**, donde entra sacar un turno |
| **Escalation rate** | % que pasan a una persona | 30–50% general · **<25% si el alcance es angosto** |
| **Goal completion rate** | % que terminan la tarea | se mide contra uno mismo |
| **CSAT** | satisfacción solo-bot | 75–85% positivo |
| **Turnos hasta la meta** | cuántos mensajes hacen falta | menos es mejor, siempre |
| **Costo por conversación** | | ya medido en este repo |

### 5. La parte probabilística necesita un conjunto dorado

Consenso completo, sin voces en contra:

- **Conjunto dorado** — entradas con salida esperada, curadas a mano.
- **Regresión en cada cambio**, idealmente en CI: si el puntaje baja, no sale.
- 🔬 **Juez-LLM** para lo cualitativo. Un modelo fuerte como juez coincide con
  expertos humanos **>80%**, el mismo nivel de acuerdo que tienen los humanos
  entre sí.
- **Revisión humana de una muestra**, siempre.
- Evaluación **online** además de offline: el comportamiento real revela lo que
  ningún set de prueba anticipa.

La limitación que la propia literatura señala: en conversaciones multi-turno no
se pueden anticipar todas las interacciones. El conjunto dorado cubre lo
conocido, no lo desconocido.

### 6. WhatsApp es su propio medio

- **Mensajes cortos.** Un párrafo largo en mensajería es una pared que nadie
  termina de leer.
- **Opciones predefinidas** en vez de texto libre cuando se puede.
- **La latencia casi no importa.** WhatsApp es asincrónico por cultura: se
  escribe a un negocio esperando respuesta en una hora o más. No hay que
  optimizar velocidad — hay que optimizar aciertos.

---

# Parte 2 · Lo que la gente odia

Esta es la parte con más datos duros, y la que más conviene tener a mano.

## El tamaño del problema

| Dato | |
|---|---|
| 📰 **77%** de los adultos dice que los chatbots de atención son frustrantes | |
| 📊 Forrester: **6,4/10** promedio de experiencia; **50%** reporta frustración frecuente | |
| 📰 **67%** usó un chatbot en los últimos 12 meses… pero **54%** preferiría esperar a un humano | |
| 📰 **~40%** de las interacciones con chatbots se perciben como negativas | |
| 📰 CSAT solo-bot: **28%**, contra **82%** con un agente humano | ⚠️ dato de blog, no verificado en fuente primaria — direccional |

**El punto de partida es hostil.** La persona que le escribe a tu bot ya viene
con una mala experiencia previa y esperando otra. Eso no se arregla siendo
simpático; se arregla resolviendo rápido y no cometiendo los pecados de abajo.

## El ranking de quejas

Dos encuestas distintas, ordenadas cada una a su manera. Coinciden en el podio:

**Por qué la gente pide escalar:**

| # | Motivo | |
|---|---|---|
| 1 | No comprende lo que le dicen | 📰 35% |
| 2 | No poder escalar a un humano | 📰 22% |
| 3 | Tarda demasiado | 📰 9% |

📰 Y **73%** se frustra cuando el bot no entiende su pregunta.

**Top de frustraciones declaradas:**

| # | Motivo | |
|---|---|---|
| 1 | No resuelve el problema | 📰 43% |
| 2 | Quedar atrapado en un bucle | 📰 38% |
| 3 | Tener que repetir información | 📰 37% |

## Lo que cuesta

| 📰 | Dato |
|---|---|
| **30%** | abandona una marca tras una mala experiencia con su chatbot |
| **38%** | abandonó una compra por una mala experiencia con un chatbot |
| **>50%** | se cambia a la competencia después de **una sola** mala experiencia |
| **73%** | se cambia después de varias |
| **30%** | se va a la competencia **y se lo cuenta a todos** |

Y el efecto de arrastre: la gente lee un mal chatbot como señal de mal servicio
en general. No falla una vez — daña la relación para las veces siguientes.

---

## Los ocho pecados, con su evidencia

### Pecado 1 · No dejar llegar a un humano

El más grave y el mejor documentado.

- 📊 **87%** de los clientes dice que es **esencial** que una empresa que usa
  GenAI ofrezca la opción de hablar con una persona. *(Gartner, agosto 2026)*
- 📰 La mala escalación explica **más del 65%** del abandono de chatbots.
- 📰 70–80% prefiere, en general, tratar con un humano.

> Los clientes atrapados en el "bucle del chatbot" sin poder llegar a un humano
> no quedan frustrados: quedan **enojados**.

Y no alcanza con que la puerta exista: tiene que estar **visible**. La
literatura de diseño es explícita en que esconder la salida en letra chica al
pie del widget no cuenta.

### Pecado 2 · El bucle

📰 38% de las quejas.

> Sin opciones de salir, escalar o volver a entrar por otra rama, el chat se
> siente como una **trampa**, y la mayoría lo abandona.

El patrón concreto que describen: el bot repite respuestas enlatadas en vez de
reconocer que hay que escalar.

**Este repo tuvo uno esta semana** —el bucle de "De nada"— y era del tipo peor:
el mensaje invitaba a escribir y contestaba lo mismo a lo que escribías.

### Pecado 3 · Hacer que la gente se repita

El dato más filoso de toda la búsqueda:

- 📰 **60%** se repite **una sola vez** antes de abandonar.
- 📰 **11%** se va **la primera vez** que se le pide repetir algo.

Y en el momento de pasar a un humano:

- 📰 **70%** dice que es muy o extremadamente importante que el humano **ya
  sepa el contexto**.
- 📰 Los que tienen que repetir en la escalación califican la experiencia
  **76% peor** y consumen bastante más tiempo del agente.

O sea: el traspaso al humano es un momento tan peligroso como el fallo mismo.

### Pecado 4 · No entender

📰 35% de los pedidos de escalar y 73% de la frustración declarada. Es también
el 42% de los errores técnicos.

Es el pecado que no se puede eliminar — se puede reducir, y sobre todo **se
puede manejar bien cuando pasa**. De ahí que la Parte 1 · punto 3 sea la más
accionable de todo el documento.

### Pecado 5 · La empatía falsa

El hallazgo más contraintuitivo, y el que más gente hace mal.

🔬 **Universidad del Sur de Florida, publicado en MIS Quarterly** — tres
experimentos, incluido uno con un chatbot LLM real. Midieron qué pasa cuando el
bot reconoce y refleja las emociones negativas del usuario.

> Los mensajes empáticos del chatbot dispararon **reactancia psicológica** — la
> respuesta negativa que aparece cuando alguien siente que le invaden el
> control o los límites. Resultado: **menor percepción de competencia, de
> calidad de servicio y de satisfacción.**

> *"La empatía de un chatbot puede sentirse intrusiva y minar la confianza."*
> — Dezhi Yin, coautor

El contraste es el punto: **con una persona, la empatía funciona.** Escuchar
"comparto tu frustración" de un empleado calma y reconstruye confianza. Del bot,
hace lo contrario.

Y en la misma línea:

- 🔬 Con un cliente **enojado**, el antropomorfismo baja la satisfacción, la
  evaluación de la empresa y la intención de compra. Con uno tranquilo, ese
  efecto no aparece. *(Crolic et al., Journal of Marketing)*
- 🔬 El antropomorfismo **infla la expectativa previa**; cuando el bot no
  cumple, la reacción se describe como traición, no como frustración.
- 📰 Los usuarios describen el elogio constante de la IA como falso,
  manipulador o directamente nauseabundo. Textual de un participante: *"todo el
  mimetismo de empatía es molesto"*; de otro: *"se esfuerza demasiado"*.

**Las frases prohibidas:** "te entiendo", "me imagino cómo te sentís",
"lamento mucho que…", "comparto tu frustración". Son empatía deceptiva:
humanizan algo que no es humano.

### Pecado 6 · Fingir ser humano, o prometer de más

El Center for Democracy & Technology catalogó **37 patrones oscuros** en
chatbots, en cinco categorías. Los que aplican a un bot de negocio:

- **No decir que es un bot.** Confunde la percepción de con qué se está
  hablando.
- **Sobreestimar sus capacidades.** Prometer algo que después no puede hacer.
- **Privacy zuckering.** Sacar más datos de los que la persona quería dar, bajo
  una fachada de "ayuda".

🔬 Un matiz importante de la literatura: **los patrones oscuros de un chatbot no
requieren mala intención del diseñador** — emergen del comportamiento del
sistema. Se llama "engaño banal": está incorporado en cómo funciona la
tecnología, no en una decisión de alguien.

Traducido: se puede cometer sin querer. Por eso conviene revisarlo a propósito.

### Pecado 7 · Pedir más datos de los necesarios

- 📰 **73%** se preocupa por la privacidad de sus datos al usar un chatbot.
- 📰 **81%** cree que las empresas que usan IA recolectan **más datos de los
  necesarios**, y que podrían usarse mal.

La "sobre-consulta de información personal o sensible" aparece listada como
causa directa de fallo de chatbots.

### Pecado 8 · Demasiadas preguntas antes de mostrar algo

Específico de bots de turnos, y por eso el más relevante acá:

> Si tu chatbot hace diez preguntas antes de que el cliente vea un solo horario
> disponible, lo perdés.

📰 La receta que da la literatura de booking:

1. **Adelantar la pregunta más importante** — qué servicio.
2. **Mostrar disponibilidad lo antes posible.**
3. **Pedir el resto de los datos después** de que eligió el horario.

Y medir el abandono **por paso**: si el 35% se cae después de ver los horarios,
el problema es la disponibilidad o el precio, no el bot.

---

## El dato estratégico que cambia el encuadre

📊 **Gartner, encuesta a 3.566 clientes B2B y B2C, febrero–marzo 2026:**

- Los clientes son **~3 veces más propensos** a usar una GenAI de terceros
  (ChatGPT, Gemini, Copilot) que el chatbot de la propia empresa.
- Los líderes de servicio invirtieron una mediana del **12%** de su presupuesto
  2025 en IA —lo más alto entre 10 funciones de negocio— y solo el **24%**
  mostró **retorno financiero positivo**.

> *"El impacto decepcionante de las inversiones en GenAI de cara al cliente
> tiene menos que ver con las limitaciones de la tecnología y más con la
> desalineación con las expectativas del cliente."*

**Por qué esto importa acá.** El fracaso masivo es de los bots de propósito
general: los que intentan ser un ChatGPT con el logo de la empresa. Contra ésos,
la gente prefiere el ChatGPT de verdad.

Un bot **angosto y transaccional** —saca turnos, contesta preguntas del
negocio— no compite en esa cancha. Compite contra "nadie te contesta el
WhatsApp hasta mañana", que es una vara mucho más baja. Y es la categoría donde
los benchmarks son favorables: <25% de escalación, 65–85% de containment.

Es el argumento de venta, y está respaldado.

---

# Parte 3 · Este bot contra la lista

### Los ocho pecados, auditados

| Pecado | ¿Lo comete? | Evidencia en el código |
|---|---|---|
| **1 · No dejar llegar a un humano** | ✅ No | «una persona» nombrada en la apertura, en el error técnico, en `atascado`, en la confirmación. Y la escalación se ofrece **sola** a los 2 fallos, sin que se le ocurra pedirla |
| **2 · El bucle** | ⚠️ Tuvo uno | El de "De nada", arreglado esta semana. `LIMITE_ATASCADO = 4` existe justo para esto |
| **3 · Hacer repetir** | ✅ No | El checkpointer de Postgres guarda todo entre mensajes. Y el puente con el panel manda cada mensaje al negocio, así que el humano llega con contexto |
| **4 · No entender** | ⚠️ Sí, como todos | Y lo maneja con la estrategia peor puntuada — **es el Hueco 1** |
| **5 · La empatía falsa** | ✅ **No, ni una línea** | Cero "te entiendo", cero "lamento". El único "perdón" de todo `plantillas.py` es por haber usado mal un nombre: una disculpa por un hecho, no emocional |
| **6 · Fingir ser humano** | ✅ No | *"Soy el asistente de X"* en el primer mensaje. Y hay un comentario explícito prohibiendo prometer cancelaciones que el bot no puede hacer |
| **7 · Pedir datos de más** | ✅ No | Pide **un** dato: el nombre. Nada más |
| **8 · Muchas preguntas antes de mostrar** | ✅ Casi no | El orden es servicio → (profesional) → día → horario → nombre → confirmar. `_pasos_a_saltear` **saltea al profesional** si hay uno solo, y el nombre va **después** del horario, que es exactamente la receta |

**Siete de ocho, limpio.** Y el pecado 5 —el que más gente comete y el que
tiene el estudio más contundente en contra— acá no existe, por una decisión de
diseño que se tomó por otro motivo: que el LLM no redacte.

Eso no es suerte. Es lo que pasa cuando el texto sale de plantillas escritas por
una persona en vez de generarse: nadie escribe "comparto tu frustración" a mano.

### Dónde ya está bien — y no hay que tocar

| Hallazgo | Cómo ya se cumple |
|---|---|
| LLM entiende, código decide | El grafo de tres nodos + la tabla `ORDEN` |
| Nunca decir lo que no se sabe | Plantillas fijas, enum cerrado, RAG con umbral |
| Salida estructurada y validada | El `ESQUEMA` plano + `_a_clasificacion()` |
| No sobre-antropomorfizar | Sin emojis, sin personalidad, sin empatía fingida |
| Mensajes cortos, listas verticales | Regla explícita en el `CLAUDE.md` |
| Fallar con gracia | `atascado` + escalación a una persona |
| Opciones accionables | En los pasos de lista, el CTA **es** la lista |
| Adelantar lo importante, pedir datos al final | El nombre va después del horario |
| Costo por conversación medido | `src/gasto.py` + el endpoint `/gasto` |

### Los cuatro huecos reales

---

#### Hueco 1 · El primer escalón de la reparación es "repetir"

**Qué pasa hoy.** La escalera de fallo es: *repetir → repetir → persona*
(`LIMITE_SIN_ENTENDER = 2`, `LIMITE_ATASCADO = 4`, en `src/agentes/flujo.py`).

El escalón que la investigación puntúa **mejor** —decir qué sí se entendió— **no
existe**, y el que puntúa **peor** es el que se usa primero.

En los pasos de lista casi no se nota, porque repetir el CTA equivale a mostrar
opciones. Se nota en los de texto libre: el nombre, el día, las preguntas. Ahí
`no_entendi()` dice *"No te entendí"* y repite el pedido idéntico, sin una sola
palabra de lo que sí llegó.

**Qué falta.** Un escalón nuevo antes de escalar, con las dos estrategias
ganadoras juntas — explicación + opciones:

```
Entendí que querés algo para el jueves, pero no qué servicio.
Decime el número:
1. Corte de pelo …
```

El clasificador **ya devuelve entidades parciales**; hoy se descartan cuando no
alcanzan para avanzar. Es material que ya está pago y se tira.

Toca `src/plantillas.py` y una rama en `avanzar`. No toca la máquina de estados.

---

#### Hueco 2 · No existe ni un solo número de resultado

**Qué pasa hoy.** Hay trazas de Phoenix (`src/observabilidad.py`) y costo por
turno (`src/gasto.py` + `/gasto`). Pero **nada cuenta cuántas conversaciones
terminan en un turno reservado**, ni cuántas se abandonan, ni en qué paso.

Y a la luz de la Parte 2, falta lo más importante: **no hay forma de saber si
alguien se fue enojado.** El 30% que abandona una marca por un chatbot no
escribe para avisarlo — desaparece.

**Qué falta.** Los cinco números de la tabla del punto 4, más **abandono por
paso**, expuestos en `/metricas` con el mismo patrón que ya usa `/gasto`. El
checkpointer ya guarda el estado final de cada conversación — es leer, no
instrumentar nada nuevo.

Y cierra el trabajo de precio que quedó a medias:

```
costo por conversación (ya medido) ÷ containment (falta) = costo real por turno resuelto
```

---

#### Hueco 3 · Lo único probabilístico es lo único sin prueba

**Qué pasa hoy.** `test_bordes.py` corre **sin LLM a propósito**, y esa decisión
es correcta: hace que un rojo signifique siempre un bug del código.

El efecto lateral es que **el clasificador —la única pieza probabilística— no
tiene ninguna prueba.** Los tres bugs de esta semana fueron los tres suyos:

| Bug | Qué pasó |
|---|---|
| el nombre de la madre | `"no soy Milagros"` caía en `desconocido` |
| `"quiero otro turno"` | el modelo lo generalizaba a `ver_mas` |
| el bucle de `"De nada"` | `"hola"` y `"gracias"` comparten intención |

Los tres los encontró una persona probando a mano.

**Qué falta.** Un `casos.jsonl` con `(mensaje, estado, intención esperada)` y un
`test_clasificador.py` que corra **a pedido**, no en cada cambio, porque cuesta
plata: ~100 casos ≈ US$ 0,002 con Haiku. Se siembra con lo que ya está escrito
—las 22 formas de negar el nombre del test 19— y crece con cada bug.

Es el único de los cuatro que **evita** bugs en lugar de arreglarlos.

---

#### Hueco 4 · La apertura ya está bien, pero la salida podría estar en más lugares

**Qué pasa hoy.** Revisando contra la Parte 2, la apertura está mejor de lo que
pensaba: dice que es un asistente, invita a preguntar *"las veces que
necesites"*, y nombra la salida a una persona en su propio bloque al final.
Cumple con el pecado 1 y con el 6.

**Lo que queda.** El dato de Gartner es que la salida tiene que estar
**disponible**, no sólo mencionada una vez al principio. Vale revisar en qué
pasos intermedios está nombrada y en cuáles no — sobre todo en los que la
persona ya viene de un fallo, que es cuando la busca.

Es una revisión, no una feature.

---

# Parte 4 · Lo que la investigación dice que NO hay que hacer

- **No poner empatía.** Ni "te entiendo", ni "lamento", ni "me imagino".
  Dispara reactancia psicológica y baja la percepción de competencia. Es el
  hallazgo mejor respaldado de la Parte 2 y el que más gente ignora.
- **No hacerlo más "humano".** Emojis, nombre propio, personalidad, small talk.
  No compra confianza, y con alguien enojado se cobra doble.
- **No dejar que el LLM redacte lo que lee la persona.** Es la decisión más
  fuerte de este repo y es la que hace imposibles los pecados 5 y 6.
- **No esconder la salida a un humano.** 87% dice que es esencial.
- **No optimizar latencia.** WhatsApp es asincrónico; nadie mira el reloj.
- **No medir engagement.** Conversaciones largas y "lindas" son señal falsa.
- **No pedir un dato que no se necesite.**

---

# Parte 5 · Orden sugerido si se ataca

| # | Hueco | Por qué en ese lugar |
|---|---|---|
| 1 | Hueco 1 — la reparación | El pecado 4 es el más frecuente y hoy se maneja con la estrategia peor puntuada |
| 2 | Hueco 2 — las métricas | El único que dice si algo de esto funciona. Y el que hace falta para vender |
| 3 | Hueco 4 — la salida en más pasos | Revisión barata sobre el pecado más grave |
| 4 | Hueco 3 — el conjunto dorado | El que evita el próximo bug |

Los cuatro con el test primero, como se viene trabajando.

**Verificación:**

```bash
python test_bordes.py          # casos nuevos por hueco
python test_flujo.py           # que los invariantes no se muevan
python todos_los_caminos.py    # tiene que seguir en 0 cosas para mirar
python chatear.py              # y leerlo como cliente
curl localhost:8000/metricas   # los cinco números, tras el hueco 2
```

---

# Fuentes

**Reparación y fallo** 🔬
- [Resilient Chatbots: Repair Strategy Preferences for Conversational Breakdowns — CHI 2019](https://dl.acm.org/doi/10.1145/3290605.3300484)
- [Troubleshooting Conversations: Exploring Chatbot Repair Strategies — Mensch und Computer 2024](https://dl.acm.org/doi/fullHtml/10.1145/3670653.3677496)
- [System and User Strategies to Repair Conversational Breakdowns — ACM](https://dl.acm.org/doi/fullHtml/10.1145/3640794.3665558)
- [When chatbots fail: exploring user coping following a chatbot-induced service failure — Information Technology & People](https://www.emerald.com/itp/article/37/8/175/1214038/When-chatbots-fail-exploring-user-coping-following)

**Empatía y antropomorfismo** 🔬
- [Chatbot Empathy in Customer Service: When It Works and When It Backfires — MIS Quarterly / USF](https://www.usf.edu/business/news/2026/04-20-chatbot-empathy-can-worsen-customer-reactions.aspx)
- [Blame the Bot: Anthropomorphism and Anger in Customer–Chatbot Interactions — Journal of Marketing](https://journals.sagepub.com/doi/full/10.1177/00222429211045687)
- [When bots' empathic expressions backfire — Electronic Markets](https://link.springer.com/article/10.1007/s12525-025-00814-7)
- [The effect of anthropomorphic design when the chatbot service fails — ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S026840122400015X)
- [Building user trust in AI chatbots through human-like cues and perceived reliability — Nature Scientific Reports](https://www.nature.com/articles/s41598-026-38179-2)
- [Effects of Humanlikeness and Conversational Breakdown on Trust in Chatbots — ACM](https://dl.acm.org/doi/fullHtml/10.1145/3546155.3546665)

**Patrones oscuros** 🔬
- [Dark Patterns in AI Chatbots: A Taxonomy to Inform Better Design — Center for Democracy & Technology](https://cdt.org/insights/dark-patterns-in-ai-chatbots-a-taxonomy-to-inform-better-design/)
- [In Search of Dark Patterns in Chatbots — ACM](https://dl.acm.org/doi/10.1007/978-3-031-54975-5_7)
- [Exploring the "Banality" of Deception in Generative AI](https://arxiv.org/pdf/2605.07012)

**Frustración y abandono** 📊 📰
- [Gartner: 87% de los clientes exige acceso a un humano cuando se usa GenAI (agosto 2026)](https://www.gartner.com/en/newsroom/press-releases/2026-08-04-gartner-survey-finds-87-percent-of-customers-say-companies-using-genai-for-customer-service-must-provide-access-to-a-human-agent0)
- [Gartner: los clientes son 3x más propensos a usar GenAI de terceros que el chatbot de la empresa (julio 2026)](https://www.gartner.com/en/newsroom/press-releases/2026-07-08-gartner-survey-finds-customers-are-three-times-more-likely-to-use-third-party-genai-than-company-provided-chatbots-for-customer-service)
- [Chatbot Frustration is Real: Hidden Costs and Best Practices — California Management Review (Berkeley)](https://cmr.berkeley.edu/2026/04/chatbot-frustration-is-real-hidden-costs-and-best-practices/)
- [Customers are losing patience with automated customer support bots — CX Dive](https://www.customerexperiencedive.com/news/customers-losing-patience-automated-customer-support/823434/)
- [Customers Hate Your AI Chatbot. Small Businesses Should Listen — Forbes](https://www.forbes.com/sites/terdawn-deboe/2026/04/20/customers-hate-your-ai-chatbot-small-businesses-should-listen/)
- [What Causes Chatbot Drop-Off and How to Fix It — Velaro](https://velaro.com/blog/chatbot-abandonment-reasons-and-solutions)
- [Handling Chatbot Errors: Techniques and Fallback Strategies](https://blog.com.bot/handling-chatbot-errors-techniques-and-fallback-strategies/)

**Privacidad** 🔬 📰
- [A literature review of user privacy concerns in conversational chatbots — JASIST](https://asistdl.onlinelibrary.wiley.com/doi/10.1002/asi.24898)
- [Study exposes privacy risks of AI chatbot conversations — Stanford](https://news.stanford.edu/stories/2025/10/ai-chatbot-privacy-concerns-risks-research)

**Arquitectura**
- [Building Conversational AI with State Machines and LLMs](https://www.brahimbouine.com/blog/building-conversational-ai-state-machines/)
- [Grice's Conversational Maxims applied to Conversation Design](https://medium.com/swlh/grices-conversational-maxims-applied-to-chatbot-conversational-ux-design-e8c4ba670c41)

**Métricas** 📰
- [Complete Guide to Chatbot Containment Rates 2026 — Botpress](https://botpress.com/blog/containment-rate)
- [Chatbot KPIs that prove ROI to leadership, with benchmarks — Netguru](https://www.netguru.com/blog/chatbot-kpis)

**Evaluación** 🔬
- [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://arxiv.org/pdf/2306.05685)
- [LLM-as-a-judge: a complete guide — Evidently AI](https://www.evidentlyai.com/llm-guide/llm-as-a-judge)
- [Building a Golden Dataset for AI Evaluation](https://www.getmaxim.ai/articles/building-a-golden-dataset-for-ai-evaluation-a-step-by-step-guide/)

**WhatsApp** 📰
- [Best practices for using chatbots on WhatsApp](https://www.aurorainbox.com/en/2025/04/10/whatsapp-chatbot-best-practices/)
- [WhatsApp chatbot ultimate guide: best practices](https://www.businesschat.io/post/whatsapp-chatbot-ultimate-guide)
