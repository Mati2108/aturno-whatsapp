"""
redaccion.py — El guardián: qué puede salir al aire y qué no.

QUÉ REEMPLAZA
-------------
Hasta acá, la garantía de que el bot no inventa era ESTRUCTURAL: el LLM no tenía
ningún camino hacia el texto que lee una persona. Clasificaba a un enum cerrado
y las plantillas escribían. Una alucinación no era improbable: era imposible.

Para que el bot pueda contestar la pregunta que le hicieron en vez de volcar la
ficha entera, esa puerta tiene que abrirse un poco. Y una puerta abierta necesita
un guardián — pero uno VERIFICABLE, no un párrafo en el prompt pidiendo que no
invente. "Se lo pedimos amablemente" no es una garantía.

Este módulo es esa garantía. Recibe un texto candidato, la fuente de donde
debería salir y la pregunta que se hizo, y devuelve el motivo por el que NO
puede salir, o `None` si puede.

CUATRO REGLAS, Y CADA UNA TIENE SU CLASE DE DAÑO
------------------------------------------------
1. `numeros`     — un precio o un horario inventado hace que alguien llegue con
                   la plata equivocada o con el local cerrado.
2. `negacion`    — la fuente dice que NO y la respuesta dice que sí. La más
                   peligrosa, porque no tiene un solo número que revisar.
3. `vocabulario` — traer un concepto que la fuente no menciona ("al lado de la
                   farmacia") manda a alguien a buscar algo que no existe.
4. `empatia`     — medido en contra: la empatía de un bot dispara reactancia
                   psicológica y baja la percepción de competencia
                   (USF, MIS Quarterly). Es el pecado 5 y hoy no se comete.

LO QUE ESTE GUARDIÁN **NO** PUEDE ATRAPAR
-----------------------------------------
Está escrito acá para que nadie lo descubra confiando de más.

Una verificación léxica no entiende el sentido, así que hay inversiones sutiles
que se le escapan — sobre todo cuando la fuente afirma y niega el mismo término
en frases distintas, o cuando el matiz vive en una palabra que sí está en la
fuente. La regla 2 cubre la forma frecuente ("no aceptamos X" → "sí, X"), no
todas las formas posibles.

Por eso el diseño no se apoya sólo acá:
· El camino "no tengo el dato" no redacta nada: elige entre secciones cargadas.
· El prompt pide REORDENAR la fuente, no responder desde el conocimiento propio.
· Cuando este guardián rechaza, sale el texto literal de siempre — o sea que el
  peor caso posible es el bot de antes, nunca uno peor.
· Y el riesgo que queda se MIDE contra `casos_invencion.jsonl` en vez de
  suponerse.
"""

from __future__ import annotations

import re
import unicodedata


def _normalizar(texto: str) -> str:
    """Minúsculas, sin acentos, sin signos. Igual que en `estados.py`."""
    sin = unicodedata.normalize("NFD", (texto or "").lower())
    sin = "".join(c for c in sin if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s:%]", " ", sin)


def _palabras(texto: str) -> list[str]:
    return [p for p in _normalizar(texto).split() if p]


# ══════════════════════════════════════════════════════════════════
#  Regla 1 · Los números
# ══════════════════════════════════════════════════════════════════

def _numeros(texto: str) -> set[str]:
    """Los números de un texto, normalizados para poder compararlos.

    El punto de los miles se saca ANTES de extraer: si no, "$8.000" da los
    números 8 y 0, y "$8.500" también da 8 — o sea que un precio inventado
    pasaría por parecido. Con el punto sacado dan 8000 y 8500, que es lo que
    son.

    Las horas se dejan enteras ("9:00"), porque 9:00 y 9:30 tienen que ser
    números distintos y no "9, 0" y "9, 30".
    """
    limpio = re.sub(r"(?<=\d)[.,](?=\d{3}\b)", "", texto or "")
    return set(re.findall(r"\d+(?::\d+)?", limpio))


# ══════════════════════════════════════════════════════════════════
#  Regla 2 · Las negaciones
# ══════════════════════════════════════════════════════════════════

_NIEGAN = {"no", "ni", "sin", "tampoco", "nunca", "jamas", "ningun", "ninguna",
           "ninguno", "cerrado", "cerrados", "cerrada", "cerradas"}


def _partes(texto: str) -> list[str]:
    """Corta en frases y en cláusulas. Una negación vale en su cláusula.

    "Con débito sí, con crédito no" es una sola oración con dos sentidos
    opuestos. Sin cortar por la coma, la negación del final teñiría también al
    débito, y al revés: leer la oración entera como afirmativa dejaría pasar un
    "sí" sobre algo que la fuente niega.

    EL CORTE VA ANTES DE NORMALIZAR, y no es un detalle de estilo: `_normalizar`
    borra los signos, así que normalizar primero deja la oración entera de una
    pieza y un solo "no" al final tiñe todo lo que vino antes. Con esa versión,
    "Atendemos con turno previo, no por orden de llegada" daba «atendemos» como
    término negado — o sea que el bot no podía volver a decir que atiende.
    """
    crudo = (texto or "").lower()
    partes = re.split(r"[.;:,\n]|\by\b|\bpero\b", crudo)
    return [n for n in (_normalizar(p) for p in partes) if n.strip()]


def _terminos_negados(fuente: str) -> set[str]:
    """Qué niega la fuente, y SÓLO lo que niega sin ambigüedad.

    Un término que aparece en una cláusula afirmativa y en una negativa no
    cuenta. Es el caso de "Aceptamos efectivo... No aceptamos tarjeta de
    crédito": "aceptamos" y "tarjeta" están de los dos lados, así que exigir que
    la respuesta los niegue frenaría la respuesta correcta ("con débito sí").
    Lo que queda es "credito", que es justo el término que importa.
    """
    negados: set[str] = set()
    afirmados: set[str] = set()
    for parte in _partes(fuente):
        palabras = set(parte.split())
        destino = negados if palabras & _NIEGAN else afirmados
        # El andamiaje se descarta: negar "con" o "de" no significa nada, y
        # dejarlos adentro convertía cualquier respuesta que los usara en una
        # inversión. Sólo se niegan las palabras que traen un dato.
        destino |= palabras - _NIEGAN - _ANDAMIAJE
    return negados - afirmados


def _invierte_una_negacion(texto: str, fuente: str) -> str | None:
    negados = _terminos_negados(fuente)
    if not negados:
        return None
    for parte in _partes(texto):
        palabras = set(parte.split())
        if palabras & _NIEGAN:
            continue  # la respuesta también lo niega: está bien
        chocan = palabras & negados
        if chocan:
            # Se reporta la palabra más larga y no la primera alfabéticamente:
            # es la que trae el dato, y por lo tanto la que explica el rechazo.
            cual = max(chocan, key=len)
            return f"negacion: la fuente niega «{cual}» y la respuesta lo afirma"
    return None


# ══════════════════════════════════════════════════════════════════
#  Regla 3 · El vocabulario
# ══════════════════════════════════════════════════════════════════

# Palabras que pueden aparecer sin estar en la fuente porque no aportan ningún
# dato: son el andamiaje con el que se arma cualquier frase en castellano.
#
# La lista es CERRADA y se arma con evidencia: cada palabra de acá salió de una
# respuesta legítima de `casos_invencion.jsonl` que el guardián frenaba de más.
# Agregar una porque "suena inofensiva" es abrirle la puerta a un dato nuevo.
_ANDAMIAJE = frozenset("""
el la los las un una unos unas de del al a en con sin por para y o u ni que
qué cual cuál cuando cuándo donde dónde como cómo si sí no se te me le lo nos
es son esta estan está están hay tiene tienen tenemos tenes tenés tengo
podes podés puede pueden puedo podemos ser estar
mas más menos muy tambien también tampoco solo sólo ya aun aún todavia todavía
esto eso esta ese esa este pero entonces asi así
desde hasta entre sobre bajo antes despues después
vos usted te lo nos su sus mi tu
""".split())

# Verbos y adverbios frecuentes que aparecen conjugados de mil formas. Se
# comparan por prefijo porque "abren" (la pregunta) y "abrimos" (la respuesta)
# son la misma palabra y una comparación exacta las trata como distintas.
_VERBOS_COMUNES = ("abr", "cerr", "llev", "sal", "cost", "val", "ped", "avis",
                   "vien", "ven", "ir", "va", "vas", "vam", "hac", "dec",
                   "quer", "nesit", "necesit", "empez", "termin", "dur",
                   "atend", "trabaj", "cobr", "acept", "lav", "cort", "reserv",
                   "cancel", "program", "present", "qued", "estam", "encontr")

_LARGO_MINIMO = 4   # por debajo de esto, una palabra no alcanza a ser un dato


def _viene_de(palabra: str, vocabulario: set[str]) -> bool:
    """¿Esta palabra sale de ese vocabulario, aunque esté conjugada?

    Se compara por prefijo de cuatro letras. "sabados" y "sabado" son la misma
    palabra; "credito" y "credencial" no lo son, y comparten tres.
    """
    if palabra in vocabulario:
        return True
    raiz = palabra[:_LARGO_MINIMO]
    return any(v.startswith(raiz) or palabra.startswith(v[:_LARGO_MINIMO])
               for v in vocabulario if len(v) >= _LARGO_MINIMO)


def _trae_algo_nuevo(texto: str, fuente: str, pregunta: str) -> str | None:
    de_fuente = set(_palabras(fuente))
    de_pregunta = set(_palabras(pregunta))

    for palabra in _palabras(texto):
        if palabra in _ANDAMIAJE or len(palabra) < _LARGO_MINIMO:
            continue
        if palabra.isdigit() or ":" in palabra or "%" in palabra:
            continue      # los números son cosa de la regla 1
        if _viene_de(palabra, de_fuente):
            continue
        if palabra.startswith(_VERBOS_COMUNES):
            continue

        # Llegó acá: no está en la fuente. Que esté en la pregunta NO alcanza
        # para afirmarla —«¿aceptan Mercado Pago?» no autoriza a contestar «sí,
        # aceptamos Mercado Pago»—: quien pregunta por algo que la fuente no
        # menciona tiene que recibir un "no lo tengo", no una confirmación.
        donde = "en la pregunta pero no en la fuente" if _viene_de(palabra, de_pregunta) \
            else "en ningún lado"
        return f"vocabulario: «{palabra}» está {donde}"
    return None


# ══════════════════════════════════════════════════════════════════
#  Regla 4 · La empatía
# ══════════════════════════════════════════════════════════════════

# No es una cuestión de gusto. Tres experimentos, uno con un LLM real: los
# mensajes empáticos de un chatbot disparan reactancia psicológica y bajan la
# percepción de competencia, de calidad y la satisfacción (USF, MIS Quarterly).
# Con una persona la empatía funciona; del bot hace lo contrario.
#
# La línea es: más humano en la COMPRENSIÓN, no en la cordialidad.
_EMPATIA = (
    "que bueno que", "que lindo que", "me alegra", "que alegria",
    "entiendo como", "entiendo lo que", "entiendo tu", "entiendo que te",
    "comparto tu", "imagino lo", "me imagino como", "se lo que",
    "lamento mucho", "lamento que", "siento mucho", "perdon por las molestias",
    "con gusto", "encantado", "encantada", "es un placer", "un gusto",
    "no te preocupes", "quedate tranquilo", "quedate tranquila",
    "gracias por preguntar", "excelente pregunta", "buena pregunta",
    "estoy aca para", "estoy para ayudarte", "feliz de ayudarte",
)


def _actua_empatia(texto: str) -> str | None:
    limpio = " ".join(_palabras(texto))
    for frase in _EMPATIA:
        if frase in limpio:
            return f"empatia: «{frase}» — la empatía de un bot resta, no suma"
    return None


# ══════════════════════════════════════════════════════════════════

def verificar(texto: str, fuente: str, pregunta: str = "") -> str | None:
    """¿Este texto puede salir al aire? Devuelve el motivo, o `None` si puede.

    Devuelve el MOTIVO y no un booleano a propósito: cuando algo se rechaza hay
    que poder decir qué regla lo frenó, tanto para el log como para el test. Un
    guardián que sólo dice "no" no se puede diagnosticar.

    Ante cualquier duda, rechaza. El costo de rechazar de más es un mensaje algo
    más seco —sale el texto literal de siempre—; el costo de dejar pasar de más
    es que un negocio pierda un cliente por un dato que el bot se inventó.
    """
    if not (texto or "").strip():
        return "vacio: no hay nada que mandar"

    # El orden decide QUÉ motivo se reporta cuando un texto rompe varias reglas
    # a la vez, y por eso los números van primero: "el corte ronda los $8.500"
    # rompe la de vocabulario («ronda») y la de números (8500), y de las dos la
    # que hay que leer en un log es la del precio inventado.
    inventados = _numeros(texto) - _numeros(fuente)
    if inventados:
        return f"numeros: «{sorted(inventados)[0]}» no está en la fuente"

    # Y la empatía va antes que el vocabulario por lo mismo: "¡Qué bueno que
    # preguntes!" también trae palabras nuevas, pero lo que hay que corregir no
    # es el vocabulario, es que el bot esté actuando cordialidad.
    for regla in (
        lambda: _invierte_una_negacion(texto, fuente),
        lambda: _actua_empatia(texto),
        lambda: _trae_algo_nuevo(texto, fuente, pregunta),
    ):
        motivo = regla()
        if motivo:
            return motivo

    return None
