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

from src.schemas import Alternativa, DiaConCupo, MotivoNoDisponible, Profesional, Servicio

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
    lineas = [
        f"{saludo} Soy el asistente de {negocio}.",
        "",
        "Esto es lo que hacemos:",
    ]
    for i, s in enumerate(servicios, 1):
        lineas.append(f"{i}. {s.nombre} — {s.duracion_minutos} min — {_plata(s.precio)}")
    lineas += ["", "Respondé con el número del servicio que querés."]
    return "\n".join(lineas)


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

def lista_staff(personas: list[Profesional], servicio: str) -> str:
    lineas = [f"{servicio}. ¿Con quién lo querés?", ""]
    for i, p in enumerate(personas, 1):
        lineas.append(f"{i}. {p.nombre}")
    lineas += [f"{len(personas) + 1}. Me da igual", "", "Respondé con el número."]
    return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════════
# T4 · Días
# ══════════════════════════════════════════════════════════════════

def selector_dias(dias: list[DiaConCupo], hoy: date | None = None) -> str:
    """Los próximos N días con su cupo, agrupados por semana.

    El encabezado y el separador existen porque "Lunes 17" y "Lunes 24" se
    parecen demasiado en una lista corrida: sin la marca de semana la gente
    elige el lunes equivocado.

    La cantidad de días la define el negocio (`dias_a_mostrar`), no esta
    función: siete es un default, no una regla.
    """
    hoy = hoy or date.today()
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
        if not d.abierto:
            lineas.append(f"   {_dia_corto(d.fecha)} — cerrado")
        elif d.libres == 0:
            lineas.append(f"   {_dia_corto(d.fecha)} — completo")
        else:
            numero += 1
            plural = "turno" if d.libres == 1 else "turnos"
            lineas.append(f"{numero}. {_dia_corto(d.fecha)} — {d.libres} {plural}")

    if numero == 0:
        return "No me queda ningún día con lugar en las próximas semanas."

    lineas += ["", "Respondé con el número del día."]
    return "\n".join(lineas)


def dias_elegibles(dias: list[DiaConCupo]) -> list[DiaConCupo]:
    """Los días que sí tienen número, en el mismo orden que los muestra T4.

    La máquina de estados resuelve "3" contra esta lista. Tiene que salir del
    mismo módulo que la plantilla: si el filtro y el renderizado se separan,
    en algún momento se desincronizan y el número 3 deja de ser el que la
    persona vio en pantalla.
    """
    return [d for d in dias if d.abierto and d.libres > 0]


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
    """Nunca un stack trace ni un JSON. Esto es lo que ve una persona."""
    return "Uy, se me complicó procesar eso. ¿Probamos de nuevo en un minuto?"


def fuera_de_alcance() -> str:
    return (
        "Eso todavía no lo puedo hacer por acá. "
        "Puedo darte información y sacarte un turno."
    )


def no_entendi(reintento: str) -> str:
    """Repite el CTA del estado actual en vez de dejar a la persona colgada."""
    return "\n".join(["No te entendí.", "", reintento])


def respuesta_info(texto: str) -> str:
    """Envuelve lo que devolvió el RAG. El LLM no reescribe: se muestra tal cual."""
    return texto.strip()


def cancelado() -> str:
    return "Listo, cancelé la reserva. Cuando quieras arrancamos de nuevo."
