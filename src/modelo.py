"""
modelo.py — De dónde sale el LLM.

Un solo lugar decide el proveedor, leyendo PROVIDER del .env. El resto del
sistema pide `construir_modelo()` y no sabe ni le importa cuál es.

Eso no es prolijidad: es lo que permite desarrollar gratis con Ollama local y
salir a producción con Claude cambiando una variable de entorno — y lo que
después habilita vender una versión self-hosted a un cliente que no quiere que
los datos de sus pacientes salgan de su servidor. El mismo código, otro `.env`.
"""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel

from src.config import config

logger = logging.getLogger("pipeline.modelo")

# temperature=0: en un bot que toma turnos no querés creatividad. El mismo
# mensaje tiene que producir la misma decisión.
TEMPERATURA = 0


def hay_credencial(proveedor: str) -> bool:
    """¿Este proveedor tiene con qué autenticarse?

    Se pregunta ANTES de armar la cadena de respaldo, porque un proveedor
    declarado sin clave no es un respaldo: es una segunda forma de fallar con
    apariencia de cobertura. Ollama no lleva clave —corre local— así que
    siempre cuenta como disponible.

    No dice si la credencial SIRVE, sólo si está. Que sirva se descubre
    intentando, que es lo que hace `/salud`.
    """
    cfg = config()
    return {
        "ollama": True,
        "anthropic": bool(cfg.anthropic_api_key),
        "openai": bool(cfg.openai_api_key),
        "gemini": bool(cfg.gemini_api_key),
    }.get(proveedor, False)


def construir_modelo(proveedor: str | None = None,
                     max_tokens: int | None = None) -> BaseChatModel:
    """Devuelve el chat model del proveedor pedido, con tool calling.

    Sin argumento usa el de la config, que es el caso normal. El parámetro
    existe para poder armar la cadena de respaldo con otro proveedor, sin tocar
    la variable de entorno ni ningún estado global.

    `max_tokens` acota la respuesta. Cada proveedor le pone otro nombre a lo
    mismo —`num_predict`, `max_tokens`, `max_output_tokens`— y traducirlo es
    justamente el trabajo de este módulo: quien llama pide "no más de N tokens"
    y no tiene que saber con quién está hablando.

    Lo usa el chequeo de salud, que sólo necesita saber si el proveedor
    CONTESTA. Sin acotarlo, el modelo respondía un párrafo entero a un punto y
    esa respuesta —que nadie lee— era el 83% de lo que costaba el chequeo.
    """
    cfg = config()
    proveedor = proveedor or cfg.provider

    if proveedor == "ollama":
        from langchain_ollama import ChatOllama

        # Local: gratis e ilimitado, ideal para iterar. El tool calling de un
        # modelo chico es más flojo que el de un modelo grande — por eso el
        # sistema es conmutable y el demo final sale por Claude.
        return ChatOllama(model=cfg.ollama_modelo, temperature=TEMPERATURA,
                          **({"num_predict": max_tokens} if max_tokens else {}))

    if proveedor == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=cfg.anthropic_modelo,
            temperature=TEMPERATURA,
            max_tokens=max_tokens or 1024,
            api_key=_key("ANTHROPIC_API_KEY", cfg.anthropic_api_key),
        )

    if proveedor == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=cfg.openai_modelo,
            temperature=TEMPERATURA,
            api_key=_key("OPENAI_API_KEY", cfg.openai_api_key),
            **({"max_tokens": max_tokens} if max_tokens else {}),
        )

    if proveedor == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=cfg.gemini_modelo,
            temperature=TEMPERATURA,
            google_api_key=_key("GEMINI_API_KEY", cfg.gemini_api_key),
            **({"max_output_tokens": max_tokens} if max_tokens else {}),
        )

    raise ValueError(
        f"PROVIDER desconocido: {proveedor!r}. "
        "Usá: ollama | anthropic | openai | gemini"
    )


def _key(nombre: str, valor: str) -> str:
    """Falla temprano y con instrucciones, no con un traceback del SDK."""
    if not valor:
        raise ValueError(
            f"Falta {nombre} en el .env. Completala, o poné PROVIDER=ollama "
            "para desarrollar sin credenciales."
        )
    return valor
