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
        self.audio_file_path = Path(config.get('audio_file_path', 'obs_audio.wav'))
        self.last_position = 0
        self.is_running = False
        self.check_interval = 1.0  # Check every second

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
        try:
            # Get file size
            file_size = self.audio_file_path.stat().st_size

            if file_size <= self.last_position:
                return  # No new data

            # Read new audio data
            with open(self.audio_file_path, 'rb') as f:
                f.seek(self.last_position)
                new_data = f.read()

            if new_data:
                # Send to transcription manager
                await self.transcription_manager.process_audio(new_data)
                self.last_position = file_size

        except Exception as e:
            logger.error(f"Error processing audio file: {e}", exc_info=True)
