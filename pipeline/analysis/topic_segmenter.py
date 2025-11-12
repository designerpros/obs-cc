"""
Topic segmentation using LLM analysis
"""
import asyncio
from typing import List, Dict, Any
from datetime import datetime

from loguru import logger
from anthropic import AsyncAnthropic

from ..common.config import config


class TopicSegmenter:
    """Segment transcript into coherent topics using Claude"""

    def __init__(self):
        self.anthropic_client = None
        self.model = config.get('analysis.topic_segmentation.model', 'claude-3-5-sonnet-20241022')
        self.min_topic_duration = config.get('analysis.topic_segmentation.min_topic_duration_seconds', 120)
        self.max_topics = config.get('analysis.topic_segmentation.max_topics_per_stream', 20)

    async def initialize(self):
        """Initialize LLM client"""
        api_key = config.get('analysis.topic_segmentation.api_key')
        if not api_key:
            raise Exception("Anthropic API key not configured")

        self.anthropic_client = AsyncAnthropic(api_key=api_key)
        logger.info(f"Topic segmenter initialized with model: {self.model}")

    async def cleanup(self):
        """Cleanup resources"""
        if self.anthropic_client:
            await self.anthropic_client.close()
        logger.info("Topic segmenter cleaned up")

    async def segment_topics(self, segments: List[Any]) -> List[Dict[str, Any]]:
        """
        Segment transcript into topics

        Args:
            segments: List of transcript segments from database

        Returns:
            List of topic dictionaries
        """
        # Build full transcript text with timestamps
        transcript_lines = []
        for seg in segments:
            timestamp = f"[{self._format_time(seg.start_time)} -> {self._format_time(seg.end_time)}]"
            speaker = f"[{seg.speaker_name or 'Unknown'}]" if seg.speaker_name else ""
            transcript_lines.append(f"{timestamp} {speaker} {seg.text}")

        full_transcript = "\n".join(transcript_lines)

        # Get categories
        categories = config.get('analysis.topic_segmentation.categories', [
            'crypto', 'finance', 'economics', 'politics', 'policy',
            'monetary_policy', 'business', 'technology', 'markets', 'other'
        ])

        # Build prompt
        prompt = f"""You are analyzing a livestream transcript to identify distinct topic segments.

TRANSCRIPT:
{full_transcript}

TASK:
Identify all major topic segments in this transcript. Each topic should:
- Be at least {self.min_topic_duration} seconds long
- Represent a distinct subject or discussion point
- Have clear beginning and end timestamps
- Be coherent and focused on one main subject

For each topic, provide:
1. Title (concise, descriptive)
2. Description (2-3 sentences summarizing the topic)
3. Category (choose from: {', '.join(categories)})
4. Start timestamp (format: MM:SS or HH:MM:SS)
5. End timestamp
6. Keywords (3-5 relevant keywords)
7. Coherence score (0-100): How well does this segment flow logically?
8. Standalone viability score (0-100): Could this work as a standalone video?
9. Engagement score (0-100): How interesting/engaging is this content?
10. Energy level (0-100): How energetic/passionate is the speaker?

Return ONLY a valid JSON array with this structure:
[
  {{
    "title": "Topic title",
    "description": "Brief description",
    "category": "category_name",
    "start_timestamp": "00:00",
    "end_timestamp": "02:30",
    "keywords": ["keyword1", "keyword2"],
    "coherence_score": 85,
    "standalone_viability_score": 90,
    "engagement_score": 80,
    "energy_level": 75
  }}
]

Maximum {self.max_topics} topics. Focus on the MOST significant segments."""

        # Call Claude
        logger.info("Calling Claude for topic segmentation...")

        response = await self.anthropic_client.messages.create(
            model=self.model,
            max_tokens=4096,
            temperature=0.3,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )

        # Parse response
        import json
        import re

        response_text = response.content[0].text

        # Extract JSON from response (might be wrapped in markdown code blocks)
        json_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', response_text, re.DOTALL)
        if json_match:
            json_text = json_match.group(1)
        else:
            # Try to find JSON array directly
            json_match = re.search(r'(\[.*\])', response_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(1)
            else:
                logger.error(f"Could not extract JSON from response: {response_text[:500]}")
                raise Exception("Failed to parse topic segmentation response")

        topics_data = json.loads(json_text)

        # Convert timestamps to seconds and validate
        topics = []
        for topic in topics_data:
            try:
                start_seconds = self._parse_timestamp(topic['start_timestamp'])
                end_seconds = self._parse_timestamp(topic['end_timestamp'])

                # Validate duration
                duration = end_seconds - start_seconds
                if duration < self.min_topic_duration:
                    logger.warning(
                        f"Topic '{topic['title']}' too short ({duration}s), skipping"
                    )
                    continue

                topics.append({
                    'title': topic['title'],
                    'description': topic['description'],
                    'category': topic['category'],
                    'start_time': start_seconds,
                    'end_time': end_seconds,
                    'keywords': topic.get('keywords', []),
                    'coherence_score': float(topic.get('coherence_score', 0)),
                    'standalone_viability_score': float(topic.get('standalone_viability_score', 0)),
                    'engagement_score': float(topic.get('engagement_score', 0)),
                    'energy_level': float(topic.get('energy_level', 0)),
                })

            except Exception as e:
                logger.warning(f"Error parsing topic {topic.get('title', 'unknown')}: {e}")
                continue

        logger.info(f"Segmented into {len(topics)} valid topics")
        return topics

    def _format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS or HH:MM:SS"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"

    def _parse_timestamp(self, timestamp: str) -> float:
        """Parse MM:SS or HH:MM:SS to seconds"""
        parts = timestamp.split(':')

        if len(parts) == 2:
            # MM:SS
            minutes, seconds = parts
            return int(minutes) * 60 + int(seconds)
        elif len(parts) == 3:
            # HH:MM:SS
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        else:
            raise ValueError(f"Invalid timestamp format: {timestamp}")


logger.info("Topic segmenter module loaded")
