"""
Transcription Worker
Processes streams with Whisper large-v3 + pyannote speaker diarization
"""
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

from loguru import logger
from fastapi import FastAPI
import uvicorn

# Add parent to path
sys.path.append(str(Path(__file__).parent.parent))

from common.config import config
from common.queue import init_queue, close_queue, job_queue, JobType, JobStatus
from common.db import get_db, Transcript, TranscriptSegment
from common.notifications import init_notifications, close_notifications, notifier

from .transcriber import WhisperTranscriber
from .diarizer import SpeakerDiarizer
from .embedding_generator import EmbeddingGenerator

# Initialize FastAPI for health checks
app = FastAPI(title="Transcription Worker", version="1.0.0")

# Global instances
transcriber: WhisperTranscriber = None
diarizer: SpeakerDiarizer = None
embedding_gen: EmbeddingGenerator = None
worker_id = f"transcription-{os.getenv('HOSTNAME', 'local')}"


@app.on_event("startup")
async def startup():
    """Initialize services on startup"""
    global transcriber, diarizer, embedding_gen

    logger.info("Starting Transcription Worker...")

    # Initialize dependencies
    await init_queue()
    await init_notifications()

    # Initialize transcriber
    transcriber = WhisperTranscriber()
    await transcriber.initialize()

    # Initialize diarizer
    diarizer = SpeakerDiarizer()
    await diarizer.initialize()

    # Initialize embedding generator
    embedding_gen = EmbeddingGenerator()
    await embedding_gen.initialize()

    # Start job processing loop
    asyncio.create_task(process_jobs())

    logger.info("Transcription Worker started successfully")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    logger.info("Shutting down Transcription Worker...")

    if transcriber:
        await transcriber.cleanup()

    if diarizer:
        await diarizer.cleanup()

    if embedding_gen:
        await embedding_gen.cleanup()

    await close_queue()
    await close_notifications()

    logger.info("Transcription Worker shut down")


async def process_jobs():
    """Main job processing loop"""
    logger.info("Job processing loop started")

    while True:
        try:
            # Dequeue next transcription job
            job_data = await job_queue.dequeue(
                job_type=JobType.TRANSCRIBE,
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
                # Process transcription
                await process_transcription(
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
                    f"❌ Transcription failed for stream {stream_id}: {str(e)}",
                    level="error",
                    stream_id=stream_id,
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Error in job processing loop: {e}")
            await asyncio.sleep(10)  # Back off on error


async def process_transcription(
    job_id: UUID,
    stream_id: UUID,
    payload: dict,
):
    """
    Process transcription for a stream

    Args:
        job_id: Job ID
        stream_id: Stream ID
        payload: Job payload with work_dir and file_mapping
    """
    work_dir = Path(payload['work_dir'])
    file_mapping = payload.get('file_mapping', {})

    # Get live_mix file for transcription
    live_mix_path = work_dir / "live_mix.mp4"

    if not live_mix_path.exists():
        raise FileNotFoundError(f"live_mix.mp4 not found in {work_dir}")

    logger.info(f"Transcribing {live_mix_path}")

    # Update progress
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=10,
        current_step="Extracting audio from video",
    )

    # Extract audio
    audio_path = await transcriber.extract_audio(live_mix_path)

    # Update progress
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=20,
        current_step="Running Whisper transcription",
    )

    # Transcribe with Whisper
    transcription_result = await transcriber.transcribe(audio_path)

    logger.info(f"Transcription completed: {len(transcription_result['segments'])} segments")

    # Update progress
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=60,
        current_step="Running speaker diarization",
    )

    # Run speaker diarization
    diarization_result = await diarizer.diarize(audio_path)

    logger.info(f"Diarization completed: {len(diarization_result['speakers'])} speakers")

    # Update progress
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=80,
        current_step="Merging transcription with diarization",
    )

    # Merge transcription with diarization
    merged_segments = await merge_transcription_and_diarization(
        transcription_result['segments'],
        diarization_result['segments'],
    )

    # Update progress
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=90,
        current_step="Generating embeddings",
    )

    # Generate embeddings
    full_text = " ".join(seg['text'] for seg in merged_segments)
    full_embedding = await embedding_gen.generate_embedding(full_text)

    segment_embeddings = await embedding_gen.generate_batch_embeddings(
        [seg['text'] for seg in merged_segments]
    )

    # Save to database
    async with get_db() as db:
        # Create transcript record
        transcript = Transcript(
            stream_id=stream_id,
            full_text=full_text,
            language=transcription_result.get('language', 'en'),
            confidence_score=transcription_result.get('confidence'),
            speakers=diarization_result['speakers'],
            embedding=full_embedding,
            transcription_engine='whisper-large-v3',
            processing_duration_seconds=transcription_result.get('duration'),
        )
        db.add(transcript)
        await db.flush()

        # Create transcript segments
        for i, segment in enumerate(merged_segments):
            segment_embedding = segment_embeddings[i] if i < len(segment_embeddings) else None

            transcript_segment = TranscriptSegment(
                transcript_id=transcript.id,
                stream_id=stream_id,
                start_time=segment['start'],
                end_time=segment['end'],
                text=segment['text'],
                speaker_id=segment.get('speaker_id'),
                speaker_name=segment.get('speaker_name'),
                confidence=segment.get('confidence'),
                words=segment.get('words'),
                embedding=segment_embedding,
            )
            db.add(transcript_segment)

        await db.commit()

        logger.info(f"Saved transcript {transcript.id} with {len(merged_segments)} segments")

    # Update progress
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=100,
        current_step="Transcription complete",
    )

    # Enqueue next job (analysis)
    await job_queue.enqueue(
        job_type=JobType.ANALYZE,
        stream_id=stream_id,
        payload={
            'work_dir': str(work_dir),
            'transcript_id': str(transcript.id),
        },
        priority=90,
    )

    logger.info(f"Enqueued analysis job for stream {stream_id}")


async def merge_transcription_and_diarization(
    transcription_segments: list,
    diarization_segments: list,
) -> list:
    """
    Merge Whisper transcription with pyannote diarization

    Assigns speaker IDs to transcription segments based on overlap
    """
    merged = []

    for trans_seg in transcription_segments:
        trans_start = trans_seg['start']
        trans_end = trans_seg['end']
        trans_mid = (trans_start + trans_end) / 2

        # Find overlapping speaker segments
        best_speaker = None
        best_overlap = 0

        for diar_seg in diarization_segments:
            diar_start = diar_seg['start']
            diar_end = diar_seg['end']

            # Calculate overlap
            overlap_start = max(trans_start, diar_start)
            overlap_end = min(trans_end, diar_end)
            overlap = max(0, overlap_end - overlap_start)

            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = diar_seg['speaker_id']

        merged.append({
            **trans_seg,
            'speaker_id': best_speaker,
            'speaker_name': f"Speaker {best_speaker}" if best_speaker else None,
        })

    return merged


@app.get("/")
async def root():
    """Health check"""
    return {
        "service": "Transcription Worker",
        "worker_id": worker_id,
        "status": "running",
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    # Get port from config
    port = int(os.getenv('PORT', '9091'))

    logger.info(f"Starting Transcription Worker on port {port}")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
