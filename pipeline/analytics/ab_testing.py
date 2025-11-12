"""
A/B Testing Framework
Tests title and thumbnail variants to optimize engagement
"""
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from uuid import UUID, uuid4
import json
from enum import Enum

from loguru import logger
from sqlalchemy import text
import scipy.stats as stats

from ..common.db import get_db
from ..common.config import config


class TestStatus(Enum):
    """A/B test status"""
    RUNNING = 'running'
    COMPLETED = 'completed'
    INCONCLUSIVE = 'inconclusive'
    CANCELLED = 'cancelled'


class ABTestingFramework:
    """
    A/B testing for titles and thumbnails

    Phase 5: Data-driven optimization
    - Create variant tests for titles and thumbnails
    - Statistical significance testing
    - Automatic winner selection
    - Learning from high-performers
    """

    def __init__(self):
        self.min_sample_size = config.get('ab_testing.min_sample_size', 100)
        self.confidence_level = config.get('ab_testing.confidence_level', 0.95)
        self.test_duration_hours = config.get('ab_testing.test_duration_hours', 48)

    async def initialize(self):
        """Initialize A/B testing framework"""
        logger.info(
            f"A/B testing framework initialized "
            f"(min_sample: {self.min_sample_size}, confidence: {self.confidence_level})"
        )

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("A/B testing framework cleaned up")

    async def create_title_test(
        self,
        extraction_id: UUID,
        platform: str,
        variant_titles: List[str],
    ) -> UUID:
        """
        Create A/B test for title variants

        Args:
            extraction_id: Extraction ID
            platform: Platform name
            variant_titles: List of title variants to test (2-4 variants)

        Returns:
            Test ID
        """
        if len(variant_titles) < 2:
            raise ValueError("Need at least 2 variants for A/B testing")

        if len(variant_titles) > 4:
            raise ValueError("Maximum 4 variants supported")

        test_id = uuid4()

        # Create test record
        async with get_db() as db:
            await db.execute(
                text("""
                    INSERT INTO ab_tests (
                        id, extraction_id, platform, test_type,
                        variant_count, variants, status,
                        min_sample_size, confidence_level,
                        started_at, expires_at
                    )
                    VALUES (
                        :id, :extraction_id, :platform, :test_type,
                        :variant_count, :variants, :status,
                        :min_sample_size, :confidence_level,
                        NOW(), NOW() + INTERVAL ':duration hours'
                    )
                """),
                {
                    'id': test_id,
                    'extraction_id': extraction_id,
                    'platform': platform,
                    'test_type': 'title',
                    'variant_count': len(variant_titles),
                    'variants': json.dumps({'titles': variant_titles}),
                    'status': TestStatus.RUNNING.value,
                    'min_sample_size': self.min_sample_size,
                    'confidence_level': self.confidence_level,
                    'duration': self.test_duration_hours,
                }
            )
            await db.commit()

        logger.info(
            f"Created title A/B test {test_id} with {len(variant_titles)} variants "
            f"for {platform}"
        )

        return test_id

    async def create_thumbnail_test(
        self,
        extraction_id: UUID,
        platform: str,
        variant_thumbnails: List[str],
    ) -> UUID:
        """
        Create A/B test for thumbnail variants

        Args:
            extraction_id: Extraction ID
            platform: Platform name
            variant_thumbnails: List of thumbnail paths to test

        Returns:
            Test ID
        """
        if len(variant_thumbnails) < 2:
            raise ValueError("Need at least 2 variants for A/B testing")

        test_id = uuid4()

        async with get_db() as db:
            await db.execute(
                text("""
                    INSERT INTO ab_tests (
                        id, extraction_id, platform, test_type,
                        variant_count, variants, status,
                        min_sample_size, confidence_level,
                        started_at, expires_at
                    )
                    VALUES (
                        :id, :extraction_id, :platform, :test_type,
                        :variant_count, :variants, :status,
                        :min_sample_size, :confidence_level,
                        NOW(), NOW() + INTERVAL ':duration hours'
                    )
                """),
                {
                    'id': test_id,
                    'extraction_id': extraction_id,
                    'platform': platform,
                    'test_type': 'thumbnail',
                    'variant_count': len(variant_thumbnails),
                    'variants': json.dumps({'thumbnails': variant_thumbnails}),
                    'status': TestStatus.RUNNING.value,
                    'min_sample_size': self.min_sample_size,
                    'confidence_level': self.confidence_level,
                    'duration': self.test_duration_hours,
                }
            )
            await db.commit()

        logger.info(
            f"Created thumbnail A/B test {test_id} with {len(variant_thumbnails)} variants"
        )

        return test_id

    async def record_impression(
        self,
        test_id: UUID,
        variant_index: int,
        clicked: bool = False,
    ):
        """
        Record an impression for a variant

        Args:
            test_id: Test ID
            variant_index: Which variant was shown (0-indexed)
            clicked: Whether user clicked/viewed
        """
        async with get_db() as db:
            await db.execute(
                text("""
                    INSERT INTO ab_test_impressions (
                        test_id, variant_index, clicked, created_at
                    )
                    VALUES (:test_id, :variant_index, :clicked, NOW())
                """),
                {
                    'test_id': test_id,
                    'variant_index': variant_index,
                    'clicked': clicked,
                }
            )
            await db.commit()

    async def evaluate_test(self, test_id: UUID) -> Dict[str, Any]:
        """
        Evaluate A/B test and determine winner

        Args:
            test_id: Test ID

        Returns:
            Test evaluation results
        """
        async with get_db() as db:
            # Get test info
            result = await db.execute(
                text("SELECT * FROM ab_tests WHERE id = :id"),
                {'id': test_id}
            )
            test = result.fetchone()

            if not test:
                raise ValueError(f"Test {test_id} not found")

            # Get impression data for each variant
            result = await db.execute(
                text("""
                    SELECT
                        variant_index,
                        COUNT(*) as impressions,
                        SUM(CASE WHEN clicked THEN 1 ELSE 0 END) as clicks
                    FROM ab_test_impressions
                    WHERE test_id = :test_id
                    GROUP BY variant_index
                    ORDER BY variant_index
                """),
                {'test_id': test_id}
            )

            variant_results = {}
            for row in result:
                variant_results[row.variant_index] = {
                    'impressions': row.impressions,
                    'clicks': row.clicks,
                    'ctr': (row.clicks / row.impressions * 100) if row.impressions > 0 else 0,
                }

        # Check if we have enough data
        total_impressions = sum(v['impressions'] for v in variant_results.values())

        if total_impressions < self.min_sample_size:
            logger.info(
                f"Test {test_id} insufficient data: "
                f"{total_impressions}/{self.min_sample_size} impressions"
            )
            return {
                'test_id': str(test_id),
                'status': TestStatus.RUNNING.value,
                'message': f"Insufficient data ({total_impressions}/{self.min_sample_size})",
                'variants': variant_results,
            }

        # Perform statistical significance test
        winner, is_significant, p_value = self._calculate_significance(variant_results)

        if is_significant:
            # Update test status
            async with get_db() as db:
                await db.execute(
                    text("""
                        UPDATE ab_tests
                        SET
                            status = :status,
                            winner_variant_index = :winner,
                            completed_at = NOW(),
                            results = :results
                        WHERE id = :id
                    """),
                    {
                        'id': test_id,
                        'status': TestStatus.COMPLETED.value,
                        'winner': winner,
                        'results': json.dumps({
                            'variant_results': variant_results,
                            'p_value': p_value,
                            'confidence_level': self.confidence_level,
                        }),
                    }
                )
                await db.commit()

            logger.info(
                f"Test {test_id} completed: Variant {winner} wins "
                f"(CTR: {variant_results[winner]['ctr']:.2f}%, p={p_value:.4f})"
            )

            return {
                'test_id': str(test_id),
                'status': TestStatus.COMPLETED.value,
                'winner_variant_index': winner,
                'winner_ctr': variant_results[winner]['ctr'],
                'p_value': p_value,
                'is_significant': True,
                'variants': variant_results,
            }

        else:
            # Check if test expired
            if datetime.utcnow() > test.expires_at:
                # Test expired without clear winner
                async with get_db() as db:
                    await db.execute(
                        text("""
                            UPDATE ab_tests
                            SET status = :status, completed_at = NOW()
                            WHERE id = :id
                        """),
                        {
                            'id': test_id,
                            'status': TestStatus.INCONCLUSIVE.value,
                        }
                    )
                    await db.commit()

                logger.warning(f"Test {test_id} expired without significant results")

                return {
                    'test_id': str(test_id),
                    'status': TestStatus.INCONCLUSIVE.value,
                    'message': 'Test expired without significant winner',
                    'variants': variant_results,
                }

            return {
                'test_id': str(test_id),
                'status': TestStatus.RUNNING.value,
                'message': 'No significant difference yet',
                'variants': variant_results,
                'p_value': p_value,
            }

    def _calculate_significance(
        self,
        variant_results: Dict[int, Dict[str, Any]]
    ) -> Tuple[Optional[int], bool, float]:
        """
        Calculate statistical significance using chi-square test

        Args:
            variant_results: Dict of {variant_index: {impressions, clicks, ctr}}

        Returns:
            Tuple of (winner_index, is_significant, p_value)
        """
        if len(variant_results) < 2:
            return None, False, 1.0

        # Extract data for chi-square test
        variant_indices = sorted(variant_results.keys())
        clicks = [variant_results[i]['clicks'] for i in variant_indices]
        impressions = [variant_results[i]['impressions'] for i in variant_indices]

        # Create contingency table
        # Rows: variants, Columns: [clicks, non-clicks]
        contingency = [
            [clicks[i], impressions[i] - clicks[i]]
            for i in range(len(variant_indices))
        ]

        # Perform chi-square test
        chi2, p_value, dof, expected = stats.chi2_contingency(contingency)

        # Determine if significant
        alpha = 1 - self.confidence_level
        is_significant = p_value < alpha

        # If significant, find winner (highest CTR)
        winner = None
        if is_significant:
            max_ctr = max(variant_results[i]['ctr'] for i in variant_indices)
            for i in variant_indices:
                if variant_results[i]['ctr'] == max_ctr:
                    winner = i
                    break

        return winner, is_significant, p_value

    async def get_test_insights(self, days: int = 30) -> Dict[str, Any]:
        """
        Get insights from past A/B tests

        Args:
            days: Number of days to analyze

        Returns:
            Test insights
        """
        async with get_db() as db:
            # Get completed tests
            result = await db.execute(
                text("""
                    SELECT
                        test_type,
                        platform,
                        COUNT(*) as test_count,
                        AVG((results->>'p_value')::float) as avg_p_value,
                        COUNT(*) FILTER (WHERE status = 'completed') as conclusive_tests,
                        COUNT(*) FILTER (WHERE status = 'inconclusive') as inconclusive_tests
                    FROM ab_tests
                    WHERE started_at >= NOW() - INTERVAL ':days days'
                    GROUP BY test_type, platform
                """),
                {'days': days}
            )

            test_summary = [
                {
                    'test_type': row.test_type,
                    'platform': row.platform,
                    'test_count': row.test_count,
                    'conclusive_tests': row.conclusive_tests,
                    'inconclusive_tests': row.inconclusive_tests,
                    'success_rate': (row.conclusive_tests / row.test_count * 100) if row.test_count > 0 else 0,
                }
                for row in result
            ]

        return {
            'period_days': days,
            'test_summary': test_summary,
        }


logger.info("A/B testing framework module loaded")
