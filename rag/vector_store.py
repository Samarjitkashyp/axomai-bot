import os
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.config import Settings
from rag.chunker import TextChunker

BASE_VECTOR_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "vector_store"))


class VectorStoreManager:
    """
    Manages persistent ChromaDB vector storage and semantic search retrieval.
    Embeddings are generated locally and stored on disk.
    """

    COLLECTION_NAME = "axom_knowledge_base"

    def __init__(self, persist_dir: str = BASE_VECTOR_DIR):
        self.persist_dir = persist_dir
        os.makedirs(self.persist_dir, exist_ok=True)
        self.chunker = TextChunker(chunk_size=600, chunk_overlap=100)

        # Initialize persistent ChromaDB client
        self.client = chromadb.PersistentClient(
            path=self.persist_dir,
            settings=Settings(anonymized_telemetry=False)
        )

        # Get or create the master knowledge base collection
        # Uses Chroma's built-in all-MiniLM-L6-v2 ONNX embedder by default (lightweight & CPU optimized)
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"description": "Axom AI Multilingual Knowledge Base"}
        )

    def index_crawl_job(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Chunks the crawled JSON pages and indexes them into the vector database.
        Uses upsert to prevent duplicates if re-indexing a job.
        """
        job_id = job_data.get("job_id", "unknown")
        chunks = self.chunker.chunk_crawl_job(job_data)

        if not chunks:
            return {
                "job_id": job_id,
                "indexed_chunks": 0,
                "message": "No text content found to index"
            }

        ids = [c["chunk_id"] for c in chunks]
        documents = [c["text"] for c in chunks]
        metadatas = [
            {
                "job_id": c["job_id"],
                "url": c["url"],
                "title": c["title"],
                "chunk_index": c["chunk_index"]
            }
            for c in chunks
        ]

        # Chroma upsert batching (batch size 100)
        batch_size = 100
        for i in range(0, len(ids), batch_size):
            end_idx = i + batch_size
            self.collection.upsert(
                ids=ids[i:end_idx],
                documents=documents[i:end_idx],
                metadatas=metadatas[i:end_idx]
            )

        return {
            "job_id": job_id,
            "indexed_chunks": len(chunks),
            "total_vectors_in_db": self.collection.count(),
            "message": f"Successfully indexed {len(chunks)} chunks into vector store"
        }

    def query(self, query_text: str, n_results: int = 5) -> List[Dict[str, Any]]:
        """
        Performs semantic vector search across the knowledge base.
        Returns top matching chunks with similarity scores and page references.
        """
        query_text = query_text.strip()
        if not query_text or self.collection.count() == 0:
            return []

        # Bound n_results to current count
        limit = min(n_results, self.collection.count())

        results = self.collection.query(
            query_texts=[query_text],
            n_results=limit,
            include=["documents", "metadatas", "distances"]
        )

        formatted_matches = []

        if results and "documents" in results and results["documents"]:
            docs = results["documents"][0]
            metas = results["metadatas"][0] if "metadatas" in results else []
            dists = results["distances"][0] if "distances" in results else []
            ids = results["ids"][0] if "ids" in results else []

            for i in range(len(docs)):
                distance = dists[i] if i < len(dists) else 1.0
                # Convert cosine distance (0=identical) to similarity score (0.0 to 1.0)
                similarity = round(max(0.0, 1.0 - (distance / 2.0)), 3)

                formatted_matches.append({
                    "chunk_id": ids[i] if i < len(ids) else f"chunk_{i}",
                    "text": docs[i],
                    "url": metas[i].get("url", "") if i < len(metas) else "",
                    "title": metas[i].get("title", "") if i < len(metas) else "",
                    "job_id": metas[i].get("job_id", "") if i < len(metas) else "",
                    "similarity": similarity,
                    "distance": round(distance, 4)
                })

        return formatted_matches

    def get_stats(self) -> Dict[str, Any]:
        """
        Returns stats about the vector database.
        """
        return {
            "collection_name": self.COLLECTION_NAME,
            "total_vectors": self.collection.count(),
            "persist_directory": self.persist_dir
        }

    def reset_collection(self) -> int:
        """
        Clears all indexed vectors from ChromaDB and recreates the collection.
        Returns the number of vectors deleted.
        """
        try:
            count = self.collection.count()
            self.client.delete_collection(name=self.COLLECTION_NAME)
            self.collection = self.client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"description": "Axom AI Multilingual Knowledge Base"}
            )
            return count
        except Exception as e:
            print(f"[VectorStoreManager] Error resetting collection: {e}")
            return 0

    def delete_job_vectors(self, job_id: str) -> int:
        """
        Deletes all vector embeddings associated with a specific crawl job.
        """
        try:
            initial_count = self.collection.count()
            self.collection.delete(where={"job_id": job_id})
            deleted = initial_count - self.collection.count()
            return max(0, deleted)
        except Exception as e:
            print(f"[VectorStoreManager] Error deleting job vectors: {e}")
            return 0
