"""
observabilidad.py — Trazas de ejecución con Arize Phoenix.

QUÉ RESUELVE
------------
Sin trazas, cuando una conversación sale mal solo se ve el resultado: el bot
contestó cualquier cosa. No se ve si el clasificador entendió mal, si el RAG
trajo el fragmento equivocado, si aturno rechazó el turno, ni cuánto costó.

Phoenix instrumenta LangChain y LangGraph por debajo: cada llamada al modelo,
cada nodo del grafo y cada búsqueda del RAG quedan como un span anidado, con
sus tokens y su latencia. Es lo que convierte "el bot falló" en "el
clasificador devolvió elegir_dia con la fecha vacía".

FALLA BLANDA, SIEMPRE
---------------------
Si Phoenix no está levantado, esto avisa y sigue. Un sistema de observabilidad
que tira abajo el servicio que observa es peor que no tenerlo: el bot tiene
que contestar aunque el trazado esté caído.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("pipeline.observabilidad")

_activo = False


def trazado_activo() -> bool:
    return _activo


def configurar_trazas(nombre_proyecto: str = "aturno-whatsapp") -> bool:
    """Engancha el trazado. Devuelve si quedó activo.

    Se controla con dos variables de entorno:
        PHOENIX_HABILITADO=true         prende el trazado
        PHOENIX_ENDPOINT=http://...     dónde está el colector

    Apagado por defecto: los tests no deberían necesitar una dependencia
    externa corriendo para pasar.
    """
    global _activo

    if os.getenv("PHOENIX_HABILITADO", "false").lower() not in ("1", "true", "si", "sí"):
        logger.info("Trazado apagado (PHOENIX_HABILITADO no está en true)")
        return False

    endpoint = os.getenv("PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")

    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from phoenix.otel import register

        proveedor = register(
            project_name=nombre_proyecto,
            endpoint=endpoint,
            auto_instrument=False,   # instrumentamos explícito, abajo
            batch=True,              # no bloquea la respuesta al usuario
        )
        # Una sola instrumentación cubre LangChain Y LangGraph: el grafo se
        # ejecuta sobre runnables de LangChain, así que cada nodo aparece como
        # un span anidado sin tener que instrumentarlo a mano.
        LangChainInstrumentor().instrument(tracer_provider=proveedor)

        _activo = True
        logger.info("Trazado activo → %s (proyecto: %s)", endpoint, nombre_proyecto)
        return True

    except Exception as e:  # noqa: BLE001 — nunca tirar abajo el servicio
        logger.warning(
            "No se pudo activar el trazado (%s). El bot sigue funcionando sin él.", e
        )
        return False


def atributos_de_conversacion(business_id: str, telefono: str, estado: str) -> dict:
    """Metadatos para poder filtrar las trazas después.

    Sin esto, en Phoenix se ven cien conversaciones iguales. Con el negocio y
    el estado se puede preguntar "¿qué pasó en todas las que se trabaron
    eligiendo el día?", que es la pregunta que uno realmente se hace.

    El teléfono va ofuscado: es un dato personal y no hace falta completo para
    seguir una conversación.
    """
    return {
        "aturno.business_id": business_id,
        "aturno.telefono": telefono[-4:] if len(telefono) > 4 else "····",
        "aturno.estado": estado,
    }
