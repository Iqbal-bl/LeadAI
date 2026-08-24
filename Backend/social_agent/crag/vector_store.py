"""
Builds or loads a persistent Chroma vector store over the knowledge base.
Embedding model comes from models.get_embedding_model() — swap providers
in .env, not here.
"""

from pathlib import Path

from langchain_chroma import Chroma

from models import get_embedding_model

from .ingest import chunk_documents, load_documents


def build_vector_store(knowledge_dir: str, persist_dir: str, force_rebuild: bool = False) -> Chroma:
    embeddings = get_embedding_model()
    persist_path = Path(persist_dir)

    if persist_path.exists() and not force_rebuild:
        return Chroma(persist_directory=str(persist_path), embedding_function=embeddings)

    docs = load_documents(knowledge_dir)
    chunks = chunk_documents(docs)
    store = Chroma.from_documents(chunks, embeddings, persist_directory=str(persist_path))
    return store
