# Qué se puede aprender de cada conversación

Nunca vas a poder probar el bot con mil personas. Pero mil personas lo van a
usar, y cada una deja rastro. Este documento es la lista de todo lo que se puede
sacar de esos rastros, qué se hace con cada cosa, y cuánto cuesta juntarla.

**El hallazgo que ordena todo el documento:** hay dos clases de datos y se
mezclan fácil.

- **Los que arreglan el bot.** Te sirven a vos. Un negocio no los mira nunca.
- **Los que arreglan el NEGOCIO.** Ésos son producto: un dueño paga por
  «perdiste 14 turnos el sábado porque no tenías lugar», y eso sale de las
  mismas conversaciones sin pedirle nada a nadie.

La segunda lista es la que convierte esto de «un bot que atiende» en «un
sistema que además te dice cómo ganar más». Y sale gratis, del mismo tráfico.

---

# A · Datos que arreglan el bot

## A1 · Lo que el bot no entendió ⭐

**Qué se guarda:** el mensaje crudo y en qué paso estaba.

**Para qué:** cada frase que se repite es un agujero concreto. «tenés turno pa
hoy?» ×7 no es una estadística: es una fila que falta en la tabla de atajos.

**Por qué es la más importante:** arreglar una de éstas no cuesta ni un peso ni
una llamada al modelo. Es exactamente el mecanismo con el que se arreglaron
«quiero otro turno» y «no soy Milagros» — sólo que hoy hay que esperar a que
alguien lo reporte.

**Cuesta:** una tabla. Fácil.

## A2 · Dónde se cae la gente

**Qué se guarda:** en qué paso quedó cada conversación abandonada. *(Ya está
hecho — `/metricas`, `abandono_por_paso`.)*

**Para qué:** dice QUÉ arreglar, no que algo anda mal. Si el 40% se cae
eligiendo el horario, o la lista es confusa o no hay lugar — y son dos arreglos
distintos.

## A3 · Qué frena el guardián

**Qué se guarda:** la palabra exacta que hizo rechazar una redacción, y el texto
entero. *(Hoy se loguea y se pierde.)*

**Para qué:** la lista de verbos del guardián **no está completa ni puede
estarlo**. Cada rechazo es la evidencia de qué falta. Sin juntarlo, la función
se va degradando en silencio.

**Cuesta:** nada, ya se loguea. Hay que guardarlo en vez de tirarlo.

## A4 · Cuánto tarda la gente en cada paso

**Qué se guarda:** segundos entre que el bot pregunta y la persona contesta.

**Para qué:** dónde duda. Si tardan 40 segundos eligiendo el servicio, la lista
está mal escrita. Es la única forma de ver confusión que no termina en abandono.

## A5 · Por qué escaló

**Qué se guarda:** si pidió una persona o si se trabó. *(Hoy se cuentan juntas.)*

**Para qué:** son cosas opuestas. Pedir un humano es el producto funcionando;
trabarse es el producto fallando. Sumarlas esconde las dos.

## A6 · La conversación entera de los que fallaron

**Qué se guarda:** los mensajes de las conversaciones que terminaron mal.

**Para qué:** un mensaje suelto no dice por qué falló. La secuencia sí. Es lo
más caro de guardar y lo más caro de leer, así que va sólo para las que
terminaron en abandono o en escalación por trabarse.

---

# B · Datos que arreglan el negocio — **esto es producto**

## B1 · Demanda perdida ⭐⭐

**Qué se guarda:** cada vez que alguien pide un día u horario **que no estaba
disponible**.

**Para qué:** *«Este mes 14 personas quisieron sábado a la mañana y no tenías
lugar.»*

**Por qué es el dato más vendible de todos:** es plata que el negocio dejó de
ganar, contada. No es una métrica de software — es un número que el dueño
entiende en un segundo y que ningún competidor le está dando. Y sale del mismo
tráfico, sin preguntarle nada a nadie.

**Cuesta:** el flujo ya detecta esto (`_rechazo`), sólo hay que guardarlo.

## B2 · Qué preguntan que el negocio no tiene cargado ⭐

**Qué se guarda:** las preguntas sin respuesta, agrupadas. *(Hoy se le avisa al
panel una por una, sin contar.)*

**Para qué:** *«Te preguntaron 12 veces por estacionamiento y no lo tenés
cargado.»* El dueño carga una respuesta y el bot mejora solo.

**El detalle que lo hace funcionar:** agrupado y contado, no una por una. Una
lista de avisos sueltos se ignora en una semana; «12 veces» se atiende.

## B3 · Qué se pide más

**Qué se guarda:** servicio, día y franja horaria de cada pedido.

**Para qué:** *«El 60% pide sábado, y abrís medio día.»* Sirve para abrir
horarios, mover gente, o subir el precio de la franja pico.

## B4 · Cuándo escriben

**Qué se guarda:** hora y día de cada mensaje.

**Para qué:** cuándo hace falta que haya alguien mirando el panel. Y de paso:
qué porcentaje llega fuera del horario de atención — o sea cuántos turnos el bot
salvó de perderse.

## B5 · Quién vuelve

**Qué se guarda:** cuántos turnos sacó cada teléfono (hasheado).

**Para qué:** tasa de recompra. Es la métrica que un negocio de servicios mira
antes que cualquier otra.

## B6 · Cuántos turnos entraron por el bot

**Qué se guarda:** ya está.

**Para qué:** es la factura. «Este mes te entraron 47 turnos por WhatsApp» es lo
que justifica que te siga pagando.

---

# C · Lo que hay que PEDIR, no observar

## C1 · «¿Te resultó fácil?» ⭐

Después de confirmar un turno, una sola línea con 👍 / 👎.

**Para qué:** es el CSAT, y es uno de los cinco números que la industria mira.
El benchmark para un bot es 75–85% positivo. Hoy no tenemos ninguno.

**Cuesta:** un mensaje más por turno, y hay que pensarlo bien: si molesta, la
gente lo ignora y encima te quedaste sin el dato. Se prueba y se mide.

## C2 · Por qué pediste un humano

Cuando alguien escala, preguntarle en una línea qué necesitaba.

**Para qué:** el 86% quiere poder llegar a una persona, pero **por qué** llegan
es lo que dice si el bot está fallando o si simplemente hay cosas que un bot no
tiene que hacer. Son dos conclusiones opuestas.

---

# Lo que NO se guarda, y por qué

- **Nada del paso del nombre.** Ahí la gente escribe su nombre completo, y un
  nombre que el bot no entendió no se arregla mirando una tabla.
- **El teléfono en claro.** Hasheado alcanza para contar y para saber quién
  vuelve. Es el pecado 7 de la investigación, cometido del lado nuestro.
- **La conversación entera de todo el mundo.** Sólo de las que terminaron mal, y
  con un vencimiento.

---

# Cómo se accede

**Una página, `/tablero`, que se abre desde el celular.** Sin instalar nada y sin
credenciales, como `/gasto` y `/salud` — porque no expone ningún dato de ninguna
persona: son conteos y frases sueltas.

Dos vistas en la misma página:

| Para vos | Para el negocio |
|---|---|
| Lo que no entendió, por frecuencia | Demanda perdida |
| Dónde se cae la gente | Preguntas sin responder |
| Qué frena el guardián | Qué se pide más |
| Containment y costo | Turnos que entraron por el bot |

La segunda vista es la que después va al panel de aturno, y es la que el negocio
mira. Esa parte es del otro repo.

---

# En qué orden

| Ola | Qué | Por qué |
|---|---|---|
| **1** | A1 · lo que no entendió · **+** · A3 · lo que frena el guardián | Los dos ya se detectan y se tiran. Es guardar, no inventar. Arreglan el bot desde el primer día |
| **2** | B1 · demanda perdida · **+** · B2 · preguntas sin responder | El producto. Los dos ya se detectan también |
| **3** | `/tablero` | Sin esto, nada de lo anterior se mira |
| **4** | B3, B4, B5 · qué se pide, cuándo, quién vuelve | Salen de la misma tabla, con columnas |
| **5** | A4, A5, A6 · tiempos, motivo de escalada, conversaciones fallidas | Más caro, menos urgente |
| **6** | C1 · el 👍/👎 | Es el único que molesta a la persona. Va último y se mide si vale la pena |

**Lo importante de la ola 1 y 2: todo eso el bot YA lo sabe.** No hay que
detectar nada nuevo — hay que dejar de tirarlo.
