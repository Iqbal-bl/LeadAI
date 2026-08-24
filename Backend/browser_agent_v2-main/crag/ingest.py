"""
Loads knowledge-base documents (.md, .txt, .pdf) from a directory and
splits them into retrieval-sized chunks. This is the only file you touch
to point CRAG at different content — everything downstream just consumes
whatever chunks this produces, regardless of source.
"""

from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader


def _read_pdf(file_path: Path) -> str:
    reader = PdfReader(str(file_path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def load_documents(knowledge_dir: str) -> list[Document]:
    dir_path = Path(knowledge_dir)
    if not dir_path.exists():
        raise FileNotFoundError(f"Knowledge directory not found: {knowledge_dir}")

    docs = []
    for file_path in sorted(dir_path.glob("**/*")):
        suffix = file_path.suffix.lower()
        if suffix in (".md", ".txt"):
            text = file_path.read_text(encoding="utf-8")
        elif suffix == ".pdf":
            text = _read_pdf(file_path)
        else:
            continue
        if text.strip():
            docs.append(Document(page_content=text, metadata={"source": str(file_path)}))

    if not docs:
        raise ValueError(f"No .md, .txt, or .pdf files found in {knowledge_dir}")
    return docs


def chunk_documents(docs: list[Document], chunk_size: int = 800, chunk_overlap: int = 100) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return splitter.split_documents(docs)