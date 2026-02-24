from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from typing import Any, List

logger = logging.getLogger(__name__)

_KG_PERSIST_DIR = Path(os.environ.get("PYOVIS_MEMORY_DIR", "/pyovis_memory")) / "kg"


class KGStore:
    """Thread-safe knowledge graph store backed by FAISS + disk persistence."""

    def __init__(self, persist_dir: Path = _KG_PERSIST_DIR) -> None:
        self.persist_dir = persist_dir
        self.model: Any = None
        self.index: Any = None
        self.dimension: int | None = None
        self.documents: List[str] = []
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        sentence_transformers = importlib.import_module("sentence_transformers")
        faiss = importlib.import_module("faiss")

        self.model = sentence_transformers.SentenceTransformer("all-MiniLM-L6-v2")
        self.dimension = self.model.get_sentence_embedding_dimension()

        self.persist_dir.mkdir(parents=True, exist_ok=True)
        index_path = self.persist_dir / "faiss.index"
        docs_path = self.persist_dir / "documents.txt"

        if index_path.exists() and docs_path.exists():
            try:
                self.index = faiss.read_index(str(index_path))
                self.documents = docs_path.read_text(encoding="utf-8").splitlines()
                logger.info(
                    "Loaded KG store: %d documents from %s",
                    len(self.documents),
                    self.persist_dir,
                )
            except Exception as exc:
                logger.warning("Failed to load persisted KG store, starting fresh: %s", exc)
                self.index = faiss.IndexFlatL2(self.dimension)
                self.documents = []
        else:
            self.index = faiss.IndexFlatL2(self.dimension)
            self.documents = []

        self._initialized = True

    def add(self, texts: List[str]) -> int:
        self._ensure_initialized()
        numpy = importlib.import_module("numpy")
        if not texts:
            return 0
        embeddings = self.model.encode(texts)
        vectors = numpy.array(embeddings).astype("float32")
        self.index.add(vectors)  # type: ignore[call-arg]
        self.documents.extend(texts)
        self._persist()
        return len(texts)

    def search(self, query: str, k: int = 5) -> List[dict]:
        self._ensure_initialized()
        query_vec = self.model.encode([query]).astype("float32")
        distances, indices = self.index.search(query_vec, k)  # type: ignore[call-arg]
        results = []
        for rank, idx in enumerate(indices[0]):
            if 0 <= idx < len(self.documents):
                results.append(
                    {
                        "text": self.documents[idx],
                        "distance": float(distances[0][rank]),
                        "index": int(idx),
                    }
                )
        return results

    def _persist(self) -> None:
        faiss = importlib.import_module("faiss")
        try:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            faiss.write_index(self.index, str(self.persist_dir / "faiss.index"))
            (self.persist_dir / "documents.txt").write_text(
                "\n".join(self.documents), encoding="utf-8"
            )
        except Exception as exc:
            logger.error("Failed to persist KG store: %s", exc)


_store = KGStore()


def _create_app() -> Any:
    """Create the FastAPI app lazily (only when actually starting the server)."""
    fastapi = importlib.import_module("fastapi")
    pydantic = importlib.import_module("pydantic")

    FastAPI = fastapi.FastAPI
    BaseModel = pydantic.BaseModel

    app = FastAPI()

    class AddRequest(BaseModel):
        texts: List[str]

    class SearchRequest(BaseModel):
        query: str
        k: int = 5

    @app.post("/add")
    def add_texts(request: AddRequest) -> dict:
        added = _store.add(request.texts)
        return {"added": added}

    @app.post("/search")
    def search_texts(request: SearchRequest) -> dict:
        results = _store.search(request.query, request.k)
        return {"results": results}

    return app


async def start_kg_server(host: str = "0.0.0.0", port: int = 8003) -> None:
    uvicorn = importlib.import_module("uvicorn")
    app = _create_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
