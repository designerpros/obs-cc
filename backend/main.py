"""
OBS News Ticker Backend Service
Handles transcription, news fetching, and AI-powered context matching
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from .transcription import TranscriptionManager
from .news_fetcher import NewsFetcher
from .context_matcher import ContextMatcher

# Setup logging FIRST (before any logger usage)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Optional audio capture modules (logger now available)
try:
    from .audio_capture import SystemAudioCapture
    SYSTEM_AUDIO_AVAILABLE = True
except ImportError:
    SYSTEM_AUDIO_AVAILABLE = False
    logger.warning("System audio capture not available (sounddevice not installed)")

try:
    from .audio_file_monitor import AudioFileMonitor
    FILE_MONITOR_AVAILABLE = True
except ImportError:
    FILE_MONITOR_AVAILABLE = False
    logger.warning("Audio file monitor not available")

# Load configuration
CONFIG_PATH = Path(__file__).parent.parent / "config.json"
if not CONFIG_PATH.exists():
    CONFIG_PATH = Path(__file__).parent.parent / "config.example.json"
    logger.warning("config.json not found, using config.example.json. Please create config.json with your API keys.")

with open(CONFIG_PATH) as f:
    config = json.load(f)

# Initialize FastAPI app
app = FastAPI(title="OBS News Ticker Backend")

# Add CORS middleware - restricted to localhost for security
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:*",  # Any port on localhost
        "http://127.0.0.1:*",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],  # Only needed methods
    allow_headers=["Content-Type"],
)

# Global state
transcription_manager = TranscriptionManager(config)
news_fetcher = NewsFetcher(config)
context_matcher = ContextMatcher(config)
current_headlines: List[Dict] = []
last_update: Optional[datetime] = None

# Audio capture (based on config)
audio_capture = None
audio_capture_mode = config.get('audio_capture', {}).get('mode', 'none')


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    global audio_capture

    logger.info("Starting OBS News Ticker Backend...")
    await news_fetcher.start()
    await transcription_manager.start()

    # Initialize audio capture based on mode
    if audio_capture_mode == 'system':
        if SYSTEM_AUDIO_AVAILABLE:
            logger.info("Initializing system audio capture...")
            audio_capture = SystemAudioCapture(config, transcription_manager)
            await audio_capture.start()
        else:
            logger.error("System audio mode selected but sounddevice not installed!")
            logger.error("Run: pip install sounddevice")

    elif audio_capture_mode == 'file':
        if FILE_MONITOR_AVAILABLE:
            logger.info("Initializing file-based audio monitor...")
            audio_capture = AudioFileMonitor(config, transcription_manager)
            await audio_capture.start()
        else:
            logger.error("File monitor mode selected but module not available!")

    elif audio_capture_mode == 'websocket':
        logger.info("Audio capture via WebSocket (OBS must connect to /ws/audio)")

    elif audio_capture_mode == 'none':
        logger.warning("⚠️  Audio capture disabled - context matching will use generic news!")
        logger.warning("⚠️  Set audio_capture.mode in config.json to enable transcription")

    else:
        logger.error(f"Unknown audio_capture mode: {audio_capture_mode}")

    # Start background tasks
    asyncio.create_task(periodic_news_update())
    logger.info("Backend started successfully")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down...")
    if audio_capture:
        await audio_capture.stop()
    await transcription_manager.stop()
    await news_fetcher.stop()


async def periodic_news_update():
    """Periodically fetch news and match with context"""
    global current_headlines, last_update

    while True:
        try:
            refresh_interval = config['news']['refresh_interval_minutes'] * 60

            # Fetch latest news
            logger.info("Fetching news articles...")
            news_articles = await news_fetcher.fetch_all()
            logger.info(f"Fetched {len(news_articles)} news articles")

            # Get transcription context (last 5 minutes)
            context = transcription_manager.get_context()

            if context:
                logger.info(f"Matching news with context (length: {len(context)} chars)...")
                # Use Claude to match news with context
                matched_headlines = await context_matcher.match_news_with_context(
                    news_articles,
                    context,
                    top_k=config['matching']['top_results']
                )
                current_headlines = matched_headlines
                logger.info(f"Matched {len(current_headlines)} relevant headlines")
            else:
                # No context yet, just take top news
                logger.info("No context available yet, using top news")
                current_headlines = news_articles[:config['matching']['top_results']]

            last_update = datetime.now()

        except Exception as e:
            logger.error(f"Error in periodic news update: {e}", exc_info=True)

        await asyncio.sleep(refresh_interval)


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "running",
        "last_update": last_update.isoformat() if last_update else None,
        "headlines_count": len(current_headlines)
    }


@app.get("/headlines")
async def get_headlines():
    """Get current matched headlines"""
    return {
        "headlines": current_headlines,
        "last_update": last_update.isoformat() if last_update else None,
        "context_length": len(transcription_manager.get_context())
    }


@app.websocket("/ws/audio")
async def websocket_audio(websocket: WebSocket):
    """WebSocket endpoint for receiving audio data from OBS"""
    await websocket.accept()
    logger.info("Audio WebSocket connection established")

    MAX_AUDIO_CHUNK = 1024 * 1024  # 1 MB per chunk to prevent memory exhaustion

    try:
        while True:
            # Receive audio data
            data = await websocket.receive_bytes()

            # Validate chunk size to prevent DoS attacks
            if len(data) > MAX_AUDIO_CHUNK:
                logger.error(f"Audio chunk too large: {len(data)} bytes")
                await websocket.close(code=1009, reason="Message too large")
                break

            # Validate data is not empty
            if not data:
                logger.warning("Received empty audio data")
                continue

            # Send to transcription manager
            await transcription_manager.process_audio(data)

    except WebSocketDisconnect:
        logger.info("Audio WebSocket disconnected")
    except Exception as e:
        logger.error(f"Error in audio WebSocket: {e}", exc_info=True)


@app.get("/context")
async def get_context():
    """Get current transcription context (for debugging)"""
    context = transcription_manager.get_context()
    return {
        "context": context,
        "length": len(context),
        "buffer_duration": config['transcription']['buffer_duration_seconds']
    }


@app.post("/trigger-update")
async def trigger_update():
    """Manually trigger a news update (for testing)"""
    try:
        news_articles = await news_fetcher.fetch_all()
        context = transcription_manager.get_context()

        if context:
            matched_headlines = await context_matcher.match_news_with_context(
                news_articles,
                context,
                top_k=config['matching']['top_results']
            )
            global current_headlines, last_update
            current_headlines = matched_headlines
            last_update = datetime.now()

            return {
                "status": "success",
                "headlines": current_headlines,
                "context_used": True
            }
        else:
            return {
                "status": "success",
                "headlines": news_articles[:config['matching']['top_results']],
                "context_used": False,
                "message": "No context available, using top news"
            }
    except Exception as e:
        # Log the full error for debugging, but don't expose details to client
        logger.error(f"Error in trigger_update: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update headlines")


if __name__ == "__main__":
    host = config['backend']['host']
    port = config['backend']['port']
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
