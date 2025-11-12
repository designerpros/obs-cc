"""
Thumbnail generator
AI-generated base + extracted frames + platform-specific text overlay
"""
import asyncio
from pathlib import Path
from typing import List, Any

from loguru import logger

from ..common.config import config


class ThumbnailGenerator:
    """
    Generate thumbnails for extractions

    Phase 1: Extract frame from video
    Phase 2: AI-generated base (SDXL) + overlays
    Phase 5: A/B testing with analytics
    """

    def __init__(self):
        self.enabled = config.get('metadata.thumbnails.enabled', True)

    async def initialize(self):
        """Initialize thumbnail generator"""
        logger.info("Thumbnail generator initialized (frame extraction mode)")

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("Thumbnail generator cleaned up")

    async def generate_thumbnails(
        self,
        extraction: Any,
        count: int = 2,
    ) -> List[Path]:
        """
        Generate thumbnail variants

        Args:
            extraction: Extraction database object
            count: Number of variants to generate

        Returns:
            List of thumbnail paths
        """
        if not self.enabled:
            return []

        video_path = Path(extraction.video_path)

        if not video_path.exists():
            logger.warning(f"Video not found: {video_path}")
            return []

        thumbnails = []

        # For Phase 1: Extract frames at different timestamps
        # Variant 1: Frame at 25% through video
        # Variant 2: Frame at 50% through video

        duration = extraction.end_time - extraction.start_time

        timestamps = [
            duration * 0.25,  # 25%
            duration * 0.50,  # 50%
        ]

        output_dir = video_path.parent / 'thumbnails'
        output_dir.mkdir(exist_ok=True)

        for i, timestamp in enumerate(timestamps[:count]):
            thumbnail_path = output_dir / f"thumbnail_variant_{i+1}.jpg"

            await self._extract_frame(
                video_path=video_path,
                timestamp=timestamp,
                output_path=thumbnail_path,
            )

            if thumbnail_path.exists():
                thumbnails.append(thumbnail_path)

        logger.info(f"Generated {len(thumbnails)} thumbnail variants")
        return thumbnails

    async def _extract_frame(
        self,
        video_path: Path,
        timestamp: float,
        output_path: Path,
    ):
        """Extract frame from video at timestamp"""
        cmd = [
            'ffmpeg',
            '-y',
            '-ss', str(timestamp),
            '-i', str(video_path),
            '-vframes', '1',
            '-q:v', '2',  # High quality JPEG
            str(output_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        await process.communicate()

        if process.returncode != 0:
            logger.error(f"Frame extraction failed for {video_path}")


logger.info("Thumbnail generator module loaded")
