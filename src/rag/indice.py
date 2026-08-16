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
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import MarkdownHeaderTextSplitter

logger = logging.getLogger("pipeline.rag")

RAIZ = Path(__file__).resolve().parent.parent.parent
CARPETA_DATOS = RAIZ / "datos"
CARPETA_INDICE = RAIZ / "chroma"

# Embeddings locales: gratis, ilimitados y los datos del negocio no salen de
# la máquina — el mismo argumento de venta del plan self-hosted.
#
# Por qué bge-m3 y no nomic-embed-text: lo medimos sobre 8 preguntas reales en
# español (ver test_recuperacion.py). nomic acertaba 4/8 en top-1 y fallaba en
# "cuánto cuesta un corte", que es LA pregunta más común de un negocio de
# turnos. bge-m3 acierta 7/8. La diferencia es el idioma: nomic está entrenado
# sobre todo en inglés, y agregarle los prefijos de tarea que pide su
# documentación no cambió nada. El producto habla español; el modelo también
# tiene que hacerlo.
MODELO_EMBEDDINGS = "bge-m3"


def _embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(model=MODELO_EMBEDDINGS)


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
        """Devuelve los fragmentos más relevantes, solo de este negocio."""
        resultados = await self._indice.asimilarity_search(
            consulta,
            k=self._k,
            filter={"business_id": self._business_id},
        )
        logger.info(
            "RAG [%s] '%s' → %d fragmento(s): %s",
            self._business_id,
            consulta[:40],
            len(resultados),
            [d.metadata.get("seccion") for d in resultados],
        )
        return resultados

    async def contexto(self, consulta: str) -> str:
        """Los fragmentos ya formateados para meter en el prompt del modelo."""
        docs = await self.buscar(consulta)
        if not docs:
            return ""
        return "\n\n---\n\n".join(d.page_content.strip() for d in docs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    construir_indice(recrear=True)
