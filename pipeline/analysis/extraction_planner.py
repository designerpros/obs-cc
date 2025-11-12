"""
Extraction planner - determines which segments to extract as clips
"""
from typing import List, Dict, Any
from uuid import UUID

from loguru import logger

from ..common.config import config


class ExtractionPlanner:
    """Plan extractions based on approved topics"""

    def __init__(self):
        # Get target counts from config
        self.long_count = config.get('extraction.targets.long_form.count', 3)
        self.short_count_min = config.get('extraction.targets.shorts.count_min', 6)
        self.short_count_max = config.get('extraction.targets.shorts.count_max', 8)

        # Durations
        self.long_min_duration = config.get('extraction.targets.long_form.duration_min_seconds', 300)
        self.long_max_duration = config.get('extraction.targets.long_form.duration_max_seconds', 900)
        self.short_min_duration = config.get('extraction.targets.shorts.duration_min_seconds', 30)
        self.short_max_duration = config.get('extraction.targets.shorts.duration_max_seconds', 90)
        self.micro_min_duration = 10
        self.micro_max_duration = 20
        self.medium_min_duration = 180
        self.medium_max_duration = 600

    async def plan_extractions(
        self,
        stream_id: UUID,
        topics: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Plan extractions from approved topics

        Args:
            stream_id: Stream ID
            topics: List of scored topics

        Returns:
            Extraction plan with counts and selected topics
        """
        # Filter approved topics
        approved_topics = [t for t in topics if t.get('consensus_approved', False)]

        if not approved_topics:
            logger.warning("No approved topics found for extraction")
            return {
                'longs': 0,
                'shorts': 0,
                'micros': 0,
                'mediums': 0,
                'long_topics': [],
                'short_topics': [],
                'micro_topics': [],
                'medium_topics': [],
            }

        # Sort by consensus score
        approved_topics.sort(key=lambda t: t.get('consensus_score', 0), reverse=True)

        logger.info(f"Planning extractions from {len(approved_topics)} approved topics")

        # Categorize topics by duration
        long_candidates = []
        medium_candidates = []
        short_candidates = []
        micro_candidates = []

        for topic in approved_topics:
            duration = topic['end_time'] - topic['start_time']

            # Long-form (5-15 min)
            if self.long_min_duration <= duration <= self.long_max_duration:
                long_candidates.append(topic)

            # Medium (3-10 min)
            if self.medium_min_duration <= duration <= self.medium_max_duration:
                medium_candidates.append(topic)

            # Short (30-90s)
            if self.short_min_duration <= duration <= self.short_max_duration:
                short_candidates.append(topic)

            # Micro (10-20s) - extract hooks from longer topics
            if duration > 60:  # Can extract micro from longer topics
                micro_candidates.append(topic)

        # Select best topics for each category
        selected_longs = long_candidates[:self.long_count]
        selected_mediums = medium_candidates[:3]  # Up to 3 mediums
        selected_shorts = short_candidates[:self.short_count_max]
        selected_micros = micro_candidates[:5]  # Up to 5 micros

        # Ensure minimum shorts
        if len(selected_shorts) < self.short_count_min:
            # Try to create more shorts by splitting longer topics
            additional_needed = self.short_count_min - len(selected_shorts)
            logger.info(f"Need {additional_needed} more shorts, will split longer topics")

            # For now, just extract from beginning of long topics
            for topic in long_candidates[:additional_needed]:
                # Create short from first 60 seconds
                short_topic = topic.copy()
                short_topic['end_time'] = min(
                    topic['start_time'] + 60,
                    topic['end_time']
                )
                selected_shorts.append(short_topic)

        logger.info(
            f"Extraction plan: {len(selected_longs)} longs, "
            f"{len(selected_shorts)} shorts, "
            f"{len(selected_micros)} micros, "
            f"{len(selected_mediums)} mediums"
        )

        return {
            'longs': len(selected_longs),
            'shorts': len(selected_shorts),
            'micros': len(selected_micros),
            'mediums': len(selected_mediums),
            'long_topics': selected_longs,
            'short_topics': selected_shorts,
            'micro_topics': selected_micros,
            'medium_topics': selected_mediums,
        }


logger.info("Extraction planner module loaded")
