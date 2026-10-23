import hashlib
import logging
import math
import threading

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Deterministic multilingual text embeddings.

    When `ENABLE_SENTENCE_TRANSFORMERS=true` and the optional `sentence-transformers`
    package is installed, the configured multilingual model is used. Otherwise a
    deterministic feature-hashing embedding is produced so the platform runs with a
    minimal dependency footprint.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._model = None
        self._model_loaded = False
        self._lock = threading.Lock()

    @property
    def dimension(self) -> int:
        return self.settings.embedding_dim

    def embed(self, text: str | None) -> list[float]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []
        model = self._get_model()
        if model is not None:
            vector = model.encode(cleaned, normalize_embeddings=True)
            return [round(float(value), 6) for value in vector]
        return self._hashed_embedding(cleaned)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    def _get_model(self):
        if not self.settings.enable_sentence_transformers:
            return None
        if self._model_loaded:
            return self._model
        with self._lock:
            if self._model_loaded:
                return self._model
            self._model_loaded = True
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.settings.embedding_model)
                logger.info("Loaded sentence-transformers model %s", self.settings.embedding_model)
            except Exception:
                logger.warning(
                    "sentence-transformers is unavailable; using deterministic hashed embeddings.",
                    exc_info=True,
                )
                self._model = None
        return self._model

    def _hashed_embedding(self, text: str) -> list[float]:
        dimension = self.dimension
        vector = [0.0] * dimension
        normalized = " ".join(text.casefold().split())

        features: list[str] = normalized.split()
        for token in normalized.split():
            padded = f" {token} "
            features.extend(padded[i : i + 3] for i in range(max(len(padded) - 2, 1)))

        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % dimension
            sign = 1.0 if (value >> 63) & 1 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return []
        return [round(value / norm, 6) for value in vector]
