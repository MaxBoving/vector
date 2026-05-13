import chromadb
from chromadb.utils import embedding_functions
import json
import os
from typing import Any, Dict, List, Optional

class KnowledgeManager:
    """
    STORY-015: Semantic Vector Memory (RAG 2.0)
    Manages document embeddings and semantic search using ChromaDB.
    """
    
    def __init__(self, company_name: str):
        self.company_name = company_name
        self.db_path = "./chroma_db"
        self.client = chromadb.PersistentClient(path=self.db_path)

        self.emb_fn = _get_embedding_function()

        # Collection unique to the company
        collection_kwargs: dict[str, Any] = {
            "name": f"knowledge_{company_name.lower().replace(' ', '_')}",
        }
        if self.emb_fn is not None:
            collection_kwargs["embedding_function"] = self.emb_fn
        self.collection = self.client.get_or_create_collection(**collection_kwargs)

    def add_documents(self, documents: List[Dict[str, str]]) -> bool:
        """
        STORY-016/066: Automated Document Chunking with Consistency Check.
        Adds documents to the vector store and returns success status.
        """
        if self.emb_fn is None:
            return False

        ids = []
        metadatas = []
        contents = []
        
        try:
            for idx, doc in enumerate(documents):
                title = doc.get("title", "Untitled")
                content = doc.get("content", "")
                
                # Simple chunking logic (STORY-016)
                chunks = self._chunk_text(content, chunk_size=500)
                
                for c_idx, chunk in enumerate(chunks):
                    # Sanitize IDs for ChromaDB
                    safe_title = "".join(x for x in title if x.isalnum() or x in "._-")
                    ids.append(f"{safe_title}_{idx}_{c_idx}")
                    metadatas.append({"title": title})
                    contents.append(chunk)
            
            if contents:
                self.collection.add(
                    ids=ids,
                    metadatas=metadatas,
                    documents=contents
                )
            return True
        except Exception as e:
            print(f"RAG Indexing Error: {e}")
            return False

    def semantic_search(self, query: str, limit: int = 5) -> List[Dict[str, str]]:
        """
        Performs semantic search to find meaning-related documents.
        """
        if self.emb_fn is None:
            return []

        results = self.collection.query(
            query_texts=[query],
            n_results=limit
        )
        
        formatted_results = []
        if results['documents']:
            for i in range(len(results['documents'][0])):
                formatted_results.append({
                    "title": results['metadatas'][0][i]['title'],
                    "content": results['documents'][0][i]
                })
        return formatted_results

    def _chunk_text(self, text: str, chunk_size: int = 500) -> List[str]:
        """Simple sliding window chunker."""
        if not text: return []
        return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]


# ---------------------------------------------------------------------------
# Module-level Chroma client + embedding function (shared across collections)
# ---------------------------------------------------------------------------

_CHROMA_DB_PATH = "./chroma_db"
_chroma_client = chromadb.PersistentClient(path=_CHROMA_DB_PATH)
_emb_fn = None


def _get_embedding_function():
    global _emb_fn
    if _emb_fn is not None:
        return _emb_fn
    try:
        _emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
    except Exception as exc:
        print(f"Knowledge embeddings unavailable: {exc}")
        _emb_fn = None
    return _emb_fn

MEMORY_COLLECTION_NAME = "ceo_memories"
ENTITY_INDEX_COLLECTION = "entity_memory_index"


def get_memory_collection():
    """Get or create the CEO memory Chroma collection."""
    emb_fn = _get_embedding_function()
    collection_kwargs: dict[str, Any] = {
        "name": MEMORY_COLLECTION_NAME,
        "metadata": {"hnsw:space": "cosine"},
    }
    if emb_fn is not None:
        collection_kwargs["embedding_function"] = emb_fn
    return _chroma_client.get_or_create_collection(
        **collection_kwargs,
    )


def index_memory(
    memory_id: str,
    ceo_id: str,
    title: str,
    content: str,
    memory_type: str,
    entities: List[str],
    tags: List[str],
) -> None:
    """Add or update a memory in the Chroma index."""
    if _get_embedding_function() is None:
        return

    collection = get_memory_collection()
    text_to_embed = f"{title}\n{content}"
    collection.upsert(
        ids=[f"mem:{ceo_id}:{memory_id}"],
        documents=[text_to_embed],
        metadatas=[{
            "memory_id": memory_id,
            "ceo_id": ceo_id,
            "memory_type": memory_type,
            "entities": json.dumps(entities),
            "tags": json.dumps(tags),
            "title": title[:200],
        }],
    )


def search_memories(
    ceo_id: str,
    query: str,
    limit: int = 8,
    memory_types: Optional[List[str]] = None,
) -> List[dict]:
    """Semantic search over CEO memories. Returns ranked results."""
    collection = get_memory_collection()
    count = collection.count()
    if count == 0:
        return []

    where: Dict[str, Any] = {"ceo_id": ceo_id}
    if memory_types:
        where["memory_type"] = {"$in": memory_types}

    try:
        results = collection.query(
            query_texts=[query],
            n_results=min(limit, count),
            where=where,
        )
    except Exception:
        return []

    if not results or not results.get("ids"):
        return []

    output = []
    ids = results["ids"][0]
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results.get("distances", [[]])[0] or [0.5] * len(ids)

    for _id, doc, meta, dist in zip(ids, docs, metas, distances):
        output.append({
            "memory_id": meta.get("memory_id"),
            "title": meta.get("title", ""),
            "content": doc,
            "memory_type": meta.get("memory_type", ""),
            "entities": json.loads(meta.get("entities") or "[]"),
            "tags": json.loads(meta.get("tags") or "[]"),
            "relevance_score": round(1 - float(dist), 3),
        })

    return sorted(output, key=lambda x: x["relevance_score"], reverse=True)


# ---------------------------------------------------------------------------
# Entity index
# ---------------------------------------------------------------------------

def get_entity_collection():
    """Get or create the entity memory index Chroma collection."""
    emb_fn = _get_embedding_function()
    collection_kwargs: dict[str, Any] = {
        "name": ENTITY_INDEX_COLLECTION,
        "metadata": {"hnsw:space": "cosine"},
    }
    if emb_fn is not None:
        collection_kwargs["embedding_function"] = emb_fn
    return _chroma_client.get_or_create_collection(
        **collection_kwargs,
    )


def index_entity_link(
    entity: str,
    ceo_id: str,
    source_type: str,   # "memory" | "thread_entry" | "interaction"
    source_id: str,
    content_snippet: str,
    timestamp: str,
) -> None:
    """Index an entity → source mapping so entity-anchored retrieval works."""
    if _get_embedding_function() is None:
        return

    collection = get_entity_collection()
    doc_id = f"entity:{ceo_id}:{entity.lower().replace(' ', '_')}:{source_type}:{source_id}"
    collection.upsert(
        ids=[doc_id],
        documents=[f"{entity}: {content_snippet}"],
        metadatas=[{
            "entity": entity,
            "entity_normalized": entity.lower(),
            "ceo_id": ceo_id,
            "source_type": source_type,
            "source_id": source_id,
            "timestamp": timestamp,
        }],
    )


def search_entity_context(
    ceo_id: str,
    entities: List[str],
    limit: int = 10,
) -> List[dict]:
    """
    Retrieve everything known about a set of entities.
    Returns cross-source results (memories + thread entries + interactions).
    """
    if not entities:
        return []

    collection = get_entity_collection()
    count = collection.count()
    if count == 0:
        return []

    query = " ".join(entities)
    try:
        results = collection.query(
            query_texts=[query],
            n_results=min(limit, count),
            where={"ceo_id": ceo_id},
        )
    except Exception:
        return []

    if not results or not results.get("ids"):
        return []

    output = []
    for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
        output.append({
            "entity": meta.get("entity"),
            "source_type": meta.get("source_type"),
            "source_id": meta.get("source_id"),
            "snippet": doc,
            "timestamp": meta.get("timestamp", ""),
        })

    return output
