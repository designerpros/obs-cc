"""
FFmpeg final renderer with GPU acceleration
"""
import asyncio
from pathlib import Path
from typing import Optional

from loguru import logger

from ..common.config import config


class FFmpegRenderer:
    """
    Final video rendering with FFmpeg

    - GPU-accelerated encoding (NVENC)
    - High quality (CRF 18)
    - Add branding (logo overlay, outro)
    """

    def __init__(self):
        self.use_gpu = config.get('rendering.ffmpeg.hardware_acceleration', 'cuda') == 'cuda'
        self.logo_path = config.get('rendering.branding.logo.path')
        self.outro_template = config.get('rendering.branding.outro.template')

    async def initialize(self):
        """Initialize FFmpeg renderer"""
        if self.use_gpu:
            # Test NVENC availability
            has_nvenc = await self._check_nvenc()
            if has_nvenc:
                logger.info("FFmpeg renderer initialized with NVENC GPU acceleration")
            else:
                logger.warning("NVENC not available, falling back to CPU encoding")
                self.use_gpu = False
        else:
            logger.info("FFmpeg renderer initialized (CPU encoding)")

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("FFmpeg renderer cleaned up")

    async def render_final(
        self,
        video_path: Path,
        audio_path: Path,
        output_dir: Path,
        format: str,
        extraction_type: str,
    ) -> Path:
        """
        Render final video

        Args:
            video_path: Input video path (after B-roll insertion)
            audio_path: Processed audio path
            output_dir: Output directory
            format: 'landscape' or 'portrait'
            extraction_type: 'long', 'short', 'micro', 'medium'

        Returns:
            Path to final rendered video
        """
        logger.info(f"Final rendering: {extraction_type} ({format})")

        # Determine output filename
        output_filename = f"{extraction_type}_{format}.mp4"
        output_path = output_dir / output_filename

        # Build FFmpeg command
        cmd = self._build_ffmpeg_command(
            video_path=video_path,
            audio_path=audio_path,
            output_path=output_path,
            format=format,
        )

        # Execute FFmpeg
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise Exception(f"FFmpeg rendering failed: {error_msg}")

        logger.info(f"Final video rendered: {output_path}")
        return output_path

    def _build_ffmpeg_command(
        self,
        video_path: Path,
        audio_path: Path,
        output_path: Path,
        format: str,
    ) -> list:
        """Build FFmpeg command with all filters"""

        # Build video filter chain
        vfilters = []

        # Add logo overlay if enabled
        if config.get('rendering.branding.logo.enabled', False) and self.logo_path:
            logo_position = config.get('rendering.branding.logo.position', 'top-right')
            logo_opacity = config.get('rendering.branding.logo.opacity', 0.10)

            # Convert position to overlay coordinates
            if logo_position == 'top-right':
                overlay_pos = 'W-w-10:10'
            elif logo_position == 'top-left':
                overlay_pos = '10:10'
            elif logo_position == 'bottom-right':
                overlay_pos = 'W-w-10:H-h-10'
            elif logo_position == 'bottom-left':
                overlay_pos = '10:H-h-10'
            else:
                overlay_pos = 'W-w-10:10'  # Default top-right

            vfilters.append(f'movie={self.logo_path},format=rgba,colorchannelmixer=aa={logo_opacity}[logo];[in][logo]overlay={overlay_pos}[out]')

        # Combine filters
        vfilter_str = ','.join(vfilters) if vfilters else None

        # Build command
        cmd = ['ffmpeg', '-y']

        # Input files
        cmd.extend(['-i', str(video_path)])
        cmd.extend(['-i', str(audio_path)])

        # Video filters
        if vfilter_str:
            cmd.extend(['-filter_complex', vfilter_str])

        # Video encoding
        if self.use_gpu:
            # GPU-accelerated encoding with NVENC
            cmd.extend([
                '-c:v', 'h264_nvenc',
                '-preset', config.get('rendering.ffmpeg.preset', 'slow'),
                '-rc', 'vbr',
                '-cq', str(config.get('rendering.ffmpeg.crf', 18)),
                '-b:v', '0',  # VBR mode
                '-maxrate', '10M',
                '-bufsize', '20M',
            ])
        else:
            # CPU encoding
            cmd.extend([
                '-c:v', 'libx264',
                '-preset', config.get('rendering.ffmpeg.preset', 'slow'),
                '-crf', str(config.get('rendering.ffmpeg.crf', 18)),
            ])

        # Audio encoding
        cmd.extend([
            '-c:a', config.get('rendering.ffmpeg.audio_codec', 'aac'),
            '-b:a', config.get('rendering.ffmpeg.audio_bitrate', '192k'),
            '-ar', str(config.get('rendering.ffmpeg.audio_sample_rate', 48000)),
        ])

        # Pixel format
        cmd.extend(['-pix_fmt', config.get('rendering.ffmpeg.pixel_format', 'yuv420p')])

        # Fast start for web playback
        cmd.extend(['-movflags', '+faststart'])

        # Output file
        cmd.append(str(output_path))

        return cmd

    async def _check_nvenc(self) -> bool:
        """Check if NVENC is available"""
        try:
            process = await asyncio.create_subprocess_exec(
                'ffmpeg',
                '-hide_banner',
                '-encoders',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, _ = await process.communicate()
            output = stdout.decode()

            return 'h264_nvenc' in output

        except Exception:
            return False


logger.info("FFmpeg renderer module loaded")
