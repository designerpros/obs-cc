"""
Rendering Worker
Orchestrates video production: multi-cam mixing, B-roll, audio, rendering
"""
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID
from typing import List, Dict, Any

from loguru import logger
from fastapi import FastAPI
import uvicorn

# Add parent to path
sys.path.append(str(Path(__file__).parent.parent))

from common.config import config
from common.queue import init_queue, close_queue, job_queue, JobType
from common.db import get_db, Stream, Topic, Extraction
from common.notifications import init_notifications, close_notifications, notifier

from .video_mixer import VideoMixer
from .audio_processor import AudioProcessor
from .broll_inserter import BRollInserter
from .ffmpeg_renderer import FFmpegRenderer

# Initialize FastAPI for health checks
app = FastAPI(title="Rendering Worker", version="1.0.0")

# Global instances
video_mixer: VideoMixer = None
audio_processor: AudioProcessor = None
broll_inserter: BRollInserter = None
ffmpeg_renderer: FFmpegRenderer = None
worker_id = f"rendering-{os.getenv('HOSTNAME', 'local')}"


@app.on_event("startup")
async def startup():
    """Initialize services on startup"""
    global video_mixer, audio_processor, broll_inserter, ffmpeg_renderer

    logger.info("Starting Rendering Worker...")

    # Initialize dependencies
    await init_queue()
    await init_notifications()

    # Initialize components
    video_mixer = VideoMixer()
    await video_mixer.initialize()

    audio_processor = AudioProcessor()
    await audio_processor.initialize()

    broll_inserter = BRollInserter()
    await broll_inserter.initialize()

    ffmpeg_renderer = FFmpegRenderer()
    await ffmpeg_renderer.initialize()

    # Start job processing loop
    asyncio.create_task(process_jobs())

    logger.info("Rendering Worker started successfully")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    logger.info("Shutting down Rendering Worker...")

    if video_mixer:
        await video_mixer.cleanup()

    if audio_processor:
        await audio_processor.cleanup()

    if broll_inserter:
        await broll_inserter.cleanup()

    if ffmpeg_renderer:
        await ffmpeg_renderer.cleanup()

    await close_queue()
    await close_notifications()

    logger.info("Rendering Worker shut down")


async def process_jobs():
    """Main job processing loop"""
    logger.info("Job processing loop started")

    while True:
        try:
            # Dequeue next rendering job
            job_data = await job_queue.dequeue(
                job_type=JobType.RENDER,
                worker_id=worker_id,
                timeout=10,
            )

            if not job_data:
                # No jobs available, wait a bit
                await asyncio.sleep(5)
                continue

            job_id = UUID(job_data['job_id'])
            stream_id = UUID(job_data['stream_id'])
            payload = job_data['payload']

            logger.info(f"Processing job {job_id} for stream {stream_id}")

            try:
                # Process rendering
                await process_rendering(
                    job_id=job_id,
                    stream_id=stream_id,
                    payload=payload,
                )

                # Mark job as completed
                await job_queue.complete_job(job_id)

                logger.info(f"Job {job_id} completed successfully")

            except Exception as e:
                logger.exception(f"Error processing job {job_id}: {e}")

                # Mark job as failed (will retry if retries available)
                await job_queue.fail_job(
                    job_id=job_id,
                    error_message=str(e),
                    retry=True,
                )

                # Notify failure
                await notifier.notify(
                    f"❌ Rendering failed for stream {stream_id}: {str(e)}",
                    level="error",
                    stream_id=stream_id,
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Error in job processing loop: {e}")
            await asyncio.sleep(10)


async def process_rendering(
    job_id: UUID,
    stream_id: UUID,
    payload: dict,
):
    """
    Process rendering for extractions

    Args:
        job_id: Job ID
        stream_id: Stream ID
        payload: Job payload with extraction plan
    """
    work_dir = Path(payload['work_dir'])
    extraction_plan = payload['extraction_plan']

    logger.info(
        f"Rendering {extraction_plan['longs']} longs, "
        f"{extraction_plan['shorts']} shorts, "
        f"{extraction_plan['micros']} micros, "
        f"{extraction_plan['mediums']} mediums"
    )

    # Load stream from database
    async with get_db() as db:
        from sqlalchemy import select

        result = await db.execute(
            select(Stream).where(Stream.id == stream_id)
        )
        stream = result.scalar_one()

    # Get source files
    source_files = {
        'cam_main': Path(stream.cam_main_path),
        'cam_guest': Path(stream.cam_guest_path) if stream.cam_guest_path else None,
        'cam_overhead': Path(stream.cam_overhead_path) if stream.cam_overhead_path else None,
        'cam_screen': Path(stream.cam_screen_path) if stream.cam_screen_path else None,
        'cam_online_caller': Path(stream.cam_online_caller_path) if stream.cam_online_caller_path else None,
        'live_mix': Path(stream.live_mix_path),
    }

    # Process each extraction type
    all_extractions = []

    # Render longs
    for i, topic_data in enumerate(extraction_plan.get('long_topics', [])):
        await job_queue.update_progress(
            job_id=job_id,
            progress_percent=10 + int((i / max(len(extraction_plan.get('long_topics', [])), 1)) * 30),
            current_step=f"Rendering long {i+1}/{len(extraction_plan['long_topics'])}",
        )

        extraction = await render_extraction(
            stream_id=stream_id,
            work_dir=work_dir,
            source_files=source_files,
            topic_data=topic_data,
            extraction_type='long',
            format='landscape',
        )

        if extraction:
            all_extractions.append(extraction)

    # Render shorts
    for i, topic_data in enumerate(extraction_plan.get('short_topics', [])):
        await job_queue.update_progress(
            job_id=job_id,
            progress_percent=40 + int((i / max(len(extraction_plan.get('short_topics', [])), 1)) * 30),
            current_step=f"Rendering short {i+1}/{len(extraction_plan['short_topics'])}",
        )

        extraction = await render_extraction(
            stream_id=stream_id,
            work_dir=work_dir,
            source_files=source_files,
            topic_data=topic_data,
            extraction_type='short',
            format='portrait',
        )

        if extraction:
            all_extractions.append(extraction)

    # Render micros
    for i, topic_data in enumerate(extraction_plan.get('micro_topics', [])):
        await job_queue.update_progress(
            job_id=job_id,
            progress_percent=70 + int((i / max(len(extraction_plan.get('micro_topics', [])), 1)) * 15),
            current_step=f"Rendering micro {i+1}/{len(extraction_plan['micro_topics'])}",
        )

        extraction = await render_extraction(
            stream_id=stream_id,
            work_dir=work_dir,
            source_files=source_files,
            topic_data=topic_data,
            extraction_type='micro',
            format='portrait',
        )

        if extraction:
            all_extractions.append(extraction)

    # Render mediums
    for i, topic_data in enumerate(extraction_plan.get('medium_topics', [])):
        await job_queue.update_progress(
            job_id=job_id,
            progress_percent=85 + int((i / max(len(extraction_plan.get('medium_topics', [])), 1)) * 10),
            current_step=f"Rendering medium {i+1}/{len(extraction_plan['medium_topics'])}",
        )

        extraction = await render_extraction(
            stream_id=stream_id,
            work_dir=work_dir,
            source_files=source_files,
            topic_data=topic_data,
            extraction_type='medium',
            format='landscape',
        )

        if extraction:
            all_extractions.append(extraction)

    # Update progress
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=100,
        current_step="Rendering complete",
    )

    logger.info(f"Rendered {len(all_extractions)} total extractions")

    # Enqueue metadata generation job
    await job_queue.enqueue(
        job_type=JobType.GENERATE_METADATA,
        stream_id=stream_id,
        payload={
            'extraction_ids': [str(e.id) for e in all_extractions],
        },
        priority=70,
    )


async def render_extraction(
    stream_id: UUID,
    work_dir: Path,
    source_files: Dict[str, Path],
    topic_data: Dict[str, Any],
    extraction_type: str,
    format: str,
) -> Extraction:
    """
    Render a single extraction

    Args:
        stream_id: Stream ID
        work_dir: Working directory
        source_files: Dict of source file paths
        topic_data: Topic data with timestamps
        extraction_type: 'long', 'short', 'micro', or 'medium'
        format: 'landscape' or 'portrait'

    Returns:
        Extraction database record
    """
    start_time = topic_data['start_time']
    end_time = topic_data['end_time']
    title = topic_data.get('title', f'{extraction_type.title()} Clip')

    logger.info(f"Rendering {extraction_type} '{title}' ({start_time:.1f}s - {end_time:.1f}s)")

    # Create extraction output directory
    extraction_dir = work_dir / 'extractions' / f"{extraction_type}_{int(start_time)}"
    extraction_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Mix multi-camera sources
    logger.info("Step 1: Mixing multi-camera sources...")
    mixed_video_path = await video_mixer.mix_cameras(
        source_files=source_files,
        start_time=start_time,
        end_time=end_time,
        output_dir=extraction_dir,
        format=format,
    )

    # Step 2: Process audio
    logger.info("Step 2: Processing audio...")
    processed_audio_path = await audio_processor.process_audio(
        video_path=mixed_video_path,
        output_dir=extraction_dir,
        broll_style=topic_data.get('category', 'general'),
    )

    # Step 3: Insert B-roll
    logger.info("Step 3: Inserting B-roll...")
    broll_result = await broll_inserter.insert_broll(
        video_path=mixed_video_path,
        audio_path=processed_audio_path,
        topic_data=topic_data,
        output_dir=extraction_dir,
        format=format,
    )

    # Step 4: Final render with FFmpeg
    logger.info("Step 4: Final rendering...")
    final_video_path = await ffmpeg_renderer.render_final(
        video_path=broll_result['video_path'],
        audio_path=processed_audio_path,
        output_dir=extraction_dir,
        format=format,
        extraction_type=extraction_type,
    )

    # Save extraction to database
    async with get_db() as db:
        extraction = Extraction(
            stream_id=stream_id,
            topic_id=topic_data.get('id'),
            type=extraction_type,
            format=format,
            start_time=start_time,
            end_time=end_time,
            video_path=str(final_video_path),
            video_size_bytes=final_video_path.stat().st_size,
            broll_style=broll_result.get('style'),
            broll_count=broll_result.get('count', 0),
            status='rendered',
        )
        db.add(extraction)
        await db.commit()
        await db.refresh(extraction)

        logger.info(f"Saved extraction {extraction.id} to database")
        return extraction


@app.get("/")
async def root():
    """Health check"""
    return {
        "service": "Rendering Worker",
        "worker_id": worker_id,
        "status": "running",
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    # Get port from config
    port = int(os.getenv('PORT', '9093'))

    logger.info(f"Starting Rendering Worker on port {port}")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
