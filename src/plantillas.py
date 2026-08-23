"""
plantillas.py — TODO lo que el usuario lee sale de acá.

LA REGLA
--------
El modelo nunca redacta el mensaje final. Clasifica intención y extrae datos;
el texto que llega al WhatsApp de una persona lo arma este módulo.

Por qué. Probando el bot con el LLM redactando aparecieron, en una sola tarde:
el saludo cambiaba de forma en cada conversación, un listado salía horizontal y
el siguiente vertical, se filtró la tabla interna con los ids (`svc-corte | ...`)
y se colaba un "¿necesitás algo más?" que no queríamos. Ninguno se arregla
pidiéndolo por prompt: son variaciones de un generador probabilístico. Se
arreglan sacándole la redacción.

Efecto secundario: la apertura es byte a byte idéntica siempre, los listados
son verticales por construcción, y es imposible que el usuario vea un JSON.

FORMATO
-------
- Un ítem por línea, con "\\n" real. Nunca separados por comas.
- Listas numeradas: la persona puede contestar el número, y resolver un número
  no cuesta un token de LLM.
- Sin markdown: WhatsApp no lo renderiza y los asteriscos se ven como basura.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.agentes.estados import hay_negacion
from src.fechas import hoy as hoy_del_negocio

from src.schemas import (
    Alternativa,
    DiaConCupo,
    MotivoNoDisponible,
    Profesional,
    Servicio,
    SinLugar,
)

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _plata(monto: float) -> str:
    """8000 -> '$8.000'. Separador de miles con punto, como se escribe acá."""
    return "$" + f"{monto:,.0f}".replace(",", ".")


def _dia_corto(d: date) -> str:
    """'Lunes 17' — el formato que pidió el cliente."""
    return f"{DIAS[d.weekday()]} {d.day}"


# ══════════════════════════════════════════════════════════════════
# T1 · Apertura
# ══════════════════════════════════════════════════════════════════

def apertura(negocio: str, servicios: list[Servicio], nombre: str | None = None) -> str:
    """El primer mensaje. Sesión nueva o expirada dispara SIEMPRE este texto.

    Lo único que varía es el nombre, y solo si el teléfono ya es de un cliente
    conocido. Todo lo demás es idéntico byte a byte en cada apertura — es lo
    que hace que el bot se sienta un producto y no una improvisación.

    Un solo CTA al final: si le das dos, la gente no contesta ninguno.
    """
    saludo = f"Hola {nombre}!" if nombre else "Hola!"

    # Un renglón en blanco entre bloques, siempre.
    #
    # Sin eso, WhatsApp junta las frases en un párrafo corrido y el mensaje se
    # lee como una pared: en la pantalla de un celular, dos oraciones seguidas
    # sin aire son una sola cosa larga que nadie termina de leer. Cada bloque
    # de acá es una idea distinta —quién soy, qué hacemos, qué podés hacer vos,
    # y la salida— y por eso van separados.
    bloques = [f"{saludo} Soy el asistente de {negocio}."]

    # Sin servicios NO se anuncia un menú. Pasó en producción: el bot decía
    # "Esto es lo que hacemos:" y abajo no había nada, porque estaba leyendo un
    # negocio que no conocía. Anunciar una lista y no mostrarla es peor que no
    # anunciarla — la persona se queda esperando el resto del mensaje.
    #
    # Le puede pasar a cualquier negocio que todavía no cargó sus servicios, así
    # que no alcanza con arreglar la configuración: la plantilla tiene que
    # aguantar el caso.
    if not servicios:
        bloques.append(
            "Ahora mismo no puedo mostrarte los servicios. "
            "Si me decís qué necesitás, le aviso a alguien del local para que "
            "te responda."
        )
        return "\n\n".join(bloques)

    if len(servicios) == 1:
        # El precio y la duración en su propio renglón, debajo del nombre. Un
        # servicio con guiones en el medio se lee como una fórmula.
        s = servicios[0]
        bloques.append(f"Sacamos turnos para {s.nombre}.\n"
                       f"{s.duracion_minutos} min · {_plata(s.precio)}")
    else:
        lista = ["Esto es lo que hacemos:", ""]
        for i, s in enumerate(servicios, 1):
            lista.append(f"{i}. {s.nombre}")
            lista.append(f"   {s.duracion_minutos} min · {_plata(s.precio)}")
        bloques.append("\n".join(lista))

    # Dos cosas en este bloque, y las dos costaron un caso real.
    #
    # Que las preguntas son MUCHAS y en cualquier momento. Decía "¿querés sacar
    # un turno o tenés alguna pregunta?": en singular y planteado como
    # alternativa, o sea "elegí una de las dos". Así, el que ya estaba
    # eligiendo el día no volvía a preguntar nada.
    #
    # Y que cierra con una pregunta ABIERTA. Este es el único momento de la
    # conversación sin una lista que responder, y por eso es por donde entra
    # todo lo que no es reservar. Un "elegí una opción" acá manda a la gente a
    # tirar un número al azar para poder seguir, y recién después preguntar lo
    # que quería preguntar.
    bloques.append("Preguntame lo que quieras —precios, cómo llegar, formas de "
                   "pago, lo que sea— las veces que necesites, antes o durante.\n\n"
                   "¿Qué necesitás?")

    # La salida a una persona va nombrada, pero última y en su propio bloque:
    # si está escondida no sirve de nada, y si compite con la pregunta de
    # arriba la mitad la elige sin haber probado. Lo que la inclina es el
    # argumento, no esconderla.
    bloques.append("Si preferís hablar con alguien del local, pedímelo y le "
                   "aviso. Por acá suele ser más rápido.")

    return "\n\n".join(bloques)


# ══════════════════════════════════════════════════════════════════
# T2 · Servicios
# ══════════════════════════════════════════════════════════════════

def lista_servicios(servicios: list[Servicio], preseleccion: str | None = None) -> str:
    """La lista, con tilde en el que el clasificador detectó.

    El paso no se saltea aunque la persona ya haya dicho qué quiere: el orden
    es el mismo que en la web, y ver el match marcado le confirma que la
    entendimos bien antes de avanzar.
    """
    # Sin servicios NO se anuncia una lista. `apertura` ya se protege de esto
    # desde que pasó en producción; acá no, y el agujero volvió a aparecer solo
    # —un negocio recién dado de alta no tiene nada cargado—. Pedirle a alguien
    # que "responda con el número" cuando no hay ningún número no es sólo raro:
    # es un callejón, porque después ninguna respuesta puede ser válida.
    if not servicios:
        return ("Todavía no tengo cargados los servicios de este negocio.\n\n"
                "Escribime «una persona» y te contesta alguien del local.")

    lineas = ["Elegí el servicio:", ""]
    for i, s in enumerate(servicios, 1):
        marca = " ✓" if preseleccion and s.id == preseleccion else ""
        lineas.append(f"{i}. {s.nombre} — {s.duracion_minutos} min — {_plata(s.precio)}{marca}")
    if preseleccion:
        lineas += ["", "Confirmá con el número o elegí otro."]
    else:
        lineas += ["", "Respondé con el número."]
    lineas += ["", "Si preferís, escribime «una persona» y te contesta alguien del local."]
    return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════════
# T3 · Staff
# ══════════════════════════════════════════════════════════════════

def lista_staff(personas: list[Profesional], servicio: str | None) -> str:
    """El equipo. `servicio=None` omite el eco del servicio elegido.

    Ese eco confirma lo que la persona acaba de elegir, y por eso está. Pero
    cuando este pedido va pegado al saludo —el caso del negocio con un solo
    servicio— el nombre ya se dijo dos renglones arriba, y repetirlo suena a
    que el bot no se acuerda de lo que escribió recién.
    """
    encabezado = f"{servicio}. ¿Con quién lo querés?" if servicio else "¿Con quién lo querés?"
    lineas = [encabezado, ""]
    for i, p in enumerate(personas, 1):
        lineas.append(f"{i}. {p.nombre}")
    lineas += [f"{len(personas) + 1}. Me da igual", "",
               "Respondé con el número o el nombre.",
               "", "Si preferís, escribime «una persona» y te contesta alguien del local."]
    return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════════
# T4 · Días
# ══════════════════════════════════════════════════════════════════

# Cómo se le dice a la persona que ese día no tiene turnos. Cada motivo con
# sus palabras: "cerrado" cuando el profesional elegido no trabaja es
# información falsa sobre el negocio.
_POR_QUE = {
    SinLugar.CERRADO: "cerrado",
    SinLugar.NO_ATIENDE: "no atiende ese día",
    SinLugar.YA_PASO: "ya no quedan horarios",
    SinLugar.COMPLETO: "completo",
}


def _semaforo(libres: int, mejor: int) -> str:
    """Verde, amarillo o rojo según cuánto lugar queda ese día.

    Con emoji y no con color: WhatsApp no renderiza formato, así que un
    semáforo "visual" tiene que ser un carácter. Y el círculo va ANTES del
    número — se lee la fila entera de un vistazo sin pasar por el texto, que
    es exactamente para lo que sirve un semáforo.
    """
    if libres <= 0:
        return "🔴"
    if mejor <= 0:
        return "🟢"
    proporcion = libres / mejor
    if proporcion >= 0.6:
        return "🟢"
    if proporcion >= 0.25:
        return "🟡"
    return "🔴"


def selector_dias(dias: list[DiaConCupo], hoy: date | None = None) -> str:
    """Los próximos N días con su cupo, agrupados por semana.

    El encabezado y el separador existen porque "Lunes 17" y "Lunes 24" se
    parecen demasiado en una lista corrida: sin la marca de semana la gente
    elige el lunes equivocado.

    La cantidad de días la define el negocio (`dias_a_mostrar`), no esta
    función: siete es un default, no una regla.
    """
    hoy = hoy or hoy_del_negocio()
    # El lunes de la semana en curso. Cruzar de semana = cambiar de bloque.
    semana_actual = hoy - timedelta(days=hoy.weekday())

    # El semáforo es RELATIVO al mejor día de la lista, no a un número fijo.
    # Un consultorio con 4 turnos por día y una peluquería con 30 no pueden
    # compartir el mismo umbral: en el primero, 3 libres es un día holgado; en
    # la segunda, es un día casi lleno. Comparar contra el mejor día hace que
    # el color signifique lo mismo en los dos.
    mejor = max((d.libres for d in dias if d.motivo is None), default=0)

    lineas = []
    bloque_anterior: int | None = None
    numero = 0

    for d in dias:
        bloque = (d.fecha - semana_actual).days // 7
        if bloque != bloque_anterior:
            if bloque_anterior is not None:
                lineas.append("")  # separador al cruzar de semana
            lineas.append(_encabezado_semana(bloque))
            bloque_anterior = bloque

        # Solo llevan número los días que se pueden elegir. Un día cerrado
        # numerado es una opción que la persona va a tocar y va a rebotar;
        # mostrarlo sin número mantiene visible la forma de la semana sin
        # ofrecer algo que no existe.
        if d.motivo is not None:
            lineas.append(f"⚫ {_dia_corto(d.fecha)} — {_POR_QUE[d.motivo]}")
        else:
            numero += 1
            plural = "turno" if d.libres == 1 else "turnos"
            lineas.append(f"{_semaforo(d.libres, mejor)} {numero}. "
                          f"{_dia_corto(d.fecha)} — {d.libres} {plural}")

    if numero == 0:
        # Decía sólo la primera línea, y ahí se terminaba la conversación: la
        # persona quedaba con un no y sin ninguna puerta. Poder llegar a un
        # humano es lo que el 87% de los clientes considera esencial, y el
        # momento en que más se busca es justo después de un no.
        return ("No me queda ningún día con lugar en las próximas semanas.\n\n"
                "Escribime «una persona» y te contesta alguien del local.")

    # Los días sin lugar no llevan número, así que la numeración salta. Decir
    # solo "respondé con el número" delante de una lista con huecos parece un
    # error del bot; nombrar el día siempre funciona y es lo que la gente hace.
    lineas += ["", "Respondé con el número o el día (por ejemplo, «el jueves»)."]
    lineas += ["", "Si preferís, escribime «una persona» y te contesta alguien del local."]
    return "\n".join(lineas)


def dias_elegibles(dias: list[DiaConCupo]) -> list[DiaConCupo]:
    """Los días que sí tienen número, en el mismo orden que los muestra T4.

    La máquina de estados resuelve "3" contra esta lista. Tiene que salir del
    mismo módulo que la plantilla: si el filtro y el renderizado se separan,
    en algún momento se desincronizan y el número 3 deja de ser el que la
    persona vio en pantalla.
    """
    return [d for d in dias if d.motivo is None and d.libres > 0]


def _encabezado_semana(bloque: int) -> str:
    return {0: "Esta semana", 1: "Próxima semana"}.get(bloque, f"En {bloque} semanas")


# ══════════════════════════════════════════════════════════════════
# T5 · Horarios
# ══════════════════════════════════════════════════════════════════

def lista_horarios(dia: date, horarios: list, maximo: int = 8) -> str:
    """Vertical, no separados por comas.

    Se cortan en `maximo` porque una lista de veinte horarios no se lee en un
    celular; el resto se pide explícitamente.
    """
    # Mismo caso que en los servicios: un día sin horarios libres anunciaba
    # "Horarios para el Jueves 20:" y abajo nada. Acá además hay una salida
    # mejor que llamar a una persona —probar otro día— así que va primero.
    if not horarios:
        return (f"No me quedan horarios libres el {_dia_corto(dia)}.\n\n"
                "Escribime «otro día» y te muestro los que sí tienen lugar, "
                "o «una persona» y te contesta alguien del local.")

    mostrados = horarios[:maximo]
    lineas = [f"Horarios para el {_dia_corto(dia)}:", ""]
    for i, h in enumerate(mostrados, 1):
        lineas.append(f"{i}. {h:%H:%M}")
    if len(horarios) > maximo:
        lineas += ["", f"Hay {len(horarios) - maximo} horarios más tarde. Pedime 'más'."]
    lineas += ["", "Respondé con el número."]
    lineas += ["", "Si preferís, escribime «una persona» y te contesta alguien del local."]
    return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════════
# T6 · Resumen y T7 · Nombre
# ══════════════════════════════════════════════════════════════════

def resumen(servicio: str, staff: str | None, dia: date, hora,
            cliente: str | None = None, de_memoria: bool = False,
            senia: int = 0) -> str:
    """Todo lo que se va a reservar, incluido A NOMBRE DE QUIÉN.

    El nombre faltaba, y es el dato más fácil de que esté mal sin que nadie se
    entere. Un teléfono lo comparte una familia: la primera vez lo saca la
    madre, y la segunda el bot saltea el paso del nombre —porque ya lo
    recuerda— y le reserva al hijo un turno a nombre de ella. Nadie lo nota
    hasta que llega al mostrador.

    Mostrarlo es la mitad; la otra es poder corregirlo sin volver atrás, y por
    eso la línea de abajo dice cómo.

    `de_memoria` es la tercera parte, y la que faltaba. Cuando el nombre no se
    dijo en esta conversación sino que viene de la vez anterior, mostrarlo como
    un hecho —"Para: Matías"— invita a leerlo por arriba y contestar que sí. El
    caso de la familia es exactamente ese: el dato está bien puesto y mal
    aplicado. Con la memoria de por medio se pregunta en vez de afirmar, que es
    la diferencia entre repasar y confirmar.

    No sale un mensaje aparte a propósito. El resumen se manda igual, así que
    preguntar acá no cuesta nada; una pregunta suelta antes del resumen suma un
    ida y vuelta a cada reserva, y con el tope diario de Twilio eso es menos de
    la mitad de turnos posibles en un día.
    """
    lineas = ["Repasemos:", ""]
    if cliente:
        lineas.append(f"Para: {cliente}" if not de_memoria
                      else f"Para: {cliente} (el nombre que me diste antes)")
    lineas.append(f"Servicio: {servicio}")
    if staff:
        # "Te atiende" y no "Con": la etiqueta corta no decía qué era ese
        # nombre, y quien lee un resumen antes de confirmar no tiene que
        # deducir nada. El dato se queda —la persona lo eligió dos pasos
        # antes— pero dicho de manera que se entienda solo.
        lineas.append(f"Te atiende: {staff}")
    lineas += [f"Día: {_dia_corto(dia)}", f"Hora: {hora:%H:%M}"]
    # La seña se avisa ACÁ, antes de confirmar, y no cuando llega el link.
    #
    # Es lo que hace la web: abre el modal del depósito antes de crear nada. Que
    # la persona diga "sí" creyendo que reserva gratis y recién ahí le aparezca
    # un cobro es la forma más rápida de perderla —y con razón: aceptó otra cosa.
    if senia:
        lineas.append(f"Seña: {_plata(senia)} (se paga ahora, por Mercado Pago)")
    lineas.append("")

    if cliente and de_memoria:
        lineas.append(f"¿Va a nombre de {cliente}? Respondé SÍ.")
        lineas.append("Si el turno es para otra persona, escribime su nombre.")
    elif cliente:
        lineas.append("¿Confirmo? Respondé SÍ.")
        lineas.append("Si el turno es para otra persona, escribime su nombre.")
    else:
        lineas.append("¿Confirmo? Respondé SÍ o NO.")
    lineas += ["", "Si preferís hablar con alguien del local, pedímelo."]
    return "\n".join(lineas)


def que_nombre() -> str:
    """Negó el nombre que teníamos pero no dijo cuál es el suyo.

    Es el caso de "no me llamo Milagros", a secas. El bot no puede adivinarlo
    —el único nombre de la frase es el que la persona está rechazando— así que
    lo pregunta, y pide disculpas primero porque veníamos llamándola mal.

    Corto y sin repetir el nombre viejo: nombrarlo otra vez para decir que no
    es ese es justo lo que no quiere leer.
    """
    return "Perdón. ¿Cómo te llamás?"


def nombre_actualizado(nombre: str) -> str:
    """Acusa recibo de una corrección de nombre, en una línea.

    Sin esto, corregirse se sentía igual que ser ignorado: el bot actualizaba
    por dentro y volvía a mostrar el paso, sin una palabra que dijera que
    escuchó. Quien acaba de decir "no me llamo así" necesita ver que cambió.

    Va antes del pedido del paso, no en un mensaje aparte: el paso se manda
    igual, así que reconocerlo sale gratis.
    """
    return f"Listo, te anoto como {nombre}."


def pedir_nombre() -> str:
    return "Para cerrarlo necesito tu nombre y apellido."


def pedir_senia(monto: int, link: str, minutos: int | None) -> str:
    """El turno quedó apartado y falta pagar la seña. Con el link.

    TRES COSAS, EN ESTE ORDEN
    Primero que el turno NO está confirmado todavía, porque es lo que la persona
    va a asumir mal si no se lo decimos: el mensaje anterior fue un resumen y el
    siguiente trae un link, y entre esas dos cosas es fácil leer "listo".
    Después el monto y el link. Y al final el reloj.

    El plazo va último y no primero a propósito: arrancar con "tenés 15 minutos"
    convierte un trámite en una carrera antes de que la persona sepa siquiera
    cuánto tiene que pagar. Pero va, porque el horario se libera solo y
    enterarse cuando ya se liberó es peor que cualquier apuro.

    Si no sabemos el plazo no se inventa ninguno: se dice que es por un rato. Un
    número equivocado acá es peor que ninguno — significa que alguien deja de
    apurarse creyendo que le quedan diez minutos que no tiene.
    """
    cuanto = (f"Te lo aparto {minutos} minutos" if minutos
              else "Te lo aparto un rato")
    return "\n".join([
        f"Te falta pagar la seña de {_plata(monto)} para confirmarlo.",
        "",
        link,
        "",
        f"{cuanto}. Si no llega el pago, el horario se libera y lo puede tomar "
        "otra persona.",
    ])


def senia_confirmada(servicio: str, staff: str | None, dia: date, hora,
                     codigo: str) -> str:
    """Entró el pago: recién ahora el turno es suyo.

    Es un mensaje aparte y no una variante del de arriba porque llega SOLO, sin
    que la persona escriba nada: pagó en Mercado Pago, cerró la pestaña, y lo
    último que le dijo el bot fue "te falta pagar". Sin esto se queda sin saber
    si el turno salió, y el que duda vuelve a reservar o llama al local.
    """
    return "\n".join([
        "¡Listo! Entró el pago y el turno quedó confirmado.",
        "",
        f"{servicio}" + (f" con {staff}" if staff else ""),
        f"{_dia_corto(dia)} a las {hora:%H:%M}",
        f"Código: {codigo}",
    ])


def falta_pagar() -> str:
    """Escribió mientras esperamos la seña, sin decir nada nuevo.

    Se le recuerda lo único que falta, en una línea. La alternativa era volver a
    mandarle el link entero, que a esa altura ya lo tiene arriba en el chat.
    """
    return ("Sigo esperando el pago de la seña para confirmarte el turno. "
            "El link te lo mandé recién acá arriba.")


def senia_vencida() -> str:
    """Se acabó el plazo y no llegó el pago.

    Se avisa aunque sea una mala noticia: el horario se liberó y la persona no
    tiene forma de saberlo. Enterarse el día del turno, en la puerta del local,
    es la única variante peor.

    Y se ofrece volver a empezar en la misma línea, porque casi siempre el que
    no llegó a pagar sigue queriendo el turno.
    """
    return "\n".join([
        "Se venció el tiempo para pagar la seña, así que solté el horario.",
        "",
        "Si todavía lo querés, escribime y lo buscamos de nuevo.",
    ])


def no_se_pudo_cobrar(url: str | None) -> str:
    """El servicio pide seña y no se pudo generar el link de pago.

    NO se promete el turno. El servicio se seña porque el negocio decidió que
    sin garantía no lo da, así que darlo igual por WhatsApp sería usar el canal
    para saltear una regla del negocio — que es exactamente lo que este arreglo
    vino a cerrar.

    El horario que se había apartado se libera solo: la reserva quedó esperando
    un pago que no va a llegar y su retención vence. No hay nada que cancelar a
    mano.

    Se manda a la web, donde el mismo cobro sí funciona, y si no hay link
    configurado queda la persona. Nunca "probá más tarde": el problema es del
    lado del negocio y no se arregla esperando.
    """
    lineas = ["No pude generar el link para cobrar la seña, así que no te dejo "
              "el turno tomado."]
    if url:
        lineas += ["", f"Podés sacarlo desde acá, que ahí sí se puede pagar: {url}",
                   "", "O escribime «una persona» y te lo resuelven del local."]
    else:
        lineas += ["", "Escribime «una persona» y te lo resuelven del local."]
    return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════════
# T8 · Confirmado
# ══════════════════════════════════════════════════════════════════

def confirmado(servicio: str, staff: str | None, dia: date, hora, codigo: str) -> str:
    con = f" con {staff}" if staff else ""
    return "\n".join([
        "Listo, turno confirmado.",
        "",
        f"{servicio}{con}",
        f"{_dia_corto(dia)} a las {hora:%H:%M}",
        f"Código: {codigo}",
        "",
        # NO decir "avisame y lo cancelo": el bot no puede cancelar turnos, y
        # prometerlo acá es lo que hacía que la persona escribiera "cancelar",
        # leyera que estaba hecho, y no fuera. Se nombra la vía que sí existe.
        "Si necesitás cancelar o cambiarlo, escribime «una persona»",
        "y te lo resuelven del local.",
    ])


# ══════════════════════════════════════════════════════════════════
# T9 · No disponible
# ══════════════════════════════════════════════════════════════════

_MOTIVOS = {
    MotivoNoDisponible.CERRADO: "Ese día está cerrado.",
    MotivoNoDisponible.FUERA_DE_HORARIO: "A esa hora no atendemos.",
    MotivoNoDisponible.OCUPADO: "Ese horario ya está tomado.",
    MotivoNoDisponible.PROFESIONAL_OCUPADO: "Esa persona ya tiene un turno a esa hora.",
    MotivoNoDisponible.PROFESIONAL_NO_HACE: "Esa persona no hace ese servicio.",
}


def no_disponible(motivo: MotivoNoDisponible, alternativas: list[Alternativa],
                  pedida: str | None = None, sugerencia: str | None = None) -> str:
    """El motivo SIEMPRE, nunca un "no hay" pelado.

    Decir solo "no hay" obliga a la persona a adivinar qué probar. El motivo
    más una alternativa concreta convierte un rechazo en el próximo paso.

    `pedida` es la hora que escribió, y cambia el motivo cuando cae DENTRO del
    horario pero fuera de la grilla. Alguien que pide las 9:39 con turnos cada
    30 minutos no está pidiendo un horario en que el local esté cerrado:
    contestarle "a esa hora no atendemos" es falso y encima confuso, porque
    abajo le ofrecemos las 9:00 del mismo día.
    """
    # Con una sugerencia cerca, una sola pregunta cerrada y nada más. Alguien
    # que se equivocó por nueve minutos no necesita volver a elegir de una
    # lista: necesita confirmar lo que quiso poner.
    if sugerencia:
        return "\n".join([
            f"Las {pedida} no las tengo. ¿Te sirven las {sugerencia}?",
            "",
            "Respondeme sí, o decime otro horario.",
        ])

    if pedida and motivo == MotivoNoDisponible.FUERA_DE_HORARIO and alternativas:
        cabecera = f"Las {pedida} no las tengo disponibles."
    else:
        cabecera = _MOTIVOS.get(motivo, "No puedo dar ese turno.")
    lineas = [cabecera]
    if alternativas:
        lineas += ["", "Lo más cercano que tengo:", ""]
        for i, a in enumerate(alternativas[:3], 1):
            lineas.append(f"{i}. {_dia_corto(a.fecha)} a las {a.hora:%H:%M}")
        lineas += ["", "Respondé con el número, o decime otro día."]
    else:
        lineas += ["", "¿Querés que busque otro día?"]
    return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════════
# T10-T14 · Bordes
# ══════════════════════════════════════════════════════════════════

def error_tecnico() -> str:
    """Nunca un stack trace ni un JSON. Esto es lo que ve una persona.

    Ofrece la persona sin ir a buscar el teléfono: este mensaje sale justo
    cuando algo falló, y muy probablemente lo que falló sea la conexión con
    aturno — que es de donde saldría el contacto. Pedirlo acá convertiría un
    error en dos.
    """
    return (
        "Uy, se me complicó procesar eso. ¿Probamos de nuevo en un minuto?\n\n"
        "Si preferís, escribime «hablar con una persona»."
    )


def escalado(nombre_negocio: str, aviso_llego: bool, contacto) -> str:
    """Lo que se le dice a la persona cuando la conversación pasa al negocio.

    La diferencia con dar un teléfono es toda: el teléfono le pasa el trabajo a
    la persona —que llame ella, desde un chat que ya tenía abierto—, y esto lo
    deja del lado del negocio. Por eso el mensaje dice que ya se avisó, no que
    llame.

    Si el aviso NO llegó a ningún lado, se dice y se pasa el contacto. Prometer
    que alguien va a responder cuando nadie se enteró es la peor variante: la
    persona espera, y espera al pedo.
    """
    if aviso_llego:
        return "\n".join([
            f"Listo, le avisé a {nombre_negocio}. Te responden por acá mismo.",
            "",
            "Mientras tanto no toco nada de lo que veníamos armando.",
            "Si preferís seguir sola, escribime «seguir con el bot».",
        ])

    lineas = [f"No pude avisarle a {nombre_negocio} desde acá, así que te paso "
              "el contacto directo:"]
    if contacto and contacto.hay_algo():
        lineas.append("")
        if contacto.telefono:
            lineas.append(f"Teléfono: {contacto.telefono}")
        if contacto.email:
            lineas.append(f"Email: {contacto.email}")
    lineas += ["", "Tu turno queda como está. Cuando quieras seguimos."]
    return "\n".join(lineas)


def volvio_el_bot(reintento: str) -> str:
    """El bot retoma. Repite el pedido del paso para no dejarla colgada."""
    return "\n".join(["Sigo yo.", "", reintento])


def link_web(nombre_negocio: str, url: str | None) -> str:
    """El link a la página, para quien prefiera reservar ahí.

    Sin URL configurada no se inventa ninguna: se dice que se puede seguir por
    acá. Un link roto es peor que no dar link — la persona lo abre, ve un 404 y
    asume que el negocio no funciona.
    """
    if not url:
        return ("No tengo el link a mano, pero lo podemos hacer por acá mismo "
                "y te queda el turno igual. ¿Seguimos?")
    return "\n".join([
        f"Ahí va la página de {nombre_negocio}:",
        "",
        url,
        "",
        "Si preferís, lo cerramos por acá y no hace falta que entres. "
        "¿Seguimos?",
    ])


def demorado(nombre_negocio: str, url: str | None) -> str:
    """Cuando la respuesta está tardando y todavía se está trabajando en ella.

    A los diez segundos alguien deja de esperar y empieza a preguntarse si esto
    anda. Es el límite que Nielsen fija para mantener la atención en un diálogo:
    pasado eso hay que decir qué está pasando, y no basta con seguir callado.

    Este mensaje NO cancela nada: la respuesta de verdad sigue viniendo y llega
    igual. Es una señal de vida con dos salidas por si la persona no quiere
    esperar, y las dos son cosas que ya puede hacer sola.

    El link va primero y la persona segunda, en ese orden a propósito: el que
    tiene apuro resuelve solo en la web, y el que tiene una duda que el bot no
    supo contestar necesita a alguien. Si no hay URL configurada no se inventa
    ninguna —un link roto es peor que ninguno, misma regla que `link_web`— y
    queda sólo la salida humana.

    Sale un mensaje de más, y eso cuesta: el cupo de Twilio es finito. Por eso
    se manda sólo cuando la demora ya ocurrió, y no como aviso preventivo en
    cada consulta.
    """
    lineas = ["Perdón, esto me está tardando más de lo normal.",
              "Seguí tranquilo que lo estoy resolviendo, pero si tenés apuro:"]
    if url:
        lineas += ["", f"Sacá el turno vos mismo acá: {url}",
                   "", "O escribime «una persona» y te contesta alguien del local."]
    else:
        # Sin link queda una sola salida, así que la frase no puede empezar con
        # "O": una alternativa a nada se lee como si faltara algo.
        lineas += ["", "Escribime «una persona» y te contesta alguien del local."]
    return "\n".join(lineas)


def no_pudo_contestar(nombre_negocio: str, url: str | None) -> str:
    """Cuando se agotó el tiempo y ya no va a haber respuesta.

    Antes decía "¿me lo mandás de nuevo?", que le devuelve el trabajo a la
    persona: reintentar contra algo que acaba de fallar es lo que menos ganas
    tiene de hacer, y encima probablemente vuelva a fallar. Un mensaje de error
    que no ofrece una salida es un callejón.
    """
    lineas = ["Perdón, no pude procesar tu mensaje a tiempo."]
    if url:
        lineas += ["", f"Podés sacar el turno acá: {url}",
                   "", "O escribime «una persona» y te contesta alguien del local."]
    else:
        lineas += ["", "Escribime «una persona» y te contesta alguien del local."]
    return "\n".join(lineas)


def solo_adjunto(tipo: str = "") -> str:
    """Llegó un audio, una foto o un sticker y nada de texto.

    Antes esto no llegaba a ninguna plantilla: el webhook lo rechazaba con 422
    y la persona no recibía NADA. Alguien mandaba una nota de voz pidiendo
    turno y le hablaba a una pared, sin enterarse nunca.

    El mensaje nombra lo que mandó en vez de decir "no entendí": la diferencia
    entre "no puedo escuchar audios" y "no entendí" es que la primera explica
    por qué y la segunda parece que la persona se explicó mal.
    """
    # `tipo` puede llegar vacío o en None: Twilio no siempre manda el
    # content-type, y una plantilla que revienta deja a la persona sin ninguna
    # respuesta — que es exactamente el agujero que esta función vino a tapar.
    tipo = tipo or ""
    if tipo.startswith("audio"):
        que = "Todavía no puedo escuchar audios"
    elif tipo.startswith("image"):
        que = "Todavía no puedo ver imágenes"
    elif tipo.startswith("video"):
        que = "Todavía no puedo ver videos"
    else:
        que = "Todavía no puedo abrir archivos"
    return "\n".join([
        f"{que}. ¿Me lo escribís?",
        "",
        "Si preferís, escribime «una persona» y te contesta alguien del local.",
    ])


def hablar_con_persona(nombre_negocio: str, contacto) -> str:
    """La salida de emergencia. Disponible en cualquier punto del flujo.

    Lo que más importa acá es la última línea. Alguien que pide hablar con una
    persona a mitad de una reserva está a un paso de abandonar, y si además
    sospecha que pedirlo le borra lo que venía eligiendo, no lo pide: se va. Es
    la misma regla que el botón atrás — nada de lo elegido se pierde nunca.

    Sin contacto cargado no se inventa ninguno: se dice que no lo hay.
    """
    if not contacto or not contacto.hay_algo():
        return "\n".join([
            f"No tengo un contacto cargado de {nombre_negocio} para pasarte, "
            "así que no te quiero mandar a ningún lado.",
            "",
            "Tu turno queda como está. Cuando quieras seguimos.",
        ])

    lineas = [f"Te paso el contacto de {nombre_negocio}:", ""]
    if contacto.telefono:
        lineas.append(f"Teléfono: {contacto.telefono}")
    if contacto.whatsapp and contacto.whatsapp != contacto.telefono:
        lineas.append(f"WhatsApp: {contacto.whatsapp}")
    if contacto.email:
        lineas.append(f"Email: {contacto.email}")
    if contacto.direccion:
        lineas.append(f"Dirección: {contacto.direccion}")
    lineas += ["", "No perdiste nada de lo que veníamos armando. "
                   "Si querés seguimos por acá."]
    return "\n".join(lineas)


def no_reservo() -> str:
    """El "no" delante del resumen. Lo primero es decir que NO se reservó nada.

    Va en la primera línea y no en la última a propósito: quien contesta que no
    está en el momento de mayor riesgo del flujo —el mensaje anterior decía
    «¿Confirmo?»— y lo que necesita saber es que el turno no salió. Todo lo
    demás puede esperar un renglón.

    Después ofrece qué cambiar con las palabras exactas que reconocen los
    atajos de `estados.py`, para que la respuesta siguiente tampoco dependa del
    clasificador. Nada de lo elegido se borra: la misma regla que el botón
    atrás.
    """
    return "\n".join([
        "Listo, no reservé nada.",
        "",
        "¿Qué querés cambiar? Escribime «el día», «el horario» o «el servicio».",
        "",
        "Si preferís dejarlo acá, escribime «cancelar».",
    ])


def de_nada() -> str:
    """Un gracias o un saludo después de reservar. No es un pedido nuevo.

    Antes esto caía en la apertura: la persona escribía «gracias» y recibía el
    saludo completo con la lista de servicios y un «¿querés sacar un turno?»,
    o sea una pregunta que no hizo, justo después de la que sí. Cerrar también
    es parte de atender.
    """
    return "De nada. Cualquier cosa escribime y lo vemos."


def atascado(url: str | None) -> str:
    """Varios mensajes sin entenderse, y sin que la conversación haya avanzado.

    Es el callejón que dejaba abierto el límite de reintentos: escalar a una
    persona exige que la conversación haya avanzado —si no, cualquiera le hace
    sonar el teléfono al dueño con dos mensajes de basura—, así que quien nunca
    eligió nada giraba sobre el mismo pedido para siempre.

    La salida son las dos puertas que puede abrir sola, SIN avisarle al
    negocio: el link y la posibilidad de pedir una persona con todas las
    letras. Pedirlo explícitamente sí escala, y ahí el aviso está justificado
    porque lo pidió alguien y no lo disparó un contador.
    """
    lineas = ["Me parece que no nos estamos entendiendo, y no te quiero hacer "
              "perder el tiempo."]
    if url:
        lineas += ["", f"Podés sacar el turno vos mismo acá: {url}",
                   "", "O escribime «una persona» y te contesta alguien del local."]
    else:
        lineas += ["", "Escribime «una persona» y te contesta alguien del local."]
    return "\n".join(lineas)


def sesion_reiniciada(apertura_: str) -> str:
    """Pasó demasiado tiempo desde el último mensaje: se arranca de nuevo.

    Sin esto, una conversación abandonada a mitad quedaba congelada en su paso
    para siempre. Alguien que llegó al resumen, se fue, y vuelve tres semanas
    después con un «dale» estaba confirmando la fecha que había elegido
    entonces — que ya pasó.

    Se avisa en vez de reiniciar en silencio: quien vuelve después de un rato
    se acuerda de que había quedado a mitad, y un bot que hace como si nada
    lo obliga a adivinar qué se guardó y qué no.
    """
    return "\n".join([
        "Pasó un rato desde tu último mensaje, así que arranco de nuevo para no "
        "reservarte algo viejo.",
        "",
        apertura_,
    ])


def solo_ubicacion() -> str:
    """Llegó una ubicación compartida y nada de texto.

    Twilio manda las ubicaciones SIN adjunto (`NumMedia=0`), así que no caían
    en la rama de los audios y las fotos: el mensaje quedaba sin texto, el
    webhook lo rechazaba con 400 y la persona no recibía nada. El mismo
    silencio que ya había pasado con las notas de voz.

    Compartir la ubicación es lo que hace mucha gente cuando quiere saber
    dónde queda el local, así que la respuesta ofrece justo eso.
    """
    return "\n".join([
        "Me llegó tu ubicación, pero todavía no la puedo usar. ¿Me escribís qué "
        "necesitás?",
        "",
        "Si querés saber dónde queda el local, preguntame «¿dónde quedan?».",
    ])


def demasiados_mensajes() -> str:
    """Se pasó del tope de mensajes por minuto.

    Sale UNA vez por ventana, no por cada mensaje rechazado: si contestara
    todos, el límite no protegería nada —seguiría saliendo un mensaje de Twilio
    por cada uno— que es justo el gasto que vino a frenar.

    Y no acusa a nadie. Casi siempre no es un ataque: es alguien apurado
    escribiendo de a una palabra por mensaje. El único que va a leer esto en su
    teléfono es esa persona; quien esté golpeando la puerta con un script no lo
    lee.
    """
    return "\n".join([
        "Pará un toque que no llego a leerte 🙂",
        "",
        "Escribime todo junto en un mensaje y lo vemos.",
    ])


def sin_texto() -> str:
    """Llegó un mensaje sin nada legible y sin adjunto que explique qué era.

    Existe para que NINGÚN mensaje entrante quede sin respuesta. Antes, todo lo
    que no fuera texto ni adjunto conocido terminaba en un 400 —o sea, en
    silencio—, y el silencio se lee como "no me dieron bola", nunca como "no me
    entendió".
    """
    return "No me llegó ningún texto. ¿Me lo escribís?"


def fuera_de_alcance() -> str:
    """Sin usos hoy. Se conserva porque nombra un caso que va a volver.

    Es la respuesta para "no sé HACER eso" —cancelar, reprogramar, cobrar la
    seña—, distinta de `sin_dato()`, que es "no TENGO ese dato". Mezclarlas fue
    un bug real: a "¿tienen estacionamiento?" contestaba "eso no lo puedo hacer
    por acá", que suena a que la pregunta estuvo mal hecha.
    """
    return (
        "Eso todavía no lo puedo hacer por acá. "
        "Puedo darte información y sacarte un turno."
    )


def buscador_caido() -> str:
    """No se pudo BUSCAR. Es distinto de que el dato no esté cargado.

    Los dos terminaban en el mismo mensaje —"ese dato no lo tengo cargado"— y
    eso es mentirle a la persona: el negocio sí lo tiene, lo que no anda es la
    búsqueda. Pasó en serio, con la cuota diaria de embeddings agotada.

    Decirlo como es tiene dos ventajas sobre la mentira piadosa: la persona
    entiende que puede volver a intentar en un rato —cosa que con "no lo tengo
    cargado" no haría nunca— y el negocio no queda como si no hubiera cargado
    algo que sí cargó.

    Y se aclara que el turno sí se puede sacar, porque es verdad: lo único que
    depende de la búsqueda son las preguntas.
    """
    return "\n".join([
        "Ahora mismo no puedo consultar esa información. Probá de nuevo en un "
        "rato, o escribime «una persona» y te contesta alguien del local.",
        "",
        "El turno sí te lo puedo sacar igual.",
    ])


def sin_dato(temas: list[str] | None = None) -> str:
    """La pregunta se entendió, pero el negocio no cargó esa respuesta.

    Es distinto de `fuera_de_alcance()`, que significa "no sé hacer eso". Acá
    el bot sí sabe hacerlo y el dato no está. Mezclarlos hacía que a
    "¿tienen estacionamiento?" contestara "eso no lo puedo hacer por acá", que
    suena a que la pregunta estuvo mal formulada.

    Lo que no hace, y es el punto, es adivinar. El conocimiento del negocio se
    carga respondiendo un cuestionario donde responder es opcional, así que
    muchas preguntas van a llegar sin dato. Un bot que rellena los huecos con
    algo verosímil manda a alguien hasta el local confiando en una invención.
    Un "no lo tengo" sale barato; un dato inventado no.
    """
    lineas = ["Ese dato no lo tengo cargado y no quiero mandarte cualquier "
              "cosa. Te lo confirman en el local."]

    # Y de qué SÍ puede hablar. Un "no lo tengo" a secas deja a la persona con
    # un no y sin próximo paso, teniendo el bot cuatro o cinco secciones
    # cargadas que nunca nombra.
    #
    # Los temas son los `##` del archivo que cargó el negocio, tal cual. No los
    # elige ni los redacta un modelo: salen del índice con un filtro por
    # metadato, así que ni cuestan una llamada ni pueden nombrar un tema que no
    # exista.
    if temas:
        lineas += ["", "De lo que sí te puedo contar:"]
        lineas += [f"· {t}" for t in temas]

    lineas += ["", "¿Querés que te saque un turno?"]
    return "\n".join(lineas)


# Los campos de `Entidades` que se pueden REPETIRLE a la persona, y por qué
# sólo esos dos.
#
# `fecha` y `hora` no son texto: son formatos. Una fecha que parsea a `date` es
# una fecha, la haya sacado el modelo de donde sea. Los otros cuatro campos
# —servicio, profesional, nombre, consulta— son prosa que escribió el LLM, y
# devolvérsela a la persona bajo el rótulo "entendí que…" sería presentarle una
# alucinación como comprensión.
#
# Es la misma frontera que sostiene todo el módulo: acá no se imprime nada que
# haya redactado un modelo.
def pista_de(entidades: dict | None, mensaje: str = "") -> str | None:
    """Lo que SÍ se entendió del mensaje, en palabras. O nada.

    El primer escalón de la reparación cuando el bot no entiende. Antes era
    repetir el pedido idéntico —la estrategia peor puntuada de las ocho que
    comparó Ashktorab et al. (CHI 2019)—; nombrar lo que sí llegó y ofrecer las
    opciones fueron las dos que ganaron, porque muestran iniciativa y son
    accionables.

    Devuelve `None` mucho más seguido de lo que devuelve texto, y eso es
    correcto: el default seguro es callarse. Arriesgar una pista de más es
    convertir "no te entendí" en "te entendí mal y te lo afirmo", que es peor.
    """
    ent = entidades or {}

    # Una frase que niega da vuelta el sentido de sus propias entidades:
    # "no quiero el jueves" trae `fecha=jueves` igual que "quiero el jueves".
    # Sin esto, el bot le contestaría "entendí que querés algo para el jueves"
    # a alguien que acaba de decir que el jueves no.
    if hay_negacion(mensaje):
        return None

    partes = []
    fecha = _fecha_si_parsea(ent.get("fecha"))
    if fecha:
        partes.append(f"para el {_dia_corto(fecha)}")
    hora = _hora_si_parsea(ent.get("hora"))
    if hora:
        partes.append(f"a las {hora}")

    return " ".join(partes) if partes else None


def _fecha_si_parsea(valor) -> date | None:
    """'2026-08-27' -> date. 'el jueves que viene' -> None."""
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        return None


def _hora_si_parsea(valor) -> str | None:
    """'15:30' -> '15:30'. '25:99' -> None."""
    try:
        h, m = str(valor).split(":")
        if 0 <= int(h) <= 23 and 0 <= int(m) <= 59:
            return f"{int(h):02d}:{int(m):02d}"
    except (AttributeError, TypeError, ValueError):
        pass
    return None


def no_entendi(reintento: str, pista: str | None = None) -> str:
    """No se entendió. Se dice qué SÍ llegó, se ofrecen las opciones, y a la
    segunda se nombra la salida.

    Los tres bloques salen de la investigación, en ese orden:

      · La EXPLICACIÓN —qué se entendió— y las OPCIONES son las dos estrategias
        de reparación que la gente prefiere. Sin pista queda "No te entendí",
        que es la vieja y la peor, pero es la única honesta cuando de verdad no
        llegó nada.
      · La SALIDA a una persona ya viene DENTRO de `reintento`: desde que cada
        paso la nombra al pie, no hace falta agregarla acá. Antes esto tenía su
        propia escalera —ofrecerla recién al segundo tropiezo— y al ponerla en
        todos los pasos quedó apareciendo dos veces en el mismo mensaje.
    """
    apertura_ = f"Entendí que querés algo {pista}." if pista else "No te entendí."
    return "\n".join([apertura_, "", reintento])


def respuesta_info(texto: str) -> str:
    """Limpia lo que devolvió el RAG para que se lea bien en WhatsApp.

    Los fragmentos vienen de archivos markdown y traían los "##" y los "-" del
    original. WhatsApp no renderiza markdown: la persona veía literalmente
    "## Horarios de atención". Se quitan los marcadores y se deja el texto.

    El LLM no reescribe esto: el dato del negocio sale tal cual está cargado,
    sin que un modelo pueda alterarlo de paso.
    """
    lineas = []
    for cruda in texto.strip().splitlines():
        linea = cruda.strip()
        if not linea or linea.startswith("---"):
            continue
        if linea.startswith("#"):
            # Un encabezado de sección pasa a ser un título simple
            lineas.append(linea.lstrip("# ").strip())
        elif linea.startswith(("- ", "* ")):
            lineas.append("· " + linea[2:].strip())
        else:
            lineas.append(linea)
    return "\n".join(lineas)


def cancelado() -> str:
    """Se abandona el pedido que se estaba armando. NO cancela ningún turno.

    Decía "Listo, cancelé la reserva", y era mentira en el caso que importa.
    Esta intención sólo limpia lo que la persona venía eligiendo: no toca la
    agenda de aturno ni un turno ya confirmado. Alguien que escribía "cancelar"
    pensando en el turno del jueves leía que estaba cancelado, no iba, y el
    lugar le seguía ocupado al negocio. Los dos se enteraban en el mostrador.

    Ahora dice lo que de verdad pasó. Para el turno ya confirmado está
    `no_puedo_cancelar`, y el flujo elige según haya algo en curso o no.

    "Cuando quieras arrancamos de nuevo" suena bien y no sirve: no dice qué
    escribir. La palabra exacta cuesta lo mismo.
    """
    return ("Listo, dejamos el pedido acá. No llegué a reservarte nada.\n\n"
            "Escribime «hola» cuando quieras empezar de nuevo.")


def no_puedo_cancelar(url: str | None) -> str:
    """Pidió cancelar y no hay nada en curso: habla de un turno ya confirmado.

    El bot NO puede cancelarlo: no existe ese camino contra aturno. Lo único
    honesto es decirlo y pasar a alguien que sí pueda. Prometer que se encarga
    es exactamente el daño que este mensaje viene a reparar.

    Tampoco se ofrece el código como salida: aturno lo emite, pero hoy no hay
    ninguna página donde canjearlo, así que mandarlo a "usar tu código" sería
    otra promesa vacía.
    """
    lineas = ["Los turnos ya confirmados no los puedo cancelar yo.",
              "",
              "Escribime «una persona» y te lo cancelan del local."]
    if url:
        lineas += ["", f"Tus turnos también los ves acá: {url}"]
    return "\n".join(lineas)
