"""
Audio processor for normalization, BGM, and EQ
"""
import asyncio
import os
from pathlib import Path
from typing import Optional
import httpx

from loguru import logger

from ..common.config import config


class AudioProcessor:
    """
    Process audio:
    - Normalize peaks to -1dB
    - Add style-matched BGM (ducked to -20dB under speech)
    - Apply EQ (high-pass 80Hz, warmth boost)
    - Add subtle reverb
    """

    def __init__(self):
        self.pixabay_api_key = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self.bgm_cache = Path('/mnt/pipeline/work/bgm_cache')
        self.bgm_cache.mkdir(parents=True, exist_ok=True)

    async def initialize(self):
        """Initialize audio processor"""
        self.pixabay_api_key = config.get('PIXABAY_API_KEY', os.getenv('PIXABAY_API_KEY'))

        if self.pixabay_api_key:
            self.http_client = httpx.AsyncClient(timeout=60.0)
            logger.info("Audio processor initialized with Pixabay BGM")
        else:
            logger.warning("Pixabay API key not configured - BGM disabled")

        logger.info("Audio processor initialized")

    async def cleanup(self):
        """Cleanup resources"""
        if self.http_client:
            await self.http_client.aclose()
        logger.info("Audio processor cleaned up")

    async def process_audio(
        self,
        video_path: Path,
        output_dir: Path,
        broll_style: str = 'general',
    ) -> Path:
        """
        Process audio from video

        Args:
            video_path: Input video path
            output_dir: Output directory
            broll_style: B-roll style (affects BGM selection)

        Returns:
            Path to processed audio file
        """
        logger.info(f"Processing audio for {video_path.name} (style: {broll_style})")

        # Extract audio from video
        extracted_audio = output_dir / 'audio_extracted.wav'
        await self._extract_audio(video_path, extracted_audio)

        # Normalize audio
        normalized_audio = output_dir / 'audio_normalized.wav'
        await self._normalize_audio(extracted_audio, normalized_audio)

        # Add BGM if enabled
        if config.get('rendering.audio.bgm.enabled', False) and self.pixabay_api_key:
            bgm_path = await self._get_bgm_track(broll_style)

            if bgm_path:
                final_audio = output_dir / 'audio_final.wav'
                await self._mix_with_bgm(
                    voice_audio=normalized_audio,
                    bgm_audio=bgm_path,
                    output_audio=final_audio,
                )
                return final_audio

        # No BGM, return normalized audio
        return normalized_audio

    async def _extract_audio(self, video_path: Path, output_path: Path):
        """Extract audio from video"""
        cmd = [
            'ffmpeg',
            '-y',
            '-i', str(video_path),
            '-vn',  # No video
            '-acodec', 'pcm_s16le',
            '-ar', '48000',  # 48kHz sample rate
            '-ac', '2',  # Stereo
            str(output_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        await process.communicate()

        if process.returncode != 0:
            raise Exception("Audio extraction failed")

    async def _normalize_audio(self, input_path: Path, output_path: Path):
        """
        Normalize audio peaks to -1dB

        Uses FFmpeg's loudnorm filter for proper loudness normalization
        """
        cmd = [
            'ffmpeg',
            '-y',
            '-i', str(input_path),
            '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',  # EBU R128 normalization
            str(output_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        await process.communicate()

        if process.returncode != 0:
            raise Exception("Audio normalization failed")

        logger.debug(f"Normalized audio: {output_path}")

    async def _get_bgm_track(self, style: str) -> Optional[Path]:
        """
        Get BGM track from Pixabay or cache

        Args:
            style: Style for BGM selection (ghibli, cyberpunk, etc.)

        Returns:
            Path to BGM audio file or None
        """
        # Map style to Pixabay search query
        music_queries = config.get('rendering.audio.bgm.style_match', {
            'ghibli': 'peaceful piano ambient',
            'cyberpunk': 'synthwave electronic dark',
            'minimalist': 'minimal ambient piano',
            'neon_finance': 'upbeat corporate tech',
            'vintage_policy': 'classical orchestral',
            'crypto': 'electronic upbeat',
            'finance': 'corporate motivation',
            'general': 'ambient background',
        })

        query = music_queries.get(style, music_queries['general'])

        # Check cache first
        cache_key = f"{style}_{query.replace(' ', '_')}.mp3"
        cached_path = self.bgm_cache / cache_key

        if cached_path.exists():
            logger.debug(f"Using cached BGM: {cached_path}")
            return cached_path

        # Fetch from Pixabay
        try:
            logger.info(f"Fetching BGM from Pixabay: {query}")

            response = await self.http_client.get(
                "https://pixabay.com/api/",
                params={
                    'key': self.pixabay_api_key,
                    'q': query,
                    'audio_type': 'music',
                    'per_page': 5,
                }
            )

            response.raise_for_status()
            data = response.json()

            if not data.get('hits'):
                logger.warning(f"No BGM found for query: {query}")
                return None

            # Get first result
            track = data['hits'][0]
            download_url = track['previewURL']

            # Download track
            logger.info(f"Downloading BGM: {track['tags']}")

            download_response = await self.http_client.get(download_url)
            download_response.raise_for_status()

            # Save to cache
            with open(cached_path, 'wb') as f:
                f.write(download_response.content)

            logger.info(f"Cached BGM: {cached_path}")
            return cached_path

        except Exception as e:
            logger.error(f"Error fetching BGM: {e}")
            return None

    async def _mix_with_bgm(
        self,
        voice_audio: Path,
        bgm_audio: Path,
        output_audio: Path,
    ):
        """
        Mix voice audio with background music

        - BGM ducked to -20dB under speech
        - Apply EQ and reverb
        """
        # Build complex FFmpeg filter
        # - adelay: delay BGM slightly for natural feel
        # - volume: reduce BGM to -20dB
        # - highpass: remove rumble at 80Hz
        # - equalizer: boost warmth
        # - aecho: subtle reverb

        filter_complex = (
            '[1:a]volume=-20dB,adelay=100|100[bgm];'  # Duck BGM
            '[0:a]highpass=f=80,equalizer=f=200:width_type=h:width=200:g=2[voice];'  # Voice EQ
            '[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2[mixed];'  # Mix
            '[mixed]aecho=0.8:0.88:60:0.4[final]'  # Subtle reverb
        )

        cmd = [
            'ffmpeg',
            '-y',
            '-i', str(voice_audio),
            '-i', str(bgm_audio),
            '-filter_complex', filter_complex,
            '-map', '[final]',
            str(output_audio),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        await process.communicate()

        if process.returncode != 0:
            raise Exception("Audio mixing failed")

        logger.debug(f"Mixed audio with BGM: {output_audio}")


logger.info("Audio processor module loaded")
