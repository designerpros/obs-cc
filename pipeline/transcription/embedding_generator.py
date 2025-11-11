"""
Embedding generator for semantic search
"""
import asyncio
from typing import List, Optional
import numpy as np

from loguru import logger

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    logger.warning("sentence-transformers not available")

from ..common.config import config


class EmbeddingGenerator:
    """Generate embeddings for semantic search using sentence-transformers"""

    def __init__(self):
        self.model: Optional[SentenceTransformer] = None
        self.model_name = config.get('transcription.output.embedding_model', 'sentence-transformers/all-MiniLM-L6-v2')
        self.embedding_dim = 768  # all-MiniLM-L6-v2 dimension

    async def initialize(self):
        """Initialize embedding model"""
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            logger.warning("sentence-transformers not available, skipping initialization")
            return

        logger.info(f"Loading embedding model: {self.model_name}")

        # Load model in thread pool
        loop = asyncio.get_event_loop()
        self.model = await loop.run_in_executor(
            None,
            SentenceTransformer,
            self.model_name,
        )

        # Move to GPU if available
        device = config.get('transcription.whisper.device', 'cuda')
        if device == 'cuda':
            try:
                import torch
                if torch.cuda.is_available():
                    self.model = self.model.to(torch.device('cuda'))
                    logger.info("Embedding model moved to GPU")
            except Exception as e:
                logger.warning(f"Could not move embedding model to GPU: {e}")

        logger.info("Embedding model loaded successfully")

    async def cleanup(self):
        """Cleanup resources"""
        if self.model:
            del self.model
            self.model = None
        logger.info("Embedding model unloaded")

    async def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for text

        Args:
            text: Input text

        Returns:
            Embedding vector (768 dimensions)
        """
        if not self.model:
            logger.warning("Embedding model not initialized, returning zeros")
            return [0.0] * self.embedding_dim

        if not text or not text.strip():
            return [0.0] * self.embedding_dim

        # Generate embedding in thread pool
        loop = asyncio.get_event_loop()
        embedding = await loop.run_in_executor(
            None,
            self._encode_sync,
            text,
        )

        return embedding.tolist()

    async def generate_batch_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for batch of texts

        Args:
            texts: List of input texts

        Returns:
            List of embedding vectors
        """
        if not self.model:
            logger.warning("Embedding model not initialized, returning zeros")
            return [[0.0] * self.embedding_dim for _ in texts]

        if not texts:
            return []

        # Generate embeddings in thread pool
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None,
            self._encode_batch_sync,
            texts,
        )

        return [emb.tolist() for emb in embeddings]

    def _encode_sync(self, text: str) -> np.ndarray:
        """Synchronous encoding (runs in thread pool)"""
        return self.model.encode(
            text,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

    def _encode_batch_sync(self, texts: List[str]) -> np.ndarray:
        """Synchronous batch encoding (runs in thread pool)"""
        return self.model.encode(
            texts,
            convert_to_numpy=True,
            show_progress_bar=False,
            batch_size=32,
        )


logger.info("Embedding generator module loaded")
