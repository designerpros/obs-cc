"""
AssemblyAI Transcription Manager
Handles real-time audio transcription and maintains context buffer
"""
import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Optional
import io

import assemblyai as aai

logger = logging.getLogger(__name__)


class TranscriptionManager:
    """Manages real-time transcription with AssemblyAI"""

    def __init__(self, config: dict):
        self.config = config
        self.api_key = config['assemblyai_api_key']
        self.buffer_duration = config['transcription']['buffer_duration_seconds']

        # Initialize AssemblyAI
        if self.api_key and self.api_key != "YOUR_ASSEMBLYAI_API_KEY_HERE":
            aai.settings.api_key = self.api_key
        else:
            logger.warning("AssemblyAI API key not configured!")

        # Transcription buffer: stores (timestamp, text) tuples
        self.context_buffer = deque()
        self.transcriber: Optional[aai.RealtimeTranscriber] = None
        self.is_running = False

    async def start(self):
        """Start the transcription service"""
        if not self.api_key or self.api_key == "YOUR_ASSEMBLYAI_API_KEY_HERE":
            logger.warning("Skipping transcription start - no API key configured")
            return

        try:
            logger.info("Starting AssemblyAI transcription service...")

            # Create real-time transcriber
            self.transcriber = aai.RealtimeTranscriber(
                sample_rate=48000,  # OBS typically uses 48kHz
                on_data=self._on_data,
                on_error=self._on_error,
            )

            # Connect to AssemblyAI
            self.transcriber.connect()
            self.is_running = True
            logger.info("AssemblyAI transcription service started")

        except Exception as e:
            logger.error(f"Error starting transcription: {e}", exc_info=True)

    async def stop(self):
        """Stop the transcription service"""
        self.is_running = False
        if self.transcriber:
            try:
                self.transcriber.close()
                logger.info("AssemblyAI transcription service stopped")
            except Exception as e:
                logger.error(f"Error stopping transcription: {e}", exc_info=True)

    def _on_data(self, transcript: aai.RealtimeTranscript):
        """Callback for transcription data"""
        try:
            if isinstance(transcript, aai.RealtimeFinalTranscript):
                text = transcript.text.strip()
                if text:
                    timestamp = datetime.now()
                    self.context_buffer.append((timestamp, text))
                    logger.debug(f"Transcribed: {text}")

                    # Clean old entries
                    self._clean_buffer()

        except Exception as e:
            logger.error(f"Error processing transcript: {e}", exc_info=True)

    def _on_error(self, error: aai.RealtimeError):
        """Callback for transcription errors"""
        logger.error(f"AssemblyAI error: {error}")

    def _clean_buffer(self):
        """Remove entries older than buffer_duration"""
        cutoff_time = datetime.now() - timedelta(seconds=self.buffer_duration)

        while self.context_buffer and self.context_buffer[0][0] < cutoff_time:
            self.context_buffer.popleft()

    def get_context(self) -> str:
        """Get the current transcription context as a single string"""
        self._clean_buffer()

        if not self.context_buffer:
            return ""

        # Combine all transcripts
        texts = [text for _, text in self.context_buffer]
        return " ".join(texts)

    async def process_audio(self, audio_data: bytes):
        """Process incoming audio data"""
        if not self.is_running or not self.transcriber:
            return

        try:
            # Send audio to AssemblyAI
            self.transcriber.stream(audio_data)

        except Exception as e:
            logger.error(f"Error processing audio: {e}", exc_info=True)
