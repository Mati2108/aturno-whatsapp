"""
config.py — Toda la configuración, validada y en un solo lugar.

El Capstone pide cero hardcoding: nombres de modelo, URLs y credenciales salen
de variables de entorno. Pydantic las valida al arrancar, así un `.env` mal
puesto falla en el import y no tres capas adentro, en medio de un turno.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from src.schemas import Tenant

RAIZ = Path(__file__).resolve().parent.parent


class Config(BaseSettings):
    """Se completa desde el .env; los defaults son para desarrollo."""

    model_config = SettingsConfigDict(
        env_file=RAIZ / ".env",  # ruta absoluta: no depende de desde dónde corras
        extra="ignore",
    )

    # ---- LLM ----
    # Ningún nombre de modelo hardcodeado en el código: todos salen de acá.
    provider: str = "ollama"
    ollama_modelo: str = "qwen3:8b"
    anthropic_modelo: str = "claude-haiku-4-5"
    openai_modelo: str = "gpt-4o-mini"
    gemini_modelo: str = "gemini-3.1-flash-lite"

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""

    # ---- Persistencia ----
    # El checkpointer de LangGraph. Postgres y no SQLite desde el arranque:
    # SQLite bloquea con escrituras concurrentes y no sirve con más de una
    # instancia, que es exactamente lo que pasa al desplegar.
    database_url: str = "postgresql://localhost:5432/aturno_whatsapp"

    # ---- Twilio ----
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = "+14155238886"

    # La URL pública del túnel. Se necesita para validar la firma de Twilio:
    # la firma se calcula sobre la URL EXACTA que Twilio llamó, y detrás de un
    # túnel el server ve "localhost", no la URL real.
    public_url: str = ""

    # Apagar la validación de firma solo para probar con curl desde la máquina.
    # En producción va siempre en True: el webhook es una URL pública y sin
    # firma cualquiera puede postear turnos falsos.
    validar_firma: bool = True

    # ---- Embeddings ----
    # "api" (Gemini, sin memoria) | "local" (fastembed, +805 MB, sin red)
    embeddings_modo: str = "api"

    # ---- Observabilidad ----
    phoenix_habilitado: bool = False
    phoenix_endpoint: str = "http://localhost:6006/v1/traces"

    # ---- Backend de aturno ----
    aturno_modo: str = "doble"  # "doble" (en memoria) | "api" (backend real)
    aturno_api_url: str = "http://localhost:3001"


@lru_cache
def config() -> Config:
    """Una sola instancia, cacheada. Importar esto, no construir Config()."""
    return Config()


# ---------- Registro de negocios ----------
# En producción esto sale de Firestore: cada negocio de aturno tiene su propio
# número de WhatsApp y el webhook rutea por el campo `To`. Con el Sandbox de
# Twilio hay un solo número, así que por ahora el mapa tiene una entrada.
TENANTS: dict[str, Tenant] = {
    "+14155238886": Tenant(
        business_id="demo-peluqueria",
        slug="demo-peluqueria",
        nombre="Peluquería Demo",
        numero_whatsapp="+14155238886",
    ),
}


def tenant_por_numero(numero: str) -> Tenant | None:
    """Resuelve a qué negocio le escribieron, según el número que recibió.

    Devolver None y no explotar es a propósito: un mensaje a un número que no
    administramos es un caso normal, no un error del sistema.
    """
    return TENANTS.get(numero)
