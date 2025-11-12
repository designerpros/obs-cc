"""
Analysis Worker
LLM-powered topic segmentation and content scoring
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
from common.db import get_db, TranscriptSegment, Topic
from common.notifications import init_notifications, close_notifications, notifier

from .topic_segmenter import TopicSegmenter
from .llm_scorer import LLMScorer
from .extraction_planner import ExtractionPlanner

# Initialize FastAPI for health checks
app = FastAPI(title="Analysis Worker", version="1.0.0")

# Global instances
topic_segmenter: TopicSegmenter = None
llm_scorer: LLMScorer = None
extraction_planner: ExtractionPlanner = None
worker_id = f"analysis-{os.getenv('HOSTNAME', 'local')}"


@app.on_event("startup")
async def startup():
    """Initialize services on startup"""
    global topic_segmenter, llm_scorer, extraction_planner

    logger.info("Starting Analysis Worker...")

    # Initialize dependencies
    await init_queue()
    await init_notifications()

    # Initialize components
    topic_segmenter = TopicSegmenter()
    await topic_segmenter.initialize()

    llm_scorer = LLMScorer()
    await llm_scorer.initialize()

    extraction_planner = ExtractionPlanner()

    # Start job processing loop
    asyncio.create_task(process_jobs())

    logger.info("Analysis Worker started successfully")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    logger.info("Shutting down Analysis Worker...")

    if topic_segmenter:
        await topic_segmenter.cleanup()

    if llm_scorer:
        await llm_scorer.cleanup()

    await close_queue()
    await close_notifications()

    logger.info("Analysis Worker shut down")


async def process_jobs():
    """Main job processing loop"""
    logger.info("Job processing loop started")

    while True:
        try:
            # Dequeue next analysis job
            job_data = await job_queue.dequeue(
                job_type=JobType.ANALYZE,
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
                # Process analysis
                await process_analysis(
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
                    f"❌ Analysis failed for stream {stream_id}: {str(e)}",
                    level="error",
                    stream_id=stream_id,
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Error in job processing loop: {e}")
            await asyncio.sleep(10)


async def process_analysis(
    job_id: UUID,
    stream_id: UUID,
    payload: dict,
):
    """
    Process analysis for a stream

    Args:
        job_id: Job ID
        stream_id: Stream ID
        payload: Job payload with transcript_id
    """
    transcript_id = UUID(payload['transcript_id'])

    # Load transcript segments from database
    async with get_db() as db:
        from sqlalchemy import select

        result = await db.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.transcript_id == transcript_id)
            .order_by(TranscriptSegment.start_time)
        )
        segments = result.scalars().all()

    if not segments:
        raise Exception(f"No transcript segments found for transcript {transcript_id}")

    logger.info(f"Analyzing {len(segments)} transcript segments")

    # Update progress
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=10,
        current_step="Identifying topic boundaries",
    )

    # Segment transcript into topics
    topics = await topic_segmenter.segment_topics(segments)

    logger.info(f"Identified {len(topics)} topics")

    # Update progress
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=30,
        current_step=f"Scoring {len(topics)} topics with LLM cascade",
    )

    # Score each topic with LLM cascade
    scored_topics = []
    for i, topic in enumerate(topics):
        logger.info(f"Scoring topic {i+1}/{len(topics)}: {topic['title']}")

        # Update progress
        progress = 30 + int((i / len(topics)) * 50)
        await job_queue.update_progress(
            job_id=job_id,
            progress_percent=progress,
            current_step=f"Scoring topic: {topic['title'][:30]}...",
        )

        # Score with LLM cascade
        scores = await llm_scorer.score_topic(topic)

        # Add scores to topic
        topic['llm_scores'] = scores['llm_scores']
        topic['consensus_score'] = scores['consensus_score']
        topic['consensus_approved'] = scores['consensus_approved']

        scored_topics.append(topic)

        logger.info(
            f"Topic '{topic['title']}' consensus: {scores['consensus_score']:.1f} "
            f"(approved: {scores['consensus_approved']})"
        )

    # Update progress
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=80,
        current_step="Saving topics to database",
    )

    # Save topics to database
    topic_ids = []
    async with get_db() as db:
        for topic_data in scored_topics:
            topic = Topic(
                stream_id=stream_id,
                title=topic_data['title'],
                description=topic_data['description'],
                category=topic_data['category'],
                keywords=topic_data['keywords'],
                start_time=topic_data['start_time'],
                end_time=topic_data['end_time'],
                coherence_score=topic_data.get('coherence_score', 0),
                standalone_viability_score=topic_data.get('standalone_viability_score', 0),
                engagement_score=topic_data.get('engagement_score'),
                energy_level=topic_data.get('energy_level'),
                llm_scores=topic_data['llm_scores'],
                consensus_score=topic_data['consensus_score'],
                consensus_approved=topic_data['consensus_approved'],
                embedding=topic_data.get('embedding'),
            )
            db.add(topic)
            await db.flush()
            topic_ids.append(topic.id)

        await db.commit()

    logger.info(f"Saved {len(topic_ids)} topics to database")

    # Update progress
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=90,
        current_step="Planning extractions",
    )

    # Plan extractions based on approved topics
    extraction_plan = await extraction_planner.plan_extractions(
        stream_id=stream_id,
        topics=scored_topics,
    )

    logger.info(
        f"Extraction plan: {extraction_plan['longs']} longs, "
        f"{extraction_plan['shorts']} shorts, "
        f"{extraction_plan['micros']} micros, "
        f"{extraction_plan['mediums']} mediums"
    )

    # Update progress
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=100,
        current_step="Analysis complete",
    )

    # Enqueue extraction job
    await job_queue.enqueue(
        job_type=JobType.EXTRACT,
        stream_id=stream_id,
        payload={
            'transcript_id': str(transcript_id),
            'topic_ids': [str(tid) for tid in topic_ids],
            'extraction_plan': extraction_plan,
        },
        priority=80,
    )

    logger.info(f"Enqueued extraction job for stream {stream_id}")


@app.get("/")
async def root():
    """Health check"""
    return {
        "service": "Analysis Worker",
        "worker_id": worker_id,
        "status": "running",
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    # Get port from config
    port = int(os.getenv('PORT', '9092'))

    logger.info(f"Starting Analysis Worker on port {port}")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
