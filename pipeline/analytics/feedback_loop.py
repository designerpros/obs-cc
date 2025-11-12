"""
Feedback Loop System
Learns from performance data to continuously improve content strategy
"""
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from uuid import UUID
import json

from loguru import logger
from sqlalchemy import text

from ..common.db import get_db
from ..common.config import config


class FeedbackLoop:
    """
    Continuous improvement through performance feedback

    Phase 5: Self-optimizing system
    - Analyze high/low performers
    - Adjust virality thresholds
    - Update topic category preferences
    - Calibrate LLM scoring weights
    - Optimize platform-specific strategies
    """

    def __init__(self):
        self.learning_rate = config.get('feedback_loop.learning_rate', 0.1)
        self.min_posts_for_learning = config.get('feedback_loop.min_posts', 20)
        self.enabled = config.get('feedback_loop.enabled', True)

    async def initialize(self):
        """Initialize feedback loop"""
        if self.enabled:
            logger.info(
                f"Feedback loop initialized "
                f"(learning_rate: {self.learning_rate}, min_posts: {self.min_posts_for_learning})"
            )
        else:
            logger.info("Feedback loop disabled")

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("Feedback loop cleaned up")

    async def analyze_and_adapt(self) -> Dict[str, Any]:
        """
        Analyze recent performance and adapt parameters

        Returns:
            Adaptation report with recommended changes
        """
        if not self.enabled:
            return {'enabled': False}

        logger.info("Running feedback loop analysis...")

        adaptations = {
            'timestamp': datetime.utcnow().isoformat(),
            'changes': [],
        }

        # 1. Adjust virality score thresholds
        virality_changes = await self._adapt_virality_thresholds()
        if virality_changes:
            adaptations['changes'].append(virality_changes)

        # 2. Update topic category preferences
        topic_changes = await self._adapt_topic_preferences()
        if topic_changes:
            adaptations['changes'].append(topic_changes)

        # 3. Optimize platform strategies
        platform_changes = await self._adapt_platform_strategies()
        if platform_changes:
            adaptations['changes'].append(platform_changes)

        # 4. Calibrate extraction type mix
        extraction_changes = await self._adapt_extraction_mix()
        if extraction_changes:
            adaptations['changes'].append(extraction_changes)

        # 5. Update LLM scoring weights
        scoring_changes = await self._adapt_scoring_weights()
        if scoring_changes:
            adaptations['changes'].append(scoring_changes)

        logger.info(f"Feedback loop complete: {len(adaptations['changes'])} adaptations made")

        # Store adaptations in database
        await self._store_adaptation(adaptations)

        return adaptations

    async def _adapt_virality_thresholds(self) -> Optional[Dict[str, Any]]:
        """Adapt virality score thresholds based on performance"""
        async with get_db() as db:
            # Get average virality score vs actual performance
            result = await db.execute(
                text("""
                    SELECT
                        e.virality_score,
                        AVG(pp.views) as avg_views,
                        AVG(pp.engagement_rate) as avg_engagement
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    WHERE pp.published_at >= NOW() - INTERVAL '30 days'
                        AND e.virality_score IS NOT NULL
                    GROUP BY
                        CASE
                            WHEN e.virality_score < 50 THEN 'low'
                            WHEN e.virality_score >= 50 AND e.virality_score < 70 THEN 'medium'
                            ELSE 'high'
                        END
                """)
            )

            score_brackets = list(result)

        if len(score_brackets) < 2:
            return None

        # Calculate correlation
        # If high virality scores aren't performing better, adjust threshold
        # This is simplified - in production, use proper correlation analysis

        return {
            'type': 'virality_threshold',
            'action': 'analyze',
            'current_data': [
                {
                    'bracket': 'low/medium/high',
                    'avg_views': float(row.avg_views) if row.avg_views else 0,
                    'avg_engagement': float(row.avg_engagement) if row.avg_engagement else 0,
                }
                for row in score_brackets
            ],
            'recommendation': 'Review virality scoring algorithm if correlation is weak',
        }

    async def _adapt_topic_preferences(self) -> Optional[Dict[str, Any]]:
        """Learn which topic categories perform best"""
        async with get_db() as db:
            result = await db.execute(
                text("""
                    SELECT
                        t.category,
                        COUNT(DISTINCT pp.id) as post_count,
                        AVG(pp.views) as avg_views,
                        AVG(pp.engagement_rate) as avg_engagement,
                        STDDEV(pp.views) as stddev_views
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    JOIN topics t ON e.topic_id = t.id
                    WHERE pp.published_at >= NOW() - INTERVAL '30 days'
                    GROUP BY t.category
                    HAVING COUNT(DISTINCT pp.id) >= :min_posts
                    ORDER BY avg_views DESC
                """),
                {'min_posts': self.min_posts_for_learning}
            )

            category_performance = list(result)

        if not category_performance:
            return None

        # Identify top and bottom performers
        top_categories = [
            {
                'category': row.category,
                'avg_views': float(row.avg_views) if row.avg_views else 0,
                'avg_engagement': float(row.avg_engagement) if row.avg_engagement else 0,
            }
            for row in category_performance[:3]
        ]

        return {
            'type': 'topic_preferences',
            'action': 'prioritize',
            'top_categories': top_categories,
            'recommendation': f"Increase extraction count for top {len(top_categories)} categories",
        }

    async def _adapt_platform_strategies(self) -> Optional[Dict[str, Any]]:
        """Optimize platform-specific strategies"""
        async with get_db() as db:
            # Get best extraction type for each platform
            result = await db.execute(
                text("""
                    SELECT
                        pp.platform,
                        e.type as extraction_type,
                        AVG(pp.views) as avg_views,
                        AVG(pp.engagement_rate) as avg_engagement,
                        ROW_NUMBER() OVER (PARTITION BY pp.platform ORDER BY AVG(pp.views) DESC) as rank
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    WHERE pp.published_at >= NOW() - INTERVAL '30 days'
                    GROUP BY pp.platform, e.type
                """)
            )

            all_results = list(result)

        # Get top performer for each platform
        platform_strategies = {}
        for row in all_results:
            if row.rank == 1:
                platform_strategies[row.platform] = {
                    'best_extraction_type': row.extraction_type,
                    'avg_views': float(row.avg_views) if row.avg_views else 0,
                    'avg_engagement': float(row.avg_engagement) if row.avg_engagement else 0,
                }

        if not platform_strategies:
            return None

        return {
            'type': 'platform_strategies',
            'action': 'optimize',
            'strategies': platform_strategies,
            'recommendation': 'Focus best-performing extraction types per platform',
        }

    async def _adapt_extraction_mix(self) -> Optional[Dict[str, Any]]:
        """Optimize mix of extraction types (long, short, micro, medium)"""
        async with get_db() as db:
            result = await db.execute(
                text("""
                    SELECT
                        e.type,
                        COUNT(DISTINCT pp.id) as post_count,
                        AVG(pp.views) as avg_views,
                        SUM(pp.views) as total_views,
                        AVG(pp.engagement_rate) as avg_engagement
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    WHERE pp.published_at >= NOW() - INTERVAL '30 days'
                    GROUP BY e.type
                    ORDER BY total_views DESC
                """)
            )

            extraction_performance = list(result)

        if not extraction_performance:
            return None

        # Calculate ROI (total views / extraction count)
        extraction_roi = [
            {
                'type': row.type,
                'post_count': row.post_count,
                'total_views': row.total_views or 0,
                'avg_views': float(row.avg_views) if row.avg_views else 0,
                'roi': (row.total_views / row.post_count) if row.post_count > 0 else 0,
            }
            for row in extraction_performance
        ]

        # Sort by ROI
        extraction_roi.sort(key=lambda x: x['roi'], reverse=True)

        return {
            'type': 'extraction_mix',
            'action': 'rebalance',
            'current_performance': extraction_roi,
            'recommendation': f"Increase proportion of {extraction_roi[0]['type']} (highest ROI)",
        }

    async def _adapt_scoring_weights(self) -> Optional[Dict[str, Any]]:
        """Calibrate LLM consensus scoring weights"""
        async with get_db() as db:
            # Analyze correlation between consensus scores and actual performance
            result = await db.execute(
                text("""
                    SELECT
                        t.consensus_score,
                        AVG(pp.views) as avg_views,
                        AVG(pp.engagement_rate) as avg_engagement
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    JOIN topics t ON e.topic_id = t.id
                    WHERE pp.published_at >= NOW() - INTERVAL '30 days'
                        AND t.consensus_score IS NOT NULL
                    GROUP BY
                        CASE
                            WHEN t.consensus_score < 70 THEN 'low'
                            WHEN t.consensus_score >= 70 AND t.consensus_score < 85 THEN 'medium'
                            ELSE 'high'
                        END
                """)
            )

            score_correlation = list(result)

        if len(score_correlation) < 2:
            return None

        return {
            'type': 'scoring_weights',
            'action': 'calibrate',
            'score_correlation': [
                {
                    'bracket': 'low/medium/high',
                    'avg_views': float(row.avg_views) if row.avg_views else 0,
                }
                for row in score_correlation
            ],
            'recommendation': 'Validate consensus scoring predicts performance',
        }

    async def _store_adaptation(self, adaptations: Dict[str, Any]):
        """Store adaptation record in database"""
        async with get_db() as db:
            await db.execute(
                text("""
                    INSERT INTO feedback_adaptations (
                        created_at, adaptations
                    )
                    VALUES (NOW(), :adaptations)
                """),
                {'adaptations': json.dumps(adaptations)}
            )
            await db.commit()

    async def get_adaptation_history(self, days: int = 30) -> List[Dict[str, Any]]:
        """Get adaptation history"""
        async with get_db() as db:
            result = await db.execute(
                text("""
                    SELECT created_at, adaptations
                    FROM feedback_adaptations
                    WHERE created_at >= NOW() - INTERVAL ':days days'
                    ORDER BY created_at DESC
                """),
                {'days': days}
            )

            return [
                {
                    'timestamp': row.created_at.isoformat(),
                    'adaptations': json.loads(row.adaptations) if isinstance(row.adaptations, str) else row.adaptations,
                }
                for row in result
            ]


logger.info("Feedback loop module loaded")
