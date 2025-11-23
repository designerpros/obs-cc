"""
Audio File Monitor
Monitors an audio file written by OBS and sends it to AssemblyAI
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional
import wave

import assemblyai as aai

logger = logging.getLogger(__name__)


class AudioFileMonitor:
    """Monitors audio file from OBS and transcribes it"""

    def __init__(self, config: dict, transcription_manager):
        self.config = config
        self.transcription_manager = transcription_manager

        # Validate and sanitize file path to prevent directory traversal
        audio_path = config.get('audio_capture', {}).get('audio_file_path', 'obs_audio.wav')
        self.audio_file_path = self._validate_audio_path(audio_path)

        self.last_position = 0
        self.is_running = False
        self.check_interval = 1.0  # Check every second

    def _validate_audio_path(self, path_str: str) -> Path:
        """Validate audio file path to prevent directory traversal attacks"""
        path = Path(path_str).resolve()

        # Get allowed base directory (current working directory)
        allowed_base = Path.cwd().resolve()

        # Check if path is within allowed directory
        try:
            path.relative_to(allowed_base)
        except ValueError:
            logger.error(f"Security: Path {path} is outside allowed directory {allowed_base}")
            raise ValueError(f"Invalid audio file path: must be within {allowed_base}")

        logger.info(f"Validated audio file path: {path}")
        return path

    async def start(self):
        """Start monitoring the audio file"""
        logger.info(f"Starting audio file monitor for: {self.audio_file_path}")
        self.is_running = True
        asyncio.create_task(self._monitor_loop())

    async def stop(self):
        """Stop monitoring"""
        self.is_running = False

    async def _monitor_loop(self):
        """Main monitoring loop"""
        while self.is_running:
            try:
                if self.audio_file_path.exists():
                    await self._process_new_audio()
                else:
                    logger.debug(f"Audio file not found: {self.audio_file_path}")

            except Exception as e:
                logger.error(f"Error monitoring audio file: {e}", exc_info=True)

            await asyncio.sleep(self.check_interval)

    async def _process_new_audio(self):
        """Process new audio data from the file"""
        MAX_CHUNK_SIZE = 1024 * 1024 * 10  # 10 MB max chunk to prevent memory exhaustion

        try:
            # Get file size
            file_size = self.audio_file_path.stat().st_size

            if file_size <= self.last_position:
                return  # No new data

            # Calculate how much to read
            bytes_to_read = file_size - self.last_position

            # Limit chunk size to prevent memory exhaustion
            if bytes_to_read > MAX_CHUNK_SIZE:
                logger.warning(f"Large audio chunk ({bytes_to_read} bytes), limiting to {MAX_CHUNK_SIZE}")
                bytes_to_read = MAX_CHUNK_SIZE

            # Read new audio data with size limit
            with open(self.audio_file_path, 'rb') as f:
                f.seek(self.last_position)
                new_data = f.read(bytes_to_read)

            if new_data:
                # Send to transcription manager
                await self.transcription_manager.process_audio(new_data)
                self.last_position += len(new_data)

        except Exception as e:
            logger.error(f"Error processing audio file: {e}", exc_info=True)
