"""
Ingestion Service
Monitors for new stream files and triggers pipeline
"""
import asyncio
import os
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from loguru import logger

# Import pipeline utilities
import sys
sys.path.append(str(Path(__file__).parent.parent))

from common.config import config
from common.queue import init_queue, close_queue, job_queue, JobType
from common.notifications import init_notifications, close_notifications, notifier
from common.db import create_stream, update_stream_status

from .file_watcher import FileWatcher
from .file_validator import FileValidator
from .upload_handler import UploadHandler
from .bot_server import BotCommandServer

# Initialize FastAPI app
app = FastAPI(title="Ingestion Service", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global instances
file_watcher: Optional[FileWatcher] = None
bot_server: Optional[BotCommandServer] = None


@app.on_event("startup")
async def startup():
    """Initialize services on startup"""
    global file_watcher, bot_server

    logger.info("Starting Ingestion Service...")

    # Initialize dependencies
    await init_queue()
    await init_notifications()

    # Initialize file watcher if enabled
    if config.get('ingestion.triggers.file_watcher.enabled', True):
        watch_path = config.get('ingestion.triggers.file_watcher.watch_path')
        if watch_path:
            file_watcher = FileWatcher(
                watch_path=watch_path,
                callback=on_new_files_detected,
            )
            await file_watcher.start()
            logger.info(f"File watcher started for: {watch_path}")

    # Initialize bot command server
    bot_server = BotCommandServer(callback=on_bot_command)
    await bot_server.start()

    logger.info("Ingestion Service started successfully")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    logger.info("Shutting down Ingestion Service...")

    if file_watcher:
        await file_watcher.stop()

    if bot_server:
        await bot_server.stop()

    await close_queue()
    await close_notifications()

    logger.info("Ingestion Service shut down")


async def on_new_files_detected(files_path: Path):
    """
    Callback when new files detected in watch folder

    Args:
        files_path: Path to folder with new files
    """
    logger.info(f"New files detected: {files_path}")

    try:
        # Validate files
        validator = FileValidator()
        validation_result = await validator.validate_folder(files_path)

        if not validation_result['valid']:
            logger.error(f"File validation failed: {validation_result['errors']}")
            await notifier.notify(
                f"❌ File validation failed:\n" + "\n".join(validation_result['errors']),
                level="error",
            )
            return

        # Process stream
        await process_stream(
            files_path=files_path,
            file_mapping=validation_result['file_mapping'],
        )

    except Exception as e:
        logger.exception(f"Error processing new files: {e}")
        await notifier.notify(
            f"❌ Error processing files: {str(e)}",
            level="error",
        )


async def on_bot_command(command: str, args: list):
    """
    Callback for bot commands

    Args:
        command: Command name
        args: Command arguments
    """
    logger.info(f"Bot command received: {command} {args}")

    if command == "process":
        if not args or args[0] == "latest":
            # Process latest stream in watch folder
            watch_path = Path(config.get('ingestion.triggers.file_watcher.watch_path'))

            # Find newest folder
            folders = [f for f in watch_path.iterdir() if f.is_dir()]
            if not folders:
                await notifier.notify("❌ No streams found in watch folder", level="error")
                return

            latest_folder = max(folders, key=lambda f: f.stat().st_mtime)
            await notifier.notify(f"🔍 Processing latest stream: {latest_folder.name}", level="info")

            await on_new_files_detected(latest_folder)

        elif len(args) >= 1:
            # Process specific date (YYYY-MM-DD)
            date_str = args[0]
            watch_path = Path(config.get('ingestion.triggers.file_watcher.watch_path'))

            # Find folder matching date
            matching_folders = [
                f for f in watch_path.iterdir()
                if f.is_dir() and date_str in f.name
            ]

            if not matching_folders:
                await notifier.notify(f"❌ No stream found for date: {date_str}", level="error")
                return

            folder = matching_folders[0]
            await notifier.notify(f"🔍 Processing stream: {folder.name}", level="info")

            await on_new_files_detected(folder)

    elif command == "status":
        # Get pipeline status
        # TODO: Implement status checking
        await notifier.notify("📊 Status check coming soon", level="info")

    elif command == "cancel":
        # Cancel running pipeline
        # TODO: Implement pipeline cancellation
        await notifier.notify("🛑 Cancel command coming soon", level="info")

    elif command == "retry":
        # Retry failed pipeline
        # TODO: Implement retry logic
        await notifier.notify("🔄 Retry command coming soon", level="info")


async def process_stream(
    files_path: Path,
    file_mapping: dict,
):
    """
    Process stream files through pipeline

    Args:
        files_path: Path to stream files
        file_mapping: Validated file mapping
    """
    stream_name = files_path.name

    try:
        # Notify start
        await notifier.notify(
            f"🚀 Starting pipeline for: {stream_name}",
            level="info",
        )

        # Create stream in database
        stream_id = await create_stream(
            cam_main_path=str(file_mapping['cam_main_me']),
            live_mix_path=str(file_mapping['live_mix']),
            cam_guest_path=str(file_mapping.get('cam_guest')),
            cam_overhead_path=str(file_mapping.get('cam_overhead')),
            cam_screen_path=str(file_mapping.get('cam_screen')),
            cam_online_caller_path=str(file_mapping.get('cam_online_caller')),
            title=stream_name,
        )

        logger.info(f"Created stream {stream_id} for {stream_name}")

        # Upload files to Node A
        upload_handler = UploadHandler()

        await notifier.send_progress(
            stream_id=stream_id,
            stream_name=stream_name,
            stages=[
                {'name': 'Upload', 'status': 'running', 'duration_remaining': '60-90 min'},
                {'name': 'Remix', 'status': 'pending'},
                {'name': 'Transcription', 'status': 'pending'},
                {'name': 'Extraction', 'status': 'pending'},
                {'name': 'B-roll', 'status': 'pending'},
                {'name': 'Posting', 'status': 'pending'},
                {'name': 'Archive', 'status': 'pending'},
            ],
            eta_seconds=None,
        )

        upload_result = await upload_handler.upload_stream(
            stream_id=stream_id,
            files_path=files_path,
            file_mapping=file_mapping,
        )

        if not upload_result['success']:
            raise Exception(f"Upload failed: {upload_result.get('error')}")

        logger.info(f"Upload completed for stream {stream_id}")

        # Update stream status
        await update_stream_status(stream_id, 'uploaded')

        # Enqueue first job (validation)
        await job_queue.enqueue(
            job_type=JobType.VALIDATE,
            stream_id=stream_id,
            payload={
                'stream_name': stream_name,
                'work_dir': upload_result['work_dir'],
                'file_mapping': file_mapping,
            },
            priority=100,
        )

        logger.info(f"Enqueued validation job for stream {stream_id}")

        # Update progress
        await notifier.send_progress(
            stream_id=stream_id,
            stream_name=stream_name,
            stages=[
                {'name': 'Upload', 'status': 'completed', 'duration': '85 min'},
                {'name': 'Remix', 'status': 'pending'},
                {'name': 'Transcription', 'status': 'pending'},
                {'name': 'Extraction', 'status': 'pending'},
                {'name': 'B-roll', 'status': 'pending'},
                {'name': 'Posting', 'status': 'pending'},
                {'name': 'Archive', 'status': 'pending'},
            ],
            eta_seconds=14400,  # ~4 hours
        )

    except Exception as e:
        logger.exception(f"Error processing stream: {e}")

        await notifier.send_failure_alert(
            stream_id=stream_id if 'stream_id' in locals() else None,
            stream_name=stream_name,
            stage="Ingestion",
            error_message=str(e),
        )

        if 'stream_id' in locals():
            await update_stream_status(stream_id, 'failed', error_message=str(e))


@app.get("/")
async def root():
    """Health check"""
    return {
        "service": "Ingestion",
        "status": "running",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.post("/trigger")
async def trigger_endpoint(
    path: str,
    authorization: Optional[str] = Header(None),
):
    """
    Manual trigger endpoint

    Args:
        path: Path to stream files
        authorization: Auth token
    """
    # Validate auth token
    expected_token = config.get('ingestion.triggers.webhook.auth_token')
    if expected_token and authorization != f"Bearer {expected_token}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    files_path = Path(path)

    if not files_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    # Process stream
    asyncio.create_task(on_new_files_detected(files_path))

    return {"status": "triggered", "path": str(files_path)}


if __name__ == "__main__":
    # Get host and port from config
    host = config.get('ingestion.triggers.webhook.host', '0.0.0.0')
    port = config.get('ingestion.triggers.webhook.port', 8080)

    logger.info(f"Starting Ingestion Service on {host}:{port}")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
    )
