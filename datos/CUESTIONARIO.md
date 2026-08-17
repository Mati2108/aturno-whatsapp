# El cuestionario del negocio

De acá sale todo lo que el bot sabe contestar. Hoy se llena a mano; la idea es
que lo pregunte el panel de aturno durante la configuración, y que el archivo
`datos/<slug>.md` se genere solo con las respuestas.

## Las tres reglas

**1. Responder es opcional, siempre.** Nadie va a contestar sesenta preguntas de
una sentada. El negocio contesta las que quiere, cuando quiere, y el bot mejora
a medida que se completa. Un cuestionario obligatorio se abandona a la mitad y
queda peor que uno vacío.

**2. Lo que no se responde, NO va al archivo.** No se escribe "sin datos" ni se
deja el título solo. El RAG parte por encabezado `##` y devuelve la sección
entera: una sección vacía se recupera igual y la persona recibe un título sin
respuesta. Si no hay respuesta, la sección no existe.

**3. El bot no completa huecos.** Sin dato, contesta que no lo tiene y ofrece
sacar el turno (`plantillas.sin_dato()`). Un dato inventado manda a alguien
hasta el local confiando en algo falso; un "no lo tengo" solo lo deja igual que
antes de preguntar.

## Lo que NO hay que preguntar

Servicios, precios, duraciones, profesionales y horarios **ya están cargados en
aturno**. Preguntarlos de nuevo es pedirle al negocio que mantenga lo mismo en
dos lugares, y eso garantiza que tarde o temprano se contradigan: el bot diría
un precio y el local cobraría otro.

Esas secciones igual aparecen en el archivo final, pero **las completa el
generador leyendo la API**, no el negocio escribiéndolas. Cada vez que se
regenera el archivo vuelven a quedar al día. El cuestionario cubre únicamente
**lo que aturno no sabe de ningún modo**.

---

## Las preguntas

Agrupadas por sección. Cada grupo termina siendo un `##` del archivo final, que
es la unidad que el RAG recupera: por eso conviene que las preguntas de un mismo
grupo se respondan juntas o no se respondan.

### Cómo llegar

- ¿Cuál es la dirección exacta? ¿Piso, departamento, timbre?
- ¿Alguna referencia para encontrarlo? ("al lado de la farmacia", "entrada por
  el pasillo")
- ¿Qué colectivos o subtes paran cerca?
- ¿Hay estacionamiento propio? ¿Es gratis?
- Si no hay: ¿dónde se puede estacionar? ¿Es zona de medidor?
- ¿El lugar es accesible en silla de ruedas? ¿Hay ascensor?

### Antes de venir

- ¿Hay que traer algo? (documento, estudios previos, credencial)
- ¿Cuánto antes conviene llegar?
- ¿Se puede venir acompañado?
- ¿Se atiende a menores? ¿Con qué condición?
- ¿Hay que venir de alguna forma en particular? (en ayunas, sin maquillaje, con
  el pelo lavado)

### Pagos

- ¿Qué medios de pago aceptan?
- ¿Aceptan transferencia? ¿A qué alias?
- ¿Trabajan con obras sociales o prepagas? ¿Cuáles?
- ¿Hay descuento por pagar en efectivo?
- ¿Se paga antes o después?

### Turnos

- ¿Con cuánta anticipación conviene sacar turno?
- ¿Qué pasa si llego tarde? ¿Cuántos minutos de tolerancia hay?
- ¿Hasta cuándo se puede cancelar sin costo?
- ¿Cobran algo si no aviso y no voy?
- ¿Atienden sin turno?
- ¿Tienen lista de espera si no hay lugar?

### El lugar

- ¿Tienen wifi para los clientes?
- ¿Hay baño?
- ¿Se puede entrar con mascotas?
- ¿Hay sala de espera? ¿Entra un acompañante?

### Contacto humano

- ¿A qué teléfono llamar si es urgente?
- ¿En qué horario atienden el teléfono?
- ¿Tienen Instagram o web?

### Lo suyo

Un campo de texto libre, sin pregunta. Para lo que el negocio sabe que le
preguntan siempre y no entró en ninguna categoría. En la práctica es el más
valioso: nadie conoce mejor que el mostrador cuáles son las cinco preguntas que
se repiten todos los días.

---

## Cómo se convierte en el archivo

Cada grupo con al menos una respuesta se escribe como una sección `##` en
`datos/<slug>.md`, en prosa corta. Las preguntas no se escriben: solo las
respuestas, redactadas como se las contestaría a alguien por WhatsApp.

Después hay que reindexar, porque el RAG busca sobre los embeddings ya
calculados y no lee los archivos en cada consulta:

    python -m src.rag.indice

Eso reconstruye el índice de cero. Reindexar encima duplicaría los fragmentos,
y un duplicado se ve como el bot repitiéndose sin razón.
