"""
revisar_negocio.py — ¿Este negocio está listo para prender el bot?

POR QUÉ EXISTE
--------------
El bot no inventa nada: todo lo que dice sale de cómo está configurado el
negocio en aturno. Un horario mal cargado, un profesional sin agenda propia o
una zona horaria equivocada no rompen la web —que es más indulgente— pero sí
arruinan la conversación, y el negocio se entera por un cliente enojado.

Esto revisa la configuración ANTES de prenderlo, y dice qué hay que arreglar.
Es el chequeo que en el producto terminado va a correr el panel cuando alguien
active el asistente.

    python revisar_negocio.py <slug>

No escribe nada: solo lee la API pública y los archivos de este repo.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

from src.aturno.api import AturnoAPI
from src.config import config
from src.fechas import hoy

DIAS = {"monday": "lunes", "tuesday": "martes", "wednesday": "miércoles",
        "thursday": "jueves", "friday": "viernes", "saturday": "sábado",
        "sunday": "domingo"}

ROJO, AMBAR, VERDE, GRIS, NEGRITA, FIN = (
    "\033[31m", "\033[33m", "\033[32m", "\033[90m", "\033[1m", "\033[0m")

# Husos válidos para un negocio argentino. No es una lista de países: es que
# de acá salen los eventos de Google Calendar y los recordatorios, y un huso
# equivocado los manda con horas de diferencia sin que nada falle.
HUSOS_AR = {"America/Argentina/Buenos_Aires", "America/Buenos_Aires",
            "America/Argentina/Cordoba", "America/Argentina/Mendoza",
            "America/Argentina/Salta", "America/Argentina/Tucuman"}


class Informe:
    def __init__(self) -> None:
        self.rompe: list[tuple[str, str]] = []
        self.empeora: list[tuple[str, str]] = []
        self.bien: list[str] = []

    def mal(self, que: str, como_se_arregla: str) -> None:
        self.rompe.append((que, como_se_arregla))

    def flojo(self, que: str, como_se_arregla: str) -> None:
        self.empeora.append((que, como_se_arregla))

    def ok(self, que: str) -> None:
        self.bien.append(que)


def _minutos(hhmm: str) -> int:
    h, m = hhmm.split(":")[:2]
    return int(h) * 60 + int(m)


def _cubre(tramos_a: list[dict], tramos_b: list[dict]) -> bool:
    """¿Algún tramo de A se pisa con alguno de B?"""
    return any(max(_minutos(a["start"]), _minutos(b["start"]))
               < min(_minutos(a["end"]), _minutos(b["end"]))
               for a in tramos_a for b in tramos_b)


async def revisar(slug: str) -> Informe:
    api = AturnoAPI(config().aturno_api_url)
    inf = Informe()
    try:
        doc = await api._negocio(slug)  # noqa: SLF001

        # ---- identidad ----
        nombre = (doc.get("businessInfo") or {}).get("name") or doc.get("name")
        if not nombre:
            inf.mal("El negocio no tiene nombre",
                    "El bot se presenta con él en cada saludo. Cargalo en Configuración.")
        else:
            inf.ok(f"Se presenta como «{nombre}»")

        huso = doc.get("timeZone")
        if huso not in HUSOS_AR:
            inf.mal(f"La zona horaria es «{huso}»",
                    "De ahí salen los recordatorios y los eventos de Google Calendar: "
                    "van a llegar con horas de diferencia. Ponela en Buenos Aires.")
        else:
            inf.ok(f"Zona horaria correcta ({huso})")

        # ---- por dónde hablar con una persona ----
        contacto = await api.contacto(slug)
        if not contacto.hay_algo():
            inf.flojo("No hay teléfono ni email cargado",
                      "Cuando alguien pide hablar con una persona, el bot no tiene "
                      "qué pasarle.")
        else:
            inf.ok("Hay contacto para derivar a una persona")
        if not contacto.direccion:
            inf.flojo("No hay dirección",
                      "«¿Dónde quedan?» es de las tres preguntas más comunes.")

        # ---- servicios ----
        servicios = await api.listar_servicios(slug)
        if not servicios:
            inf.mal("No hay ningún servicio cargado",
                    "El bot no tiene nada que ofrecer. Es lo primero.")
        else:
            inf.ok(f"{len(servicios)} servicio(s) para ofrecer")
            for s in servicios:
                if s.precio <= 0:
                    inf.flojo(f"«{s.nombre}» no tiene precio",
                              "El bot lo muestra en la primera pantalla; sin precio "
                              "la gente pregunta igual y hay que contestar a mano.")
            crudos = {str(s.get("id")): s for s in (doc.get("services") or [])}
            for s in servicios:
                if (crudos.get(s.id) or {}).get("requiresDeposit"):
                    inf.mal(f"«{s.nombre}» pide seña y el bot todavía no la cobra",
                            "Por WhatsApp se reserva sin cobrarla; por la web sí se "
                            "cobra. Hasta que esté, sacale la seña o no lo ofrezcas "
                            "por WhatsApp.")

        # ---- equipo ----
        personal = doc.get("staff") or []
        activos = [p for p in personal if p.get("active")]
        if not activos:
            inf.flojo("No hay personal activo",
                      "El bot saltea ese paso y asigna solo. Está bien si atiende "
                      "una sola persona.")
        else:
            inf.ok(f"{len(activos)} profesional(es) activo(s)")

        agenda = doc.get("schedule") or {}
        for p in activos:
            propio = p.get("schedule") or {}
            dias_propios = [d for d in DIAS if (propio.get(d) or {}).get("enabled")
                            and (propio.get(d) or {}).get("ranges")]
            if not dias_propios:
                inf.mal(f"«{p.get('name')}» no tiene horario propio",
                        "aturno lo trata como que no atiende ningún día, y el bot "
                        "hace lo mismo: elegirla no va a dar ningún horario.")
                continue
            muertos = []
            for d in dias_propios:
                del_negocio = agenda.get(d) or {}
                if not del_negocio.get("enabled") or not del_negocio.get("ranges"):
                    muertos.append(DIAS[d])
                elif not _cubre(propio[d]["ranges"], del_negocio["ranges"]):
                    muertos.append(DIAS[d])
            if muertos:
                inf.flojo(f"«{p.get('name')}» tiene horario en días que el local no abre",
                          f"({', '.join(muertos)}) — esas horas no se ofrecen nunca. "
                          "No rompe nada, pero indica que la agenda quedó a medias.")

        # ---- ¿hay turnos de verdad en la semana? ----
        if servicios:
            cupos = await api.dias_con_cupo(slug, servicios[0].id, hoy(), 7)
            con_lugar = [c for c in cupos if c.abierto and c.libres > 0]
            if not con_lugar:
                inf.mal("No hay ni un horario libre en los próximos 7 días",
                        "Puede ser agenda llena, o el horario del local y el del "
                        "personal no se pisan en ningún momento.")
            else:
                total = sum(c.libres for c in con_lugar)
                inf.ok(f"{total} horarios libres en los próximos 7 días")

        # ---- lo que el bot puede contestar ----
        archivo = Path(__file__).parent / "datos" / f"{slug}.md"
        if not archivo.exists():
            inf.flojo(f"No existe datos/{slug}.md",
                      "Sin eso, a cualquier pregunta del negocio el bot contesta "
                      "que no tiene el dato cargado.")
        else:
            texto = archivo.read_text(encoding="utf-8")
            # Solo los ENCABEZADOS, no el archivo entero. Buscar la palabra en
            # cualquier lado daba por respondido lo que no lo estaba: el
            # párrafo que aclara "estacionamiento y medios de pago todavía no
            # están cargados" contiene las dos palabras, así que el chequeo se
            # daba por satisfecho leyendo la advertencia de que faltaban.
            # Además el RAG recupera por sección: si el tema no es una sección,
            # para el bot no existe.
            titulos = " ".join(l[3:].lower() for l in texto.splitlines()
                               if l.startswith("## "))
            n_secciones = sum(1 for l in texto.splitlines() if l.startswith("## "))
            inf.ok(f"{n_secciones} secciones de conocimiento cargadas")
            if nombre and nombre.lower() not in texto.lower():
                inf.flojo(f"datos/{slug}.md no menciona «{nombre}»",
                          "Probablemente quedó del nombre anterior. Conviene "
                          "regenerarlo.")

            # Los grupos del cuestionario, con las palabras que los delatan.
            grupos = {
                "cómo llegar": ("llegar", "estacion", "colectivo", "subte", "dónde"),
                "antes de venir": ("traer", "antes de venir", "tolerancia", "llegar tarde"),
                "pagos": ("pago", "pagar", "tarjeta", "efectivo", "obra social"),
                "turnos": ("cancel", "reprogram", "anticipación", "sin turno"),
                "el lugar": ("wifi", "baño", "mascota", "sala de espera"),
            }
            sin_responder = [g for g, palabras in grupos.items()
                             if not any(p in titulos for p in palabras)]
            if sin_responder:
                inf.flojo(f"El bot no puede contestar sobre: {', '.join(sin_responder)}",
                          "Cada grupo sin responder es una pregunta que la persona "
                          "va a hacer igual y alguien va a tener que contestar a "
                          "mano. Están en datos/CUESTIONARIO.md.")
        return inf
    finally:
        await api.cerrar()


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    slug = sys.argv[1]

    print(f"\n{NEGRITA}{'═' * 70}{FIN}")
    print(f"{NEGRITA}  ¿ESTÁ LISTO «{slug}» PARA PRENDER EL BOT?{FIN}")
    print(f"{'═' * 70}")

    inf = await revisar(slug)

    if inf.rompe:
        print(f"\n{ROJO}{NEGRITA}  HAY QUE ARREGLAR ESTO ANTES{FIN}")
        for que, como in inf.rompe:
            print(f"\n  {ROJO}✗{FIN} {NEGRITA}{que}{FIN}")
            print(f"      {GRIS}{como}{FIN}")

    if inf.empeora:
        print(f"\n{AMBAR}{NEGRITA}  ANDA IGUAL, PERO SE NOTA{FIN}")
        for que, como in inf.empeora:
            print(f"\n  {AMBAR}!{FIN} {que}")
            print(f"      {GRIS}{como}{FIN}")

    print(f"\n{VERDE}{NEGRITA}  EN ORDEN{FIN}")
    for que in inf.bien:
        print(f"  {VERDE}✓{FIN} {que}")

    print(f"\n{'═' * 70}")
    if inf.rompe:
        print(f"{ROJO}{NEGRITA}  NO LO PRENDAS TODAVÍA — {len(inf.rompe)} cosa(s) que arreglar{FIN}")
    elif inf.empeora:
        print(f"{AMBAR}{NEGRITA}  SE PUEDE PRENDER — {len(inf.empeora)} detalle(s) para mejorar{FIN}")
    else:
        print(f"{VERDE}{NEGRITA}  LISTO PARA PRENDER{FIN}")
    print(f"{'═' * 70}\n")
    return 1 if inf.rompe else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
