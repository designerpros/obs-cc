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
from .scene_detector import SceneDetector


class VideoMixer:
    """
    Mix multiple camera sources into single video

    Phase 1: Use live_mix as baseline
    Phase 3: Intelligent switching with scene detection and audio analysis
    """

    def __init__(self):
        self.strategy = config.get('camera_switching.strategy', 'hybrid_c')
        self.intelligent_switching = config.get('camera_switching.intelligent_enabled', True)
        self.scene_detector = None

    async def initialize(self):
        """Initialize video mixer"""
        if self.intelligent_switching:
            self.scene_detector = SceneDetector()
            await self.scene_detector.initialize()
            logger.info(f"Video mixer initialized with intelligent switching (strategy: {self.strategy})")
        else:
            logger.info(f"Video mixer initialized (baseline mode, strategy: {self.strategy})")

    async def cleanup(self):
        """Cleanup resources"""
        if self.scene_detector:
            await self.scene_detector.cleanup()
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

        # Check if we have multiple camera sources for intelligent switching
        available_cameras = {
            name: path for name, path in source_files.items()
            if path and Path(path).exists() and name != 'live_mix'
        }

        # Phase 3: Use intelligent switching if available and multiple cameras exist
        if self.intelligent_switching and self.scene_detector and len(available_cameras) >= 2:
            logger.info(f"Using intelligent switching with {len(available_cameras)} cameras")
            return await self._mix_intelligent(
                source_files=source_files,
                start_time=start_time,
                end_time=end_time,
                output_dir=output_dir,
                format=format,
            )
        else:
            # Phase 1: Use live_mix as baseline
            logger.info("Using baseline live_mix (intelligent switching disabled or insufficient cameras)")
            return await self._mix_baseline(
                source_files=source_files,
                start_time=start_time,
                end_time=end_time,
                output_dir=output_dir,
                format=format,
            )

    async def _mix_baseline(
        self,
        source_files: Dict[str, Path],
        start_time: float,
        end_time: float,
        output_dir: Path,
        format: str,
    ) -> Path:
        """Use live_mix as baseline (Phase 1 approach)"""
        live_mix_path = source_files.get('live_mix')

        if not live_mix_path or not live_mix_path.exists():
            raise FileNotFoundError("live_mix.mp4 not found - required for video mixing")

        duration = end_time - start_time
        output_path = output_dir / f"mixed_video_{int(start_time)}.mp4"

        # Extract segment from live_mix
        await self._extract_segment(
            input_path=live_mix_path,
            output_path=output_path,
            start_time=start_time,
            duration=duration,
            format=format,
        )

        logger.info(f"Baseline mixed video saved to {output_path}")
        return output_path

    async def _mix_intelligent(
        self,
        source_files: Dict[str, Path],
        start_time: float,
        end_time: float,
        output_dir: Path,
        format: str,
    ) -> Path:
        """Intelligent multi-camera mixing with scene detection (Phase 3)"""
        duration = end_time - start_time

        # Analyze segment to get camera switch recommendations
        camera_paths = {
            name: path for name, path in source_files.items()
            if path and Path(path).exists() and name != 'live_mix'
        }

        switch_recommendations = await self.scene_detector.analyze_segment(
            video_paths=camera_paths,
            start_time=start_time,
            end_time=end_time,
        )

        if not switch_recommendations:
            logger.warning("No switch recommendations, falling back to baseline")
            return await self._mix_baseline(
                source_files=source_files,
                start_time=start_time,
                end_time=end_time,
                output_dir=output_dir,
                format=format,
            )

        # Build FFmpeg filter for camera switching
        output_path = output_dir / f"mixed_video_{int(start_time)}_intelligent.mp4"

        await self._apply_camera_switches(
            camera_paths=camera_paths,
            switch_recommendations=switch_recommendations,
            start_time=start_time,
            end_time=end_time,
            output_path=output_path,
            format=format,
        )

        logger.info(f"Intelligent mixed video saved to {output_path}")
        return output_path

    async def _apply_camera_switches(
        self,
        camera_paths: Dict[str, Path],
        switch_recommendations: List[Dict[str, Any]],
        start_time: float,
        end_time: float,
        output_path: Path,
        format: str,
    ):
        """Apply camera switches using FFmpeg concat demuxer"""

        # Sort recommendations by timestamp
        switches = sorted(switch_recommendations, key=lambda x: x['timestamp'])

        # Build segments list
        segments = []
        current_time = start_time
        current_camera = list(camera_paths.keys())[0]  # Start with first available camera

        for switch in switches:
            if switch['timestamp'] > current_time:
                # Add segment for current camera
                segments.append({
                    'camera': current_camera,
                    'start': current_time,
                    'end': switch['timestamp'],
                })
                current_time = switch['timestamp']
                current_camera = switch['camera']

        # Add final segment
        if current_time < end_time:
            segments.append({
                'camera': current_camera,
                'start': current_time,
                'end': end_time,
            })

        # Extract each segment
        temp_dir = output_path.parent / 'temp_segments'
        temp_dir.mkdir(exist_ok=True)

        segment_files = []
        for i, segment in enumerate(segments):
            camera_path = camera_paths.get(segment['camera'])
            if not camera_path:
                continue

            segment_path = temp_dir / f"segment_{i:03d}.mp4"

            await self._extract_segment(
                input_path=camera_path,
                output_path=segment_path,
                start_time=segment['start'],
                duration=segment['end'] - segment['start'],
                format=format,
            )

            segment_files.append(segment_path)

        # Concatenate segments
        if segment_files:
            await self._concatenate_segments(segment_files, output_path)

            # Cleanup temp files
            import shutil
            shutil.rmtree(temp_dir)
        else:
            raise Exception("No segments generated for intelligent mixing")

    async def _concatenate_segments(
        self,
        segment_files: List[Path],
        output_path: Path,
    ):
        """Concatenate video segments using FFmpeg concat demuxer"""

        # Create concat file
        concat_file = output_path.parent / 'concat_list.txt'
        with open(concat_file, 'w') as f:
            for segment in segment_files:
                f.write(f"file '{segment.absolute()}'\n")

        # Run FFmpeg concat
        cmd = [
            'ffmpeg',
            '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-c', 'copy',
            str(output_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise Exception(f"FFmpeg concatenation failed: {error_msg}")

        # Cleanup concat file
        concat_file.unlink()

        logger.debug(f"Concatenated {len(segment_files)} segments into {output_path}")

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
