"""
Multi-camera video mixer
Intelligently switches between camera angles based on audio and content
"""
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any
import json

from loguru import logger

from ..common.config import config


class VideoMixer:
    """
    Mix multiple camera sources into single video

    For Phase 1: Use live_mix as baseline
    For Phase 3: Implement intelligent switching with audio + CV
    """

    def __init__(self):
        self.strategy = config.get('camera_switching.strategy', 'hybrid_c')

    async def initialize(self):
        """Initialize video mixer"""
        logger.info(f"Video mixer initialized (strategy: {self.strategy})")

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("Video mixer cleaned up")

    async def mix_cameras(
        self,
        source_files: Dict[str, Path],
        start_time: float,
        end_time: float,
        output_dir: Path,
        format: str = 'landscape',
    ) -> Path:
        """
        Mix multi-camera sources

        Args:
            source_files: Dict of camera source paths
            start_time: Start time in seconds
            end_time: End time in seconds
            output_dir: Output directory
            format: 'landscape' or 'portrait'

        Returns:
            Path to mixed video file
        """
        duration = end_time - start_time

        logger.info(
            f"Mixing cameras from {start_time:.1f}s to {end_time:.1f}s "
            f"(duration: {duration:.1f}s, format: {format})"
        )

        # For Phase 1: Use live_mix as baseline
        # This is the user's manual switching during live stream
        # In Phase 3, we'll replace this with intelligent switching

        live_mix_path = source_files.get('live_mix')

        if not live_mix_path or not live_mix_path.exists():
            raise FileNotFoundError("live_mix.mp4 not found - required for video mixing")

        # Extract segment from live_mix
        output_path = output_dir / f"mixed_video_{int(start_time)}.mp4"

        # Use FFmpeg to extract segment
        await self._extract_segment(
            input_path=live_mix_path,
            output_path=output_path,
            start_time=start_time,
            duration=duration,
            format=format,
        )

        logger.info(f"Mixed video saved to {output_path}")
        return output_path

    async def _extract_segment(
        self,
        input_path: Path,
        output_path: Path,
        start_time: float,
        duration: float,
        format: str,
    ):
        """
        Extract segment from video using FFmpeg

        Args:
            input_path: Input video path
            output_path: Output video path
            start_time: Start time in seconds
            duration: Duration in seconds
            format: 'landscape' or 'portrait'
        """
        # Build FFmpeg command
        if format == 'landscape':
            # 1920x1080 landscape
            scale_filter = 'scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2'
        else:
            # 1080x1920 portrait
            scale_filter = 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2'

        cmd = [
            'ffmpeg',
            '-y',  # Overwrite output
            '-ss', str(start_time),  # Start time
            '-i', str(input_path),  # Input file
            '-t', str(duration),  # Duration
            '-vf', scale_filter,  # Video filter (scale and pad)
            '-c:v', 'libx264',  # Video codec
            '-preset', 'medium',  # Encoding preset
            '-crf', '23',  # Quality (lower = better, 18-28 is good range)
            '-c:a', 'aac',  # Audio codec
            '-b:a', '192k',  # Audio bitrate
            '-movflags', '+faststart',  # Fast start for web playback
            str(output_path),
        ]

        # Execute FFmpeg
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise Exception(f"FFmpeg segment extraction failed: {error_msg}")

        logger.debug(f"Extracted segment: {output_path}")


logger.info("Video mixer module loaded")
