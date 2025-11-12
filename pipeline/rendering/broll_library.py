"""
B-roll Library Manager
Semantic search and reuse of previously generated B-roll panels using pgvector
"""
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import hashlib
import json
from datetime import datetime

from loguru import logger
from sqlalchemy import select, func, text
from sqlalchemy.dialects.postgresql import insert

from ..common.db import get_db
from ..common.config import config
from .comfyui_client import ComfyUIClient


class BRollLibrary:
    """
    Manage B-roll library with semantic search

    Phase 4: pgvector-powered reuse system
    - Semantic search for similar prompts
    - Quality scoring and deduplication
    - Usage tracking and analytics
    - Automatic library pruning
    """

    def __init__(self):
        self.similarity_threshold = config.get('broll.library.similarity_threshold', 0.85)
        self.max_age_days = config.get('broll.library.max_age_days', 90)
        self.embedding_generator = None

    async def initialize(self):
        """Initialize B-roll library"""
        # Import embedding generator
        from ..transcription.embedding_generator import EmbeddingGenerator
        self.embedding_generator = EmbeddingGenerator()
        await self.embedding_generator.initialize()

        logger.info(f"B-roll library initialized (similarity threshold: {self.similarity_threshold})")

    async def cleanup(self):
        """Cleanup resources"""
        if self.embedding_generator:
            await self.embedding_generator.cleanup()
        logger.info("B-roll library cleaned up")

    async def search_similar(
        self,
        prompt: str,
        style: str,
        width: int,
        height: int,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Search for similar B-roll panels in library

        Args:
            prompt: Generation prompt
            style: Style (ghibli, cyberpunk, etc.)
            width: Image width
            height: Image height
            limit: Maximum results to return

        Returns:
            List of similar panels with similarity scores
        """
        # Generate embedding for search prompt
        search_embedding = await self.embedding_generator.generate_embedding(prompt)

        if not search_embedding:
            logger.warning("Failed to generate search embedding")
            return []

        # Query database with cosine similarity
        async with get_db() as db:
            # Use pgvector cosine distance operator
            query = text("""
                SELECT
                    id,
                    prompt,
                    style,
                    width,
                    height,
                    image_path,
                    quality_score,
                    usage_count,
                    created_at,
                    1 - (prompt_embedding <=> :search_embedding::vector) as similarity
                FROM broll_library
                WHERE
                    style = :style
                    AND width = :width
                    AND height = :height
                    AND 1 - (prompt_embedding <=> :search_embedding::vector) >= :threshold
                    AND created_at > NOW() - make_interval(days => :max_age_days)
                ORDER BY similarity DESC
                LIMIT :limit
            """)

            result = await db.execute(
                query,
                {
                    'search_embedding': search_embedding,
                    'style': style,
                    'width': width,
                    'height': height,
                    'threshold': self.similarity_threshold,
                    'max_age_days': self.max_age_days,
                    'limit': limit,
                }
            )

            panels = []
            for row in result:
                panels.append({
                    'id': row.id,
                    'prompt': row.prompt,
                    'style': row.style,
                    'width': row.width,
                    'height': row.height,
                    'image_path': row.image_path,
                    'quality_score': float(row.quality_score) if row.quality_score else 0.0,
                    'usage_count': row.usage_count,
                    'created_at': row.created_at,
                    'similarity': float(row.similarity),
                })

        logger.info(f"Found {len(panels)} similar B-roll panels (threshold: {self.similarity_threshold})")
        return panels

    async def get_or_generate(
        self,
        prompt: str,
        style: str,
        width: int,
        height: int,
        output_path: Path,
        comfyui_client: ComfyUIClient,
    ) -> Tuple[Path, bool]:
        """
        Get B-roll from library or generate new one

        Args:
            prompt: Generation prompt
            style: Style preset
            width: Image width
            height: Image height
            output_path: Output path for image
            comfyui_client: ComfyUI client for generation

        Returns:
            Tuple of (image_path, was_reused)
        """
        # Search library first
        similar_panels = await self.search_similar(
            prompt=prompt,
            style=style,
            width=width,
            height=height,
            limit=1,
        )

        if similar_panels:
            # Found similar panel, reuse it
            panel = similar_panels[0]
            library_path = Path(panel['image_path'])

            if library_path.exists():
                # Copy to output path
                import shutil
                shutil.copy2(library_path, output_path)

                # Increment usage count
                await self._increment_usage(panel['id'])

                logger.info(
                    f"Reused B-roll from library (similarity: {panel['similarity']:.2f}, "
                    f"usage_count: {panel['usage_count'] + 1})"
                )

                return output_path, True
            else:
                logger.warning(f"Library panel file not found: {library_path}")
                # Fall through to generation

        # No similar panel found or file missing, generate new one
        logger.info("No suitable B-roll in library, generating new panel")

        generated_path = await comfyui_client.generate_broll(
            prompt=prompt,
            style=style,
            width=width,
            height=height,
            output_path=output_path,
        )

        if generated_path:
            # Add to library
            await self.add_to_library(
                prompt=prompt,
                style=style,
                width=width,
                height=height,
                image_path=generated_path,
            )

            return generated_path, False
        else:
            raise Exception("Failed to generate B-roll panel")

    async def add_to_library(
        self,
        prompt: str,
        style: str,
        width: int,
        height: int,
        image_path: Path,
        quality_score: Optional[float] = None,
    ) -> str:
        """
        Add B-roll panel to library

        Args:
            prompt: Generation prompt
            style: Style preset
            width: Image width
            height: Image height
            image_path: Path to generated image
            quality_score: Optional quality score (0-100)

        Returns:
            Panel ID (UUID)
        """
        # Generate embedding
        prompt_embedding = await self.embedding_generator.generate_embedding(prompt)

        if not prompt_embedding:
            logger.error("Failed to generate embedding for library entry")
            return None

        # Calculate content hash for deduplication
        content_hash = self._calculate_image_hash(image_path)

        # Auto-score quality if not provided
        if quality_score is None:
            quality_score = await self._calculate_quality_score(image_path)

        # Insert into database
        async with get_db() as db:
            from uuid import uuid4
            panel_id = uuid4()

            stmt = text("""
                INSERT INTO broll_library (
                    id, prompt, prompt_embedding, style, width, height,
                    image_path, content_hash, quality_score, usage_count,
                    created_at
                )
                VALUES (
                    :id, :prompt, :prompt_embedding::vector, :style, :width, :height,
                    :image_path, :content_hash, :quality_score, 0, NOW()
                )
                ON CONFLICT (content_hash) DO UPDATE
                SET usage_count = broll_library.usage_count + 1
                RETURNING id
            """)

            result = await db.execute(
                stmt,
                {
                    'id': panel_id,
                    'prompt': prompt,
                    'prompt_embedding': prompt_embedding,
                    'style': style,
                    'width': width,
                    'height': height,
                    'image_path': str(image_path),
                    'content_hash': content_hash,
                    'quality_score': quality_score,
                }
            )

            await db.commit()

            returned_id = result.scalar()

        logger.info(f"Added B-roll panel to library: {returned_id} (quality: {quality_score:.1f})")
        return str(returned_id)

    async def _increment_usage(self, panel_id: str):
        """Increment usage count for a panel"""
        async with get_db() as db:
            await db.execute(
                text("UPDATE broll_library SET usage_count = usage_count + 1 WHERE id = :id"),
                {'id': panel_id}
            )
            await db.commit()

    def _calculate_image_hash(self, image_path: Path) -> str:
        """Calculate perceptual hash of image for deduplication"""
        try:
            # Simple file hash for now
            # In production, use perceptual hashing (pHash) for better deduplication
            with open(image_path, 'rb') as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            return file_hash
        except Exception as e:
            logger.error(f"Error calculating image hash: {e}")
            return hashlib.sha256(str(image_path).encode()).hexdigest()

    async def _calculate_quality_score(self, image_path: Path) -> float:
        """
        Calculate quality score for image

        Uses simple heuristics:
        - Resolution check
        - Blur detection
        - Contrast analysis

        Returns score 0-100
        """
        try:
            import cv2
            import numpy as np

            # Load image
            img = cv2.imread(str(image_path))
            if img is None:
                return 50.0  # Default score

            # Resolution score (full marks if >= target resolution)
            height, width = img.shape[:2]
            resolution_score = min(100, (width * height) / (1920 * 1080) * 100)

            # Blur detection (Laplacian variance)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            blur_score = min(100, laplacian_var / 100 * 100)

            # Contrast (std dev of pixel values)
            contrast = np.std(gray)
            contrast_score = min(100, contrast / 50 * 100)

            # Weighted average
            quality_score = (
                resolution_score * 0.3 +
                blur_score * 0.4 +
                contrast_score * 0.3
            )

            return round(quality_score, 2)

        except Exception as e:
            logger.warning(f"Error calculating quality score: {e}")
            return 50.0  # Default score

    async def prune_library(
        self,
        min_quality_score: float = 30.0,
        max_age_days: int = 180,
        min_usage_count: int = 0,
    ) -> int:
        """
        Prune low-quality and unused panels from library

        Args:
            min_quality_score: Minimum quality score to keep
            max_age_days: Maximum age in days
            min_usage_count: Minimum usage count for old panels

        Returns:
            Number of panels pruned
        """
        async with get_db() as db:
            # Delete low quality panels
            result = await db.execute(
                text("""
                    DELETE FROM broll_library
                    WHERE
                        quality_score < :min_quality
                        OR (
                            created_at < NOW() - make_interval(days => :max_age)
                            AND usage_count < :min_usage
                        )
                    RETURNING id
                """),
                {
                    'min_quality': min_quality_score,
                    'max_age': max_age_days,
                    'min_usage': min_usage_count,
                }
            )

            deleted_ids = result.fetchall()
            await db.commit()

        pruned_count = len(deleted_ids)
        logger.info(f"Pruned {pruned_count} panels from library")

        return pruned_count

    async def get_library_stats(self) -> Dict[str, Any]:
        """Get library statistics"""
        async with get_db() as db:
            result = await db.execute(
                text("""
                    SELECT
                        COUNT(*) as total_panels,
                        AVG(quality_score) as avg_quality,
                        SUM(usage_count) as total_reuses,
                        COUNT(DISTINCT style) as unique_styles,
                        pg_size_pretty(pg_total_relation_size('broll_library')) as table_size
                    FROM broll_library
                """)
            )

            row = result.fetchone()

        return {
            'total_panels': row.total_panels if row else 0,
            'avg_quality': float(row.avg_quality) if row and row.avg_quality else 0.0,
            'total_reuses': row.total_reuses if row else 0,
            'unique_styles': row.unique_styles if row else 0,
            'table_size': row.table_size if row else '0 bytes',
        }


logger.info("B-roll library module loaded")
