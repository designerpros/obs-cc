"""
Auto Tuner
Automatically adjusts system parameters based on performance feedback
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


class AutoTuner:
    """
    Self-tuning parameter optimization

    Phase 6: Advanced automation
    - Automatically adjust extraction counts
    - Optimize LLM scoring thresholds
    - Tune virality score cutoffs
    - Calibrate platform posting quotas
    - Adapt based on performance trends
    """

    def __init__(self):
        self.enabled = config.get('auto_tuner.enabled', True)
        self.tuning_interval_days = config.get('auto_tuner.interval_days', 7)
        self.min_sample_size = config.get('auto_tuner.min_sample_size', 50)
        self.adjustment_step_size = config.get('auto_tuner.step_size', 0.05)  # 5% adjustments

    async def initialize(self):
        """Initialize auto tuner"""
        if self.enabled:
            logger.info(
                f"Auto tuner initialized "
                f"(interval: {self.tuning_interval_days} days, "
                f"min_sample: {self.min_sample_size})"
            )
        else:
            logger.info("Auto tuner disabled")

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("Auto tuner cleaned up")

    async def run_tuning_cycle(self) -> Dict[str, Any]:
        """
        Run complete tuning cycle

        Returns:
            Tuning results with parameter adjustments
        """
        if not self.enabled:
            return {'enabled': False}

        logger.info("Running auto-tuning cycle...")

        tuning_results = {
            'timestamp': datetime.utcnow().isoformat(),
            'adjustments': [],
        }

        # 1. Tune extraction counts
        extraction_adjustment = await self._tune_extraction_counts()
        if extraction_adjustment:
            tuning_results['adjustments'].append(extraction_adjustment)

        # 2. Tune LLM scoring thresholds
        scoring_adjustment = await self._tune_llm_thresholds()
        if scoring_adjustment:
            tuning_results['adjustments'].append(scoring_adjustment)

        # 3. Tune virality cutoffs
        virality_adjustment = await self._tune_virality_cutoffs()
        if virality_adjustment:
            tuning_results['adjustments'].append(virality_adjustment)

        # 4. Tune platform quotas
        quota_adjustment = await self._tune_platform_quotas()
        if quota_adjustment:
            tuning_results['adjustments'].append(quota_adjustment)

        # 5. Tune B-roll insertion parameters
        broll_adjustment = await self._tune_broll_parameters()
        if broll_adjustment:
            tuning_results['adjustments'].append(broll_adjustment)

        # Store tuning results
        await self._store_tuning_results(tuning_results)

        logger.info(f"Auto-tuning complete: {len(tuning_results['adjustments'])} adjustments")

        return tuning_results

    async def _tune_extraction_counts(self) -> Optional[Dict[str, Any]]:
        """Tune extraction counts based on performance ROI"""
        async with get_db() as db:
            # Get ROI by extraction type
            result = await db.execute(
                text("""
                    SELECT
                        e.type,
                        COUNT(DISTINCT pp.id) as post_count,
                        SUM(pp.views) as total_views,
                        AVG(pp.views) as avg_views,
                        SUM(pp.views) / COUNT(DISTINCT e.stream_id) as views_per_stream
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    WHERE pp.published_at >= NOW() - INTERVAL ':days days'
                    GROUP BY e.type
                    HAVING COUNT(DISTINCT pp.id) >= :min_sample
                """),
                {
                    'days': self.tuning_interval_days,
                    'min_sample': self.min_sample_size,
                }
            )

            extraction_roi = list(result)

        if len(extraction_roi) < 2:
            return None

        # Sort by views per stream (ROI metric)
        sorted_by_roi = sorted(
            extraction_roi,
            key=lambda x: x.views_per_stream,
            reverse=True
        )

        # Calculate current distribution vs optimal
        total_posts = sum(e.post_count for e in extraction_roi)
        current_distribution = {
            e.type: e.post_count / total_posts
            for e in extraction_roi
        }

        # Proposed: shift 5% from lowest to highest ROI
        highest_roi = sorted_by_roi[0]
        lowest_roi = sorted_by_roi[-1]

        proposed_adjustments = {
            highest_roi.type: +self.adjustment_step_size,
            lowest_roi.type: -self.adjustment_step_size,
        }

        return {
            'parameter': 'extraction_counts',
            'current_distribution': {
                k: f"{v*100:.1f}%" for k, v in current_distribution.items()
            },
            'roi_analysis': [
                {
                    'type': e.type,
                    'views_per_stream': float(e.views_per_stream),
                    'avg_views': float(e.avg_views),
                }
                for e in sorted_by_roi
            ],
            'proposed_adjustments': proposed_adjustments,
            'action': f"Increase {highest_roi.type} by 5%, decrease {lowest_roi.type} by 5%",
        }

    async def _tune_llm_thresholds(self) -> Optional[Dict[str, Any]]:
        """Tune LLM consensus score thresholds"""
        async with get_db() as db:
            # Analyze correlation between consensus score and performance
            result = await db.execute(
                text("""
                    SELECT
                        CASE
                            WHEN t.consensus_score >= 90 THEN 'very_high'
                            WHEN t.consensus_score >= 80 THEN 'high'
                            WHEN t.consensus_score >= 70 THEN 'medium'
                            ELSE 'low'
                        END as score_bracket,
                        AVG(t.consensus_score) as avg_score,
                        COUNT(DISTINCT pp.id) as post_count,
                        AVG(pp.views) as avg_views,
                        AVG(pp.engagement_rate) as avg_engagement
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    JOIN topics t ON e.topic_id = t.id
                    WHERE pp.published_at >= NOW() - INTERVAL ':days days'
                        AND t.consensus_score IS NOT NULL
                    GROUP BY score_bracket
                    ORDER BY avg_score DESC
                """),
                {'days': self.tuning_interval_days}
            )

            score_performance = list(result)

        if len(score_performance) < 2:
            return None

        # Calculate performance delta between brackets
        high_bracket = next((s for s in score_performance if s.score_bracket == 'high'), None)
        medium_bracket = next((s for s in score_performance if s.score_bracket == 'medium'), None)

        if not high_bracket or not medium_bracket:
            return None

        # If medium performers are very close to high performers, lower threshold
        performance_ratio = medium_bracket.avg_views / high_bracket.avg_views if high_bracket.avg_views > 0 else 0

        if performance_ratio > 0.85:
            recommendation = "Lower threshold from 80 to 75 (medium bracket performing well)"
        elif performance_ratio < 0.5:
            recommendation = "Raise threshold from 80 to 85 (larger quality gap)"
        else:
            recommendation = "Keep current threshold (appropriate separation)"

        return {
            'parameter': 'llm_consensus_threshold',
            'current_threshold': 80,
            'performance_analysis': [
                {
                    'bracket': s.score_bracket,
                    'avg_score': float(s.avg_score),
                    'avg_views': float(s.avg_views) if s.avg_views else 0,
                    'post_count': s.post_count,
                }
                for s in score_performance
            ],
            'performance_ratio': performance_ratio,
            'recommendation': recommendation,
        }

    async def _tune_virality_cutoffs(self) -> Optional[Dict[str, Any]]:
        """Tune virality score cutoffs"""
        async with get_db() as db:
            # Get virality score distribution vs performance
            result = await db.execute(
                text("""
                    SELECT
                        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY e.virality_score) as q1,
                        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY e.virality_score) as median,
                        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY e.virality_score) as q3,
                        AVG(pp.views) FILTER (WHERE e.virality_score >= 70) as avg_views_high,
                        AVG(pp.views) FILTER (WHERE e.virality_score < 70) as avg_views_low
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    WHERE pp.published_at >= NOW() - INTERVAL ':days days'
                        AND e.virality_score IS NOT NULL
                """),
                {'days': self.tuning_interval_days}
            )

            stats = result.fetchone()

        if not stats:
            return None

        # Calculate if current 70 cutoff is optimal
        high_low_ratio = stats.avg_views_high / stats.avg_views_low if stats.avg_views_low > 0 else 1

        if high_low_ratio < 1.2:
            recommendation = f"Lower cutoff to {int(stats.median)} (high/low ratio too small)"
        elif high_low_ratio > 2.0:
            recommendation = f"Raise cutoff to {int(stats.q3)} (strong differentiation)"
        else:
            recommendation = "Keep current cutoff at 70 (appropriate balance)"

        return {
            'parameter': 'virality_cutoff',
            'current_cutoff': 70,
            'distribution': {
                'q1': float(stats.q1) if stats.q1 else 0,
                'median': float(stats.median) if stats.median else 0,
                'q3': float(stats.q3) if stats.q3 else 0,
            },
            'performance': {
                'avg_views_high': float(stats.avg_views_high) if stats.avg_views_high else 0,
                'avg_views_low': float(stats.avg_views_low) if stats.avg_views_low else 0,
                'ratio': high_low_ratio,
            },
            'recommendation': recommendation,
        }

    async def _tune_platform_quotas(self) -> Optional[Dict[str, Any]]:
        """Tune posting quotas per platform based on performance"""
        async with get_db() as db:
            # Get platform performance
            result = await db.execute(
                text("""
                    SELECT
                        pp.platform,
                        COUNT(*) as post_count,
                        AVG(pp.views) as avg_views,
                        AVG(pp.engagement_rate) as avg_engagement,
                        SUM(pp.views) as total_views
                    FROM platform_posts pp
                    WHERE pp.published_at >= NOW() - INTERVAL ':days days'
                    GROUP BY pp.platform
                    ORDER BY avg_views DESC
                """),
                {'days': self.tuning_interval_days}
            )

            platform_performance = list(result)

        if len(platform_performance) < 3:
            return None

        # Calculate ROI (total views per post)
        platform_roi = [
            {
                'platform': p.platform,
                'post_count': p.post_count,
                'avg_views': float(p.avg_views) if p.avg_views else 0,
                'roi': float(p.total_views / p.post_count) if p.post_count > 0 else 0,
            }
            for p in platform_performance
        ]

        # Sort by ROI
        sorted_by_roi = sorted(platform_roi, key=lambda x: x['roi'], reverse=True)

        # Top 3 and bottom 3
        top_platforms = sorted_by_roi[:3]
        bottom_platforms = sorted_by_roi[-3:]

        return {
            'parameter': 'platform_quotas',
            'top_performers': top_platforms,
            'bottom_performers': bottom_platforms,
            'recommendation': f"Prioritize {top_platforms[0]['platform']}, reduce {bottom_platforms[-1]['platform']}",
        }

    async def _tune_broll_parameters(self) -> Optional[Dict[str, Any]]:
        """Tune B-roll insertion parameters"""
        async with get_db() as db:
            # Compare posts with vs without B-roll
            result = await db.execute(
                text("""
                    SELECT
                        e.has_broll,
                        COUNT(*) as post_count,
                        AVG(pp.views) as avg_views,
                        AVG(pp.engagement_rate) as avg_engagement
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    WHERE pp.published_at >= NOW() - INTERVAL ':days days'
                    GROUP BY e.has_broll
                """),
                {'days': self.tuning_interval_days}
            )

            broll_comparison = list(result)

        if len(broll_comparison) < 2:
            return {
                'parameter': 'broll_insertion',
                'status': 'insufficient_data',
            }

        with_broll = next((b for b in broll_comparison if b.has_broll), None)
        without_broll = next((b for b in broll_comparison if not b.has_broll), None)

        if not with_broll or not without_broll:
            return None

        # Calculate performance lift from B-roll
        views_lift = (with_broll.avg_views / without_broll.avg_views - 1) * 100 if without_broll.avg_views > 0 else 0

        if views_lift > 10:
            recommendation = "Increase B-roll usage (strong positive impact)"
        elif views_lift < -10:
            recommendation = "Decrease B-roll usage (negative impact)"
        else:
            recommendation = "Maintain current B-roll usage (neutral impact)"

        return {
            'parameter': 'broll_insertion',
            'with_broll': {
                'post_count': with_broll.post_count,
                'avg_views': float(with_broll.avg_views) if with_broll.avg_views else 0,
            },
            'without_broll': {
                'post_count': without_broll.post_count,
                'avg_views': float(without_broll.avg_views) if without_broll.avg_views else 0,
            },
            'views_lift_percent': views_lift,
            'recommendation': recommendation,
        }

    async def _store_tuning_results(self, results: Dict[str, Any]):
        """Store tuning results in database"""
        async with get_db() as db:
            await db.execute(
                text("""
                    INSERT INTO auto_tuning_history (
                        created_at, tuning_results
                    )
                    VALUES (NOW(), :results)
                """),
                {'results': json.dumps(results)}
            )
            await db.commit()

    async def get_tuning_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent tuning history"""
        async with get_db() as db:
            result = await db.execute(
                text("""
                    SELECT created_at, tuning_results
                    FROM auto_tuning_history
                    ORDER BY created_at DESC
                    LIMIT :limit
                """),
                {'limit': limit}
            )

            return [
                {
                    'timestamp': row.created_at.isoformat(),
                    'results': json.loads(row.tuning_results) if isinstance(row.tuning_results, str) else row.tuning_results,
                }
                for row in result
            ]


logger.info("Auto tuner module loaded")
