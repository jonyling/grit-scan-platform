"""Text embeddings from Gemini on Vertex. The model proposes vectors. Nothing here scores or decides."""

from __future__ import annotations

from typing import Any

from retrieval.similarity import normalize, to_float32

DEFAULT_MODEL = "gemini-embedding-001"
DEFAULT_DIMENSIONS = 768
TASKS = ("RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT", "SEMANTIC_SIMILARITY")
MAX_CHARS = 6000  # the model reads about 2,048 tokens. A longer text is cut, not rejected.


class EmbeddingError(RuntimeError):
    """An embedding call failed or returned the wrong shape. The message never includes a credential."""


class Embedder:
    batch_size = 50

    def __init__(self, *, client: Any | None = None, model: str = DEFAULT_MODEL, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self._client = client
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: list[str], *, task: str) -> list[list[float]]:
        """Return one unit vector per text, rounded to float32."""
        if task not in TASKS:
            raise ValueError(f"task must be one of {', '.join(TASKS)}")
        if not texts:
            return []
        if self._client is None:
            from agents.gemini import gemini_client

            self._client = gemini_client()
        try:
            response = self._client.models.embed_content(
                model=self.model,
                contents=[text[:MAX_CHARS] for text in texts],
                config={"task_type": task, "output_dimensionality": self.dimensions},
            )
            vectors = [list(item.values) for item in response.embeddings]
        except Exception as exc:
            text = str(exc).split("{", 1)[0].strip()
            raise EmbeddingError(f"Embedding request failed: {text[:160] or type(exc).__name__}") from None
        if len(vectors) != len(texts) or any(len(vector) != self.dimensions for vector in vectors):
            raise EmbeddingError(f"Expected {len(texts)} vectors of {self.dimensions} dimensions")
        return [to_float32(normalize(vector)) for vector in vectors]
