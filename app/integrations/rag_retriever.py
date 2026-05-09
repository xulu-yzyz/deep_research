from __future__ import annotations

from langchain_chroma import Chroma
from langchain_core.tools import StructuredTool
from langchain_huggingface import HuggingFaceEmbeddings
from app.core.trace import trace

def _format_doc(doc, score: float) -> str:
    meta = doc.metadata or {}
    title = meta.get("title") or meta.get("source") or "Untitled"
    source = meta.get("source", "")
    return (
        f"Title: {title}\n"
        f"Source: {source}\n"
        f"Score: {score:.4f}\n"
        f"Content:\n{doc.page_content[:1500]}"
    )


def build_rag_retrieval_tool(
    *,
    persist_directory: str = ".rag/chroma",
    collection_name: str = "research_docs",
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
    top_k: int = 5,
    metadata_filter: dict | None = None,
) -> StructuredTool:
    embeddings = HuggingFaceEmbeddings(model_name=embedding_model)
    store = Chroma(
        collection_name=collection_name,
        persist_directory=persist_directory,
        embedding_function=embeddings,
    )

    def retrieve_knowledge(query: str) -> str:
        """Search private RAG knowledge base for relevant research evidence."""
        trace("RAG", "RAG检索开始", query=query, top_k=top_k, filter=metadata_filter)
        results = store.similarity_search_with_score(
            query,
            k=top_k,
            filter=metadata_filter,
        )
        if not results:
            return "No relevant private knowledge base results found."
        trace("RAG", "RAG检索结束", results=len(results))
        for i, (doc, score) in enumerate(results):
            meta = doc.metadata or {}
            trace(
                "rag",
                "hit",
                rank=i + 1,
                score=score,
                title=meta.get("title"),
                source=meta.get("source"),
                chunk_id=meta.get("chunk_id"),
            )
        return "\n\n---\n\n".join(
            _format_doc(doc, score) for doc, score in results
        )

    return StructuredTool.from_function(
        func=retrieve_knowledge,
        name="private_knowledge_search",
        description=(
            "Search the private/domain knowledge base. Use this before web search "
            "when the task may depend on uploaded documents, internal notes, "
            "domain reports, or private references."
        ),
    )