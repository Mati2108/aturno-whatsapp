"""
indice.py — El RAG, con aislamiento entre negocios por diseño.

EL REQUISITO QUE MANDA
----------------------
Un cliente de la peluquería no puede recibir jamás un dato del consultorio.
No es una preferencia: es lo que hace vendible un SaaS multi-tenant. Si un
negocio ve los precios de otro, el producto no se puede ofrecer.

Por eso el filtro por `business_id` NO es un parámetro opcional de la búsqueda.
Está en el constructor de `Recuperador`, así que no existe forma de escribir
una consulta sin él: para buscar hay que decir primero de qué negocio sos.
La alternativa —un `filtro=` que se puede olvidar— es exactamente el bug que
este diseño hace imposible.

FRAGMENTACIÓN POR SECCIÓN
-------------------------
Cortamos por encabezado `##`, no cada N caracteres. Una sección de un negocio
responde una pregunta completa ("¿cuánto sale el corte?", "¿a qué hora abren?"),
y partirla al medio es lo que produce respuestas a medias: el clásico caso de
separar una cláusula de su excepción.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import MarkdownHeaderTextSplitter

logger = logging.getLogger("pipeline.rag")

RAIZ = Path(__file__).resolve().parent.parent.parent
CARPETA_DATOS = RAIZ / "datos"
CARPETA_INDICE = RAIZ / "chroma"

# Embeddings locales, en proceso: gratis, ilimitados y los datos del negocio
# no salen del servidor — el mismo argumento de venta del plan self-hosted.
#
# Se eligió midiendo, no por gusto (ver test_recuperacion.py, 8 preguntas
# reales en español):
#
#   nomic-embed-text   4/8 top-1  — fallaba en "cuánto cuesta un corte", que
#                                   es LA pregunta más común. Entrenado sobre
#                                   todo en inglés; los prefijos de tarea que
#                                   pide su doc no cambiaron nada.
#   bge-m3             8/8 top-1  — pero corre sobre Ollama, un demonio de
#                                   1,2 GB. Inviable en un contenedor chico.
#   MiniLM multilingüe 8/8 top-1  — misma puntuación, 0,22 GB, y corre EN
#                                   PROCESO. Sin demonio que desplegar.
#
#   Gemini API         8/8 top-1  — misma puntuación otra vez, y CERO memoria
#                                   en el proceso.
#
# Y ahí apareció el dato que decidió el default. Medido en este proyecto:
#
#     proceso con el modelo local cargado ...... 962 MB
#     proceso usando embeddings por API ........ 173 MB
#
# Los 805 MB de diferencia son el runtime de ONNX; limitarle hilos y arenas no
# los baja. Un plan chico de hosting tiene 512 MB, así que el modelo local
# directamente no entra — y pagar el doble de RAM cuesta más que el resto del
# sistema junto.
#
# Por eso el default es la API y lo local queda como opción: es el mismo
# patrón que el LLM. Un consultorio que no quiere que los datos salgan de su
# servidor cambia EMBEDDINGS_MODO=local y paga esa memoria a propósito.
#
# Ocho preguntas no prueban equivalencia; alcanzan para elegir entre opciones
# que puntúan igual.
MODELO_LOCAL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODELO_API = "models/gemini-embedding-001"

# Debajo de esto, el fragmento no responde la pregunta y no se usa.
#
# Medido sobre el negocio `aturno` con doce preguntas, seis que su documento
# responde y seis que no (estacionamiento, tarjeta, mascotas, wifi, obra
# social, y una directamente de otro rubro):
#
#     con respuesta en el documento .... mejor puntaje 0.621 a 0.729
#     sin respuesta en el documento .... mejor puntaje 0.489 a 0.583
#
# 0.60 cae en el hueco y separa los doce casos. El más cercano por abajo es
# "¿atienden obra social?" con 0.583, que es justamente una pregunta que el
# negocio debería contestar en el cuestionario: cuando la conteste, va a subir
# por encima del umbral sola.
#
# Doce preguntas sobre un negocio no prueban nada en general; alcanzan para
# elegir un número y para que se pueda volver a medir cuando cambie el modelo
# de embeddings. Si se cambia el modelo, hay que rehacer esta medición: los
# puntajes no son comparables entre modelos.
UMBRAL = 0.60

# Cuántos fragmentos como máximo entran en una respuesta. En la medición, para
# toda pregunta con respuesta el segundo fragmento ya quedaba en 0.588 o menos,
# o sea que el umbral por sí solo devuelve uno. El tope está para el caso en
# que una respuesta quede repartida en dos secciones, y para que nunca vuelva a
# salir el chorizo de tres bloques que recibía la persona antes.
MAX_FRAGMENTOS = 2


class EmbeddingsLocales(Embeddings):
    """Adaptador mínimo sobre fastembed (ONNX), sin servicios externos.

    No usamos el wrapper de langchain-community porque ese paquete está en
    proceso de discontinuación. Son quince líneas: no vale la pena arrastrar
    una dependencia sin mantenimiento por ellas.

    El modelo se carga una sola vez, la primera vez que se usa: cargarlo por
    consulta agregaría segundos a cada mensaje.
    """

    def __init__(self, modelo: str = MODELO_LOCAL) -> None:
        from fastembed import TextEmbedding

        self._modelo = TextEmbedding(model_name=modelo)

    def embed_documents(self, textos: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._modelo.embed(textos)]

    def embed_query(self, texto: str) -> list[float]:
        return next(iter(self._modelo.embed([texto]))).tolist()


_cache: Embeddings | None = None


def _embeddings() -> Embeddings:
    """El proveedor configurado. Una sola instancia por proceso.

    EMBEDDINGS_MODO=api    (default) Gemini. Sin modelo en memoria.
    EMBEDDINGS_MODO=local            fastembed en proceso, +805 MB, sin red.
    """
    global _cache
    if _cache is not None:
        return _cache

    from src.config import config
    cfg = config()

    if cfg.embeddings_modo == "local":
        _cache = EmbeddingsLocales()
    else:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        _cache = GoogleGenerativeAIEmbeddings(
            model=MODELO_API, google_api_key=cfg.gemini_api_key
        )
    return _cache


def modelo_en_uso() -> str:
    from src.config import config

    return MODELO_LOCAL if config().embeddings_modo == "local" else MODELO_API


def _fragmentar(texto: str, business_id: str) -> list[Document]:
    """Parte un documento por encabezados y le estampa el negocio a cada trozo.

    El `business_id` va en los metadatos de CADA fragmento, no del documento:
    Chroma filtra por metadatos del fragmento, así que si se estampa en el nivel
    equivocado el filtro no encuentra nada — o peor, encuentra de más.
    """
    partidor = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "titulo"), ("##", "seccion")],
        strip_headers=False,
    )
    fragmentos = partidor.split_text(texto)

    for f in fragmentos:
        f.metadata["business_id"] = business_id
        f.metadata["fuente"] = f"{business_id}.md"

    # Los fragmentos sin `##` (el preámbulo del archivo) no responden ninguna
    # pregunta del cliente; los descartamos para no ensuciar la recuperación.
    return [f for f in fragmentos if f.metadata.get("seccion")]


def construir_indice(recrear: bool = False) -> Chroma:
    """Lee datos/*.md, fragmenta e indexa. El nombre del archivo es el negocio.

    Con `recrear=True` borra el índice anterior. Necesario cuando cambian los
    datos de un negocio: reindexar encima duplicaría los fragmentos, y un
    duplicado en el RAG se ve como el modelo repitiéndose sin razón.
    """
    if recrear and CARPETA_INDICE.exists():
        import shutil

        shutil.rmtree(CARPETA_INDICE)
        logger.info("Índice anterior borrado")

    documentos: list[Document] = []
    for archivo in sorted(CARPETA_DATOS.glob("*.md")):
        business_id = archivo.stem
        # Los archivos en MAYÚSCULAS son documentación de la carpeta, no el
        # conocimiento de un negocio. Sin esto, CUESTIONARIO.md se indexaba
        # como si existiera un negocio llamado "CUESTIONARIO": nadie lo
        # consulta nunca, pero paga embeddings y confunde a quien revisa el
        # índice preguntándose de dónde salió ese tenant.
        if business_id.isupper():
            logger.info("%s: documentación, no se indexa", archivo.name)
            continue
        trozos = _fragmentar(archivo.read_text(encoding="utf-8"), business_id)
        documentos.extend(trozos)
        logger.info("%s → %d fragmentos", archivo.name, len(trozos))

    if not documentos:
        raise RuntimeError(f"No hay documentos en {CARPETA_DATOS}")

    indice = Chroma.from_documents(
        documents=documentos,
        embedding=_embeddings(),
        persist_directory=str(CARPETA_INDICE),
        collection_name="negocios",
    )
    logger.info("Índice construido: %d fragmentos en total", len(documentos))
    return indice


def reindexar_negocio(business_id: str, markdown: str) -> int:
    """Reemplaza en el índice lo que sabe UN negocio. Devuelve los fragmentos.

    Reemplaza y no agrega: indexar encima duplicaría los fragmentos, y un
    duplicado se ve como el bot repitiéndose sin razón.

    Y toca solo a ese negocio en vez de reconstruir todo el índice, que es lo
    que hace `construir_indice(recrear=True)`. La diferencia no es de
    prolijidad: los embeddings del plan gratuito son 1.000 por día para TODO el
    proyecto. Reconstruir entero cada vez que un negocio contesta una pregunta
    del formulario se come la cuota de todos los demás, y cuando se agota el
    bot deja de poder contestar cualquier cosa.

    El archivo se escribe igual, para que un reinicio que reconstruye desde
    cero encuentre lo mismo que hay en el índice.
    """
    CARPETA_DATOS.mkdir(parents=True, exist_ok=True)
    archivo = CARPETA_DATOS / f"{business_id}.md"

    indice = abrir_indice()
    # Se borra siempre, incluso si no hay nada nuevo: un negocio que borró todas
    # sus respuestas quiere que el bot DEJE de contestar eso, y dejar los
    # fragmentos viejos sería lo contrario de lo que pidió.
    try:
        indice.delete(where={"business_id": business_id})
    except Exception:  # noqa: BLE001 — no existía nada de este negocio
        logger.info("no había nada indexado de %s", business_id)

    if not (markdown or "").strip():
        archivo.unlink(missing_ok=True)
        logger.info("%s se quedó sin conocimiento cargado", business_id)
        return 0

    archivo.write_text(markdown, encoding="utf-8")
    trozos = _fragmentar(markdown, business_id)
    if trozos:
        indice.add_documents(trozos)
    logger.info("%s reindexado: %d fragmentos", business_id, len(trozos))
    return len(trozos)


def abrir_indice() -> Chroma:
    """Abre el índice ya construido, sin recalcular embeddings."""
    if not CARPETA_INDICE.exists():
        raise RuntimeError(
            "No existe el índice. Corré: python -m src.rag.indice"
        )
    return Chroma(
        persist_directory=str(CARPETA_INDICE),
        embedding_function=_embeddings(),
        collection_name="negocios",
    )


class Recuperador:
    """Busca en el conocimiento de UN negocio. No puede leer los de otro.

    El `business_id` se fija al construir el objeto, no al buscar. Es la
    diferencia entre "el filtro se puede olvidar" y "el filtro no se puede
    olvidar", y es lo único que separa un SaaS multi-tenant vendible de una
    filtración de datos entre clientes.
    """

    def __init__(self, business_id: str, indice: Chroma | None = None, k: int = 3):
        if not business_id:
            raise ValueError("Un recuperador siempre pertenece a un negocio")
        self._business_id = business_id
        self._indice = indice if indice is not None else abrir_indice()
        self._k = k

    async def buscar(self, consulta: str) -> list[Document]:
        """Los fragmentos relevantes de ESTE negocio. Puede no devolver ninguno.

        Devolver la lista vacía es el comportamiento importante, y es lo que
        antes no pasaba nunca: una búsqueda por los k más cercanos siempre trae
        k resultados, tengan que ver o no. A "¿tienen estacionamiento?" —un dato
        que este negocio no cargó— el bot contestaba la dirección y el teléfono,
        con la misma seguridad que si fuera la respuesta. Para quien pregunta,
        un bot que responde otra cosa y uno que inventa se parecen bastante.
        """
        crudos = await self._indice.asimilarity_search_with_relevance_scores(
            consulta,
            k=self._k,
            filter={"business_id": self._business_id},
        )
        resultados = [d for d, puntaje in crudos if puntaje >= UMBRAL][:MAX_FRAGMENTOS]
        logger.info(
            "RAG [%s] '%s' → %d de %d (mejor %.3f): %s",
            self._business_id,
            consulta[:40],
            len(resultados),
            len(crudos),
            crudos[0][1] if crudos else 0.0,
            [d.metadata.get("seccion") for d in resultados],
        )
        return resultados

    async def contexto(self, consulta: str) -> str:
        """Los fragmentos, listos para mandárselos a la persona.

        Se sacan las líneas que empiezan con `>`. Son las preguntas que el
        panel escribe en cada sección para que la búsqueda encuentre la
        respuesta: sin ellas, una dirección suelta no se parece en nada a
        "¿dónde quedan?" —tres palabras sin un solo término en común— y el bot
        contesta que no lo tiene cargado teniéndolo.

        Se indexan y no se muestran, que son dos cosas distintas y acá se
        confunden fácil: lo que devuelve esto se le manda al cliente TAL CUAL,
        sin que un modelo lo reescriba. Un bot que repite tu pregunta antes de
        contestarla suena a formulario, no a alguien atendiendo.
        """
        docs = await self.buscar(consulta)
        if not docs:
            return ""
        limpios = []
        for d in docs:
            visible = "\n".join(
                l for l in d.page_content.splitlines() if not l.lstrip().startswith(">")
            ).strip()
            if visible:
                limpios.append(visible)
        return "\n\n---\n\n".join(limpios)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    construir_indice(recrear=True)
