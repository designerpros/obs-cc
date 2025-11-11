"""
System Audio Capture
Captures system audio directly (requires pyaudio or sounddevice)
"""
import asyncio
import logging
from typing import Optional
import queue
import threading

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
    import numpy as np
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False
    logger.warning("sounddevice not installed. Run: pip install sounddevice")


class SystemAudioCapture:
    """Captures system audio for transcription"""

    def __init__(self, config: dict, transcription_manager):
        if not AUDIO_AVAILABLE:
            raise ImportError("sounddevice not installed")

        self.config = config
        self.transcription_manager = transcription_manager

        # Audio settings
        self.sample_rate = config.get('audio', {}).get('sample_rate', 48000)
        self.channels = config.get('audio', {}).get('channels', 2)
        self.chunk_duration = config.get('audio', {}).get('chunk_duration', 0.1)  # 100ms
        self.device = config.get('audio', {}).get('device', None)  # None = default

        self.audio_queue = queue.Queue()
        self.stream = None
        self.is_running = False

    async def start(self):
        """Start capturing audio"""
        logger.info("Starting system audio capture...")

        # List available devices
        devices = sd.query_devices()
        logger.info("Available audio devices:")
        for i, device in enumerate(devices):
            logger.info(f"  {i}: {device['name']} (inputs: {device['max_input_channels']})")

        # Start audio stream
        self.is_running = True
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            callback=self._audio_callback,
            device=self.device,
        )
        self.stream.start()

        # Start processing task
        asyncio.create_task(self._process_audio_queue())

        logger.info(f"Audio capture started (device: {self.device or 'default'}, {self.sample_rate}Hz)")

    async def stop(self):
        """Stop capturing audio"""
        self.is_running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        logger.info("Audio capture stopped")

    def _audio_callback(self, indata, frames, time_info, status):
        """Callback for audio data (runs in separate thread)"""
        if status:
            logger.warning(f"Audio callback status: {status}")

        # Convert to bytes and queue
        audio_bytes = indata.tobytes()
        self.audio_queue.put(audio_bytes)

    async def _process_audio_queue(self):
        """Process queued audio data"""
        while self.is_running:
            try:
                # Get audio from queue (non-blocking)
                try:
                    audio_data = self.audio_queue.get_nowait()

                    # Send to transcription manager
                    await self.transcription_manager.process_audio(audio_data)

                except queue.Empty:
                    await asyncio.sleep(0.01)  # Brief pause

            except Exception as e:
                logger.error(f"Error processing audio queue: {e}", exc_info=True)


def list_audio_devices():
    """List all available audio devices"""
    if not AUDIO_AVAILABLE:
        print("sounddevice not installed. Run: pip install sounddevice")
        return

    devices = sd.query_devices()
    print("\nAvailable Audio Devices:")
    print("-" * 60)
    for i, device in enumerate(devices):
        print(f"{i}: {device['name']}")
        print(f"   Inputs: {device['max_input_channels']}, Outputs: {device['max_output_channels']}")
        print(f"   Default SR: {device['default_samplerate']}Hz")
        print()


if __name__ == "__main__":
    list_audio_devices()
