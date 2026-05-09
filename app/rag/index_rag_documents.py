from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter


def compute_file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_file(path: Path) -> list[Document]:
    if path.suffix.lower() == ".pdf":
        return PyPDFLoader(str(path)).load()

    if path.suffix.lower() in {".txt", ".md"}:
        return TextLoader(str(path), encoding="utf-8").load()

    return []


def build_documents(docs_dir: Path, domain: str) -> list[Document]:
    docs: list[Document] = []

    for path in docs_dir.rglob("*"):
        if not path.is_file():
            continue

        loaded = load_file(path)
        if not loaded:
            continue

        file_md5 = compute_file_md5(path)
        source = str(path)

        for page_index, doc in enumerate(loaded):
            doc.metadata["source"] = source
            doc.metadata["title"] = path.stem
            doc.metadata["file_md5"] = file_md5
            doc.metadata["page_index"] = page_index
            if domain:
                doc.metadata["domain"] = domain
            docs.append(doc)

    return docs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs-dir", default="docs/rag")
    parser.add_argument("--persist-dir", default=".rag/chroma")
    parser.add_argument("--collection", default="research_docs")
    parser.add_argument("--domain", default="")
    args = parser.parse_args()

    docs_dir = Path(args.docs_dir)
    docs = build_documents(docs_dir, args.domain)

    if not docs:
        print(f"No supported documents found in {docs_dir}")
        return

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=900,
        chunk_overlap=150,
        add_start_index=True,
    )
    chunks = splitter.split_documents(docs)

    chunk_ids: list[str] = []
    for chunk_index, chunk in enumerate(chunks):
        file_md5 = str(chunk.metadata.get("file_md5") or "")
        page_index = int(chunk.metadata.get("page_index") or 0)
        start_index = int(chunk.metadata.get("start_index") or 0)

        chunk_id = f"{file_md5}:p{page_index:04d}:s{start_index:08d}:c{chunk_index:06d}"
        chunk.metadata["chunk_id"] = chunk_id
        chunk.metadata["chunk_index"] = chunk_index
        chunk_ids.append(chunk_id)

    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2"
    )

    Chroma.from_documents(
        documents=chunks,
        ids=chunk_ids,
        embedding=embeddings,
        collection_name=args.collection,
        persist_directory=args.persist_dir,
    )

    print(
        f"Ingested {len(chunks)} chunks from {len(docs)} document page(s) "
        f"into {args.persist_dir}/{args.collection}"
    )


if __name__ == "__main__":
    main()