"""
Whisper large-v3 transcriber
"""
import asyncio
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
import json
import time

from faster_whisper import WhisperModel
from loguru import logger

from ..common.config import config


class WhisperTranscriber:
    """Whisper large-v3 transcription with GPU acceleration"""

    def __init__(self):
        self.model: Optional[WhisperModel] = None
        self.model_size = config.get('transcription.whisper.model', 'large-v3')
        self.device = config.get('transcription.whisper.device', 'cuda')
        self.compute_type = config.get('transcription.whisper.compute_type', 'float16')

    async def initialize(self):
        """Initialize Whisper model"""
        logger.info(f"Loading Whisper model: {self.model_size} on {self.device}")

        # Load model
        self.model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )

        logger.info("Whisper model loaded successfully")

    async def cleanup(self):
        """Cleanup resources"""
        if self.model:
            del self.model
            self.model = None
        logger.info("Whisper model unloaded")

    async def extract_audio(self, video_path: Path) -> Path:
        """
        Extract audio from video file

        Args:
            video_path: Path to video file

        Returns:
            Path to extracted audio file (WAV)
        """
        audio_path = video_path.with_suffix('.wav')

        logger.info(f"Extracting audio from {video_path}")

        # Use FFmpeg to extract audio
        process = await asyncio.create_subprocess_exec(
            'ffmpeg',
            '-y',  # Overwrite output file
            '-i', str(video_path),
            '-vn',  # No video
            '-acodec', 'pcm_s16le',  # PCM 16-bit
            '-ar', '16000',  # 16kHz sample rate (Whisper standard)
            '-ac', '1',  # Mono
            str(audio_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise Exception(f"FFmpeg audio extraction failed: {error_msg}")

        logger.info(f"Audio extracted to {audio_path}")
        return audio_path

    async def transcribe(self, audio_path: Path) -> Dict[str, Any]:
        """
        Transcribe audio file

        Args:
            audio_path: Path to audio file

        Returns:
            Transcription result with segments
        """
        if not self.model:
            raise Exception("Whisper model not initialized")

        logger.info(f"Transcribing {audio_path}")

        start_time = time.time()

        # Run transcription in thread pool (CPU-bound operation)
        loop = asyncio.get_event_loop()
        segments, info = await loop.run_in_executor(
            None,
            self._transcribe_sync,
            str(audio_path),
        )

        # Convert generator to list
        segments_list = list(segments)

        duration = time.time() - start_time

        logger.info(
            f"Transcription completed in {duration:.2f}s: "
            f"{len(segments_list)} segments, language: {info.language}"
        )

        # Format segments
        formatted_segments = []
        for segment in segments_list:
            formatted_segments.append({
                'start': segment.start,
                'end': segment.end,
                'text': segment.text.strip(),
                'confidence': getattr(segment, 'avg_logprob', None),
                'words': [
                    {
                        'word': word.word,
                        'start': word.start,
                        'end': word.end,
                        'probability': word.probability,
                    }
                    for word in segment.words
                ] if hasattr(segment, 'words') and segment.words else [],
            })

        return {
            'segments': formatted_segments,
            'language': info.language,
            'language_probability': info.language_probability,
            'duration': duration,
            'confidence': sum(s.get('confidence', 0) for s in formatted_segments) / len(formatted_segments) if formatted_segments else 0,
        }

    def _transcribe_sync(self, audio_path: str):
        """Synchronous transcription (runs in thread pool)"""
        return self.model.transcribe(
            audio_path,
            language=config.get('transcription.whisper.language', 'en'),
            batch_size=config.get('transcription.whisper.batch_size', 16),
            beam_size=config.get('transcription.whisper.beam_size', 5),
            best_of=config.get('transcription.whisper.best_of', 5),
            temperature=config.get('transcription.whisper.temperature', [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]),
            vad_filter=config.get('transcription.whisper.vad_filter', True),
            vad_parameters={
                'threshold': config.get('transcription.whisper.vad_threshold', 0.5),
            },
            word_timestamps=True,  # Enable word-level timestamps
        )


logger.info("Whisper transcriber module loaded")
