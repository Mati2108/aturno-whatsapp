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

    # La pregunta es abierta a propósito. Este es el único momento de la
    # conversación donde no hay una lista que responder, y por eso es por donde
    # entra todo lo que no es reservar: precios, dónde quedan, si hay lugar
    # mañana. Un "elegí una opción" acá manda a la gente a contestar un número
    # al azar para poder seguir, y después preguntar lo que quería preguntar.
    bloques.append("¿Querés sacar un turno o tenés alguna pregunta?\n"
                   "Escribime lo que necesites.")

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
    lineas = ["Elegí el servicio:", ""]
    for i, s in enumerate(servicios, 1):
        marca = " ✓" if preseleccion and s.id == preseleccion else ""
        lineas.append(f"{i}. {s.nombre} — {s.duracion_minutos} min — {_plata(s.precio)}{marca}")
    if preseleccion:
        lineas += ["", "Confirmá con el número o elegí otro."]
    else:
        lineas += ["", "Respondé con el número."]
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
               "Respondé con el número o el nombre."]
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
            lineas.append(f"   {_dia_corto(d.fecha)} — {_POR_QUE[d.motivo]}")
        else:
            numero += 1
            plural = "turno" if d.libres == 1 else "turnos"
            lineas.append(f"{numero}. {_dia_corto(d.fecha)} — {d.libres} {plural}")

    if numero == 0:
        return "No me queda ningún día con lugar en las próximas semanas."

    # Los días sin lugar no llevan número, así que la numeración salta. Decir
    # solo "respondé con el número" delante de una lista con huecos parece un
    # error del bot; nombrar el día siempre funciona y es lo que la gente hace.
    lineas += ["", "Respondé con el número o el día (por ejemplo, «el jueves»)."]
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
    mostrados = horarios[:maximo]
    lineas = [f"Horarios para el {_dia_corto(dia)}:", ""]
    for i, h in enumerate(mostrados, 1):
        lineas.append(f"{i}. {h:%H:%M}")
    if len(horarios) > maximo:
        lineas += ["", f"Hay {len(horarios) - maximo} horarios más tarde. Pedime 'más'."]
    lineas += ["", "Respondé con el número."]
    return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════════
# T6 · Resumen y T7 · Nombre
# ══════════════════════════════════════════════════════════════════

def resumen(servicio: str, staff: str | None, dia: date, hora) -> str:
    lineas = ["Repasemos:", "", f"Servicio: {servicio}"]
    if staff:
        lineas.append(f"Con: {staff}")
    lineas += [
        f"Día: {_dia_corto(dia)}",
        f"Hora: {hora:%H:%M}",
        "",
        "¿Confirmo? Respondé SÍ o NO.",
    ]
    return "\n".join(lineas)


def pedir_nombre() -> str:
    return "Para cerrarlo necesito tu nombre y apellido."


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
        "Si necesitás cancelar, avisame con tiempo.",
        "Y si querés hablar con alguien del local, escribime «hablar con una persona».",
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


def no_disponible(motivo: MotivoNoDisponible, alternativas: list[Alternativa]) -> str:
    """El motivo SIEMPRE, nunca un "no hay" pelado.

    Decir solo "no hay" obliga a la persona a adivinar qué probar. El motivo
    más una alternativa concreta convierte un rechazo en el próximo paso.
    """
    lineas = [_MOTIVOS.get(motivo, "No puedo dar ese turno.")]
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
        "Si preferís, lo cerramos por acá y no hace falta que entres.",
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


def fuera_de_alcance() -> str:
    return (
        "Eso todavía no lo puedo hacer por acá. "
        "Puedo darte información y sacarte un turno."
    )


def sin_dato() -> str:
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
    return (
        "Ese dato no lo tengo cargado y no quiero mandarte cualquier cosa. "
        "Te lo confirman en el local.\n\n"
        "¿Querés que te saque un turno?"
    )


def no_entendi(reintento: str) -> str:
    """Repite el CTA del estado actual en vez de dejar a la persona colgada."""
    return "\n".join(["No te entendí.", "", reintento])


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
    return "Listo, cancelé la reserva. Cuando quieras arrancamos de nuevo."
