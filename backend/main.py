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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load configuration
CONFIG_PATH = Path(__file__).parent.parent / "config.json"
if not CONFIG_PATH.exists():
    CONFIG_PATH = Path(__file__).parent.parent / "config.example.json"
    logger.warning("config.json not found, using config.example.json. Please create config.json with your API keys.")

with open(CONFIG_PATH) as f:
    config = json.load(f)

# Initialize FastAPI app
app = FastAPI(title="OBS News Ticker Backend")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
transcription_manager = TranscriptionManager(config)
news_fetcher = NewsFetcher(config)
context_matcher = ContextMatcher(config)
current_headlines: List[Dict] = []
last_update: Optional[datetime] = None


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    logger.info("Starting OBS News Ticker Backend...")
    await news_fetcher.start()
    await transcription_manager.start()

    # Start background tasks
    asyncio.create_task(periodic_news_update())
    logger.info("Backend started successfully")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down...")
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

    try:
        while True:
            # Receive audio data
            data = await websocket.receive_bytes()

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
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    host = config['backend']['host']
    port = config['backend']['port']
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
