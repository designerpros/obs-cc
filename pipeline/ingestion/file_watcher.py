"""
File watcher for monitoring upload directory
"""
import asyncio
from pathlib import Path
from typing import Callable, Optional
from datetime import datetime

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from loguru import logger


class StreamFileHandler(FileSystemEventHandler):
    """Handle file system events for stream uploads"""

    def __init__(self, callback: Callable):
        self.callback = callback
        self.pending_folders = {}  # Track folders with incomplete uploads

    def on_created(self, event: FileSystemEvent):
        """Handle file creation events"""
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        folder_path = file_path.parent

        # Skip if not a video file
        if file_path.suffix.lower() not in ['.mp4', '.mkv', '.mov', '.avi', '.json']:
            return

        logger.debug(f"File created: {file_path}")

        # Track this folder
        if folder_path not in self.pending_folders:
            self.pending_folders[folder_path] = {
                'files': set(),
                'last_update': datetime.now(),
            }

        self.pending_folders[folder_path]['files'].add(file_path.name)
        self.pending_folders[folder_path]['last_update'] = datetime.now()

    def on_modified(self, event: FileSystemEvent):
        """Handle file modification events"""
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        folder_path = file_path.parent

        if folder_path in self.pending_folders:
            self.pending_folders[folder_path]['last_update'] = datetime.now()


class FileWatcher:
    """Watch directory for new stream uploads"""

    def __init__(
        self,
        watch_path: str,
        callback: Callable,
        poll_interval: int = 5,
        stable_duration: int = 30,
    ):
        """
        Initialize file watcher

        Args:
            watch_path: Path to watch for new files
            callback: Async callback function(folder_path)
            poll_interval: How often to check for stable folders (seconds)
            stable_duration: How long folder must be stable before processing (seconds)
        """
        self.watch_path = Path(watch_path)
        self.callback = callback
        self.poll_interval = poll_interval
        self.stable_duration = stable_duration

        self.observer: Optional[Observer] = None
        self.event_handler: Optional[StreamFileHandler] = None
        self.poll_task: Optional[asyncio.Task] = None
        self.processed_folders = set()

        # Create watch directory if it doesn't exist
        self.watch_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"FileWatcher initialized for: {self.watch_path}")

    async def start(self):
        """Start watching directory"""
        # Create event handler
        self.event_handler = StreamFileHandler(callback=self.callback)

        # Create observer
        self.observer = Observer()
        self.observer.schedule(
            self.event_handler,
            str(self.watch_path),
            recursive=False,
        )
        self.observer.start()

        # Start polling task
        self.poll_task = asyncio.create_task(self._poll_stable_folders())

        logger.info(f"FileWatcher started, monitoring: {self.watch_path}")

    async def stop(self):
        """Stop watching directory"""
        if self.poll_task:
            self.poll_task.cancel()
            try:
                await self.poll_task
            except asyncio.CancelledError:
                pass

        if self.observer:
            self.observer.stop()
            self.observer.join()

        logger.info("FileWatcher stopped")

    async def _poll_stable_folders(self):
        """Poll for folders that have been stable (no new files) for stable_duration"""
        while True:
            try:
                await asyncio.sleep(self.poll_interval)

                if not self.event_handler:
                    continue

                now = datetime.now()
                stable_folders = []

                for folder_path, data in list(self.event_handler.pending_folders.items()):
                    # Check if folder has been stable
                    time_since_update = (now - data['last_update']).total_seconds()

                    if time_since_update >= self.stable_duration:
                        # Folder is stable and ready to process
                        if folder_path not in self.processed_folders:
                            stable_folders.append(folder_path)
                            self.processed_folders.add(folder_path)

                            # Remove from pending
                            del self.event_handler.pending_folders[folder_path]

                # Process stable folders
                for folder_path in stable_folders:
                    logger.info(f"Folder stable, processing: {folder_path}")
                    try:
                        await self.callback(folder_path)
                    except Exception as e:
                        logger.exception(f"Error in callback for {folder_path}: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in poll loop: {e}")
                await asyncio.sleep(5)  # Back off on error


logger.info("File watcher module loaded")
