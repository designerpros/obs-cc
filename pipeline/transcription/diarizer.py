"""
Speaker diarization using pyannote.audio
"""
import asyncio
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
import time

from loguru import logger

# Import pyannote
try:
    from pyannote.audio import Pipeline
    PYANNOTE_AVAILABLE = True
except ImportError:
    PYANNOTE_AVAILABLE = False
    logger.warning("pyannote.audio not available - speaker diarization disabled")

from ..common.config import config


class SpeakerDiarizer:
    """Speaker diarization using pyannote.audio"""

    def __init__(self):
        self.pipeline: Optional[Pipeline] = None
        self.model_name = config.get('transcription.diarization.model', 'pyannote/speaker-diarization-3.1')
        self.hf_token = os.getenv('HF_TOKEN')

    async def initialize(self):
        """Initialize pyannote pipeline"""
        if not PYANNOTE_AVAILABLE:
            logger.warning("Pyannote not available, skipping initialization")
            return

        if not self.hf_token:
            logger.error("HF_TOKEN not set - cannot load pyannote model")
            logger.error("Please set HF_TOKEN environment variable with Hugging Face token")
            logger.error("Token must have accepted pyannote terms at: https://huggingface.co/pyannote/speaker-diarization-3.1")
            return

        logger.info(f"Loading pyannote model: {self.model_name}")

        try:
            # Load pipeline
            self.pipeline = Pipeline.from_pretrained(
                self.model_name,
                use_auth_token=self.hf_token,
            )

            # Move to GPU if available
            device = config.get('transcription.whisper.device', 'cuda')
            if device == 'cuda':
                import torch
                if torch.cuda.is_available():
                    self.pipeline.to(torch.device('cuda'))
                    logger.info("Pyannote pipeline moved to GPU")

            logger.info("Pyannote pipeline loaded successfully")

        except Exception as e:
            logger.exception(f"Error loading pyannote model: {e}")
            self.pipeline = None

    async def cleanup(self):
        """Cleanup resources"""
        if self.pipeline:
            del self.pipeline
            self.pipeline = None
        logger.info("Pyannote pipeline unloaded")

    async def diarize(self, audio_path: Path) -> Dict[str, Any]:
        """
        Perform speaker diarization

        Args:
            audio_path: Path to audio file

        Returns:
            Diarization result with speaker segments
        """
        if not self.pipeline:
            logger.warning("Pyannote pipeline not initialized, returning mock diarization")
            return self._mock_diarization(audio_path)

        logger.info(f"Running speaker diarization on {audio_path}")

        start_time = time.time()

        # Run diarization in thread pool
        loop = asyncio.get_event_loop()
        diarization = await loop.run_in_executor(
            None,
            self._diarize_sync,
            str(audio_path),
        )

        duration = time.time() - start_time

        # Extract speaker information
        speakers = {}
        segments = []

        for turn, _, speaker in diarization.itertracks(yield_label=True):
            # Track speaker
            if speaker not in speakers:
                speakers[speaker] = {
                    'speaker_id': speaker,
                    'name': f"Speaker {len(speakers) + 1}",
                    'total_duration': 0,
                }

            speakers[speaker]['total_duration'] += turn.duration

            # Add segment
            segments.append({
                'start': turn.start,
                'end': turn.end,
                'speaker_id': speaker,
            })

        logger.info(
            f"Diarization completed in {duration:.2f}s: "
            f"{len(speakers)} speakers, {len(segments)} segments"
        )

        return {
            'speakers': list(speakers.values()),
            'segments': segments,
            'duration': duration,
        }

    def _diarize_sync(self, audio_path: str):
        """Synchronous diarization (runs in thread pool)"""
        min_speakers = config.get('transcription.diarization.min_speakers', 1)
        max_speakers = config.get('transcription.diarization.max_speakers', 4)

        return self.pipeline(
            audio_path,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )

    def _mock_diarization(self, audio_path: Path) -> Dict[str, Any]:
        """
        Mock diarization when pyannote not available

        Returns a single speaker for the entire audio duration
        """
        # Get audio duration
        try:
            import subprocess
            result = subprocess.run(
                [
                    'ffprobe',
                    '-v', 'error',
                    '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    str(audio_path),
                ],
                capture_output=True,
                text=True,
            )
            duration = float(result.stdout.strip())
        except Exception:
            duration = 3600.0  # Default 1 hour

        logger.warning("Using mock diarization (single speaker)")

        return {
            'speakers': [
                {
                    'speaker_id': 'SPEAKER_00',
                    'name': 'Speaker 1',
                    'total_duration': duration,
                }
            ],
            'segments': [
                {
                    'start': 0.0,
                    'end': duration,
                    'speaker_id': 'SPEAKER_00',
                }
            ],
            'duration': 0.0,
        }


logger.info("Speaker diarizer module loaded")
