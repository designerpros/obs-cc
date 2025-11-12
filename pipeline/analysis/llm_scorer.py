"""
LLM cascade scorer for topic quality evaluation
Cost-optimized multi-stage evaluation: Llama → Grok → Claude → GPT-4o
"""
import asyncio
from typing import Dict, Any, List, Optional
import httpx

from loguru import logger
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from ..common.config import config


class LLMScorer:
    """
    Multi-LLM consensus scorer with cascade optimization

    Pipeline:
    1. Local Llama 8B pre-filter (FREE, ~0.5s)
    2. Grok cheap screening ($0.01/call)
    3. Claude mid-tier ($0.015/call)
    4. GPT-4o + Perplexity expensive consensus ($0.03/call each)

    Final decision: 3/4 LLMs must score ≥80 (or ≥70 fallback)
    """

    def __init__(self):
        # LLM clients
        self.anthropic_client = None
        self.openai_client = None
        self.http_client = None

        # API keys
        self.grok_api_key = None
        self.perplexity_api_key = None

        # Thresholds
        self.consensus_threshold = config.get('analysis.coherence.consensus_threshold', 0.75)
        self.min_score = config.get('analysis.coherence.min_score_per_llm', 80)
        self.fallback_score = 70

    async def initialize(self):
        """Initialize LLM clients"""
        logger.info("Initializing LLM scorer with cascade model...")

        # Get API keys from config
        providers = config.get('analysis.coherence.llm_providers', [])

        for provider in providers:
            if provider['name'] == 'anthropic':
                self.anthropic_client = AsyncAnthropic(api_key=provider['api_key'])
                logger.info(f"Initialized Anthropic client (model: {provider['model']})")

            elif provider['name'] == 'openai':
                self.openai_client = AsyncOpenAI(api_key=provider['api_key'])
                logger.info(f"Initialized OpenAI client (model: {provider['model']})")

            elif provider['name'] == 'grok':
                self.grok_api_key = provider['api_key']
                logger.info(f"Initialized Grok client (model: {provider['model']})")

            elif provider['name'] == 'perplexity':
                self.perplexity_api_key = provider['api_key']
                logger.info(f"Initialized Perplexity client (model: {provider['model']})")

        # HTTP client for API calls
        self.http_client = httpx.AsyncClient(timeout=60.0)

        logger.info("LLM scorer initialized")

    async def cleanup(self):
        """Cleanup resources"""
        if self.anthropic_client:
            await self.anthropic_client.close()

        if self.openai_client:
            await self.openai_client.close()

        if self.http_client:
            await self.http_client.aclose()

        logger.info("LLM scorer cleaned up")

    async def score_topic(
        self,
        topic: Dict[str, Any],
        retries: int = 0,
    ) -> Dict[str, Any]:
        """
        Score a topic using LLM cascade

        Args:
            topic: Topic dictionary with text, title, description
            retries: Current retry count

        Returns:
            Scoring result with consensus decision
        """
        # Build scoring prompt
        prompt = self._build_scoring_prompt(topic)

        # Stage 1: Local Llama 8B pre-filter (if available)
        llama_score = await self._score_with_llama(prompt)

        if llama_score is not None:
            if llama_score < 60:
                logger.info(f"Topic '{topic['title'][:30]}' rejected by Llama pre-filter ({llama_score})")
                return {
                    'llm_scores': {'llama': llama_score},
                    'consensus_score': llama_score,
                    'consensus_approved': False,
                }

            if llama_score >= 90:
                logger.info(f"Topic '{topic['title'][:30]}' approved by Llama pre-filter ({llama_score})")
                # Still need at least one API confirmation
                pass

        # Stage 2: Grok cheap screening
        grok_score = await self._score_with_grok(prompt)

        if grok_score < 70:
            logger.info(f"Topic '{topic['title'][:30]}' rejected by Grok ({grok_score})")
            return {
                'llm_scores': {'llama': llama_score, 'grok': grok_score},
                'consensus_score': (llama_score + grok_score) / 2 if llama_score else grok_score,
                'consensus_approved': False,
            }

        # Stage 3: Claude mid-tier
        claude_score = await self._score_with_claude(prompt)

        # Check if we have 2/2 high confidence (Grok + Claude both ≥80)
        if grok_score >= self.min_score and claude_score >= self.min_score:
            logger.info(
                f"Topic '{topic['title'][:30]}' approved by Grok+Claude "
                f"({grok_score}, {claude_score})"
            )
            return {
                'llm_scores': {
                    'llama': llama_score,
                    'grok': grok_score,
                    'claude': claude_score,
                },
                'consensus_score': (grok_score + claude_score) / 2,
                'consensus_approved': True,
            }

        # Stage 4: Get expensive consensus (GPT-4o + Perplexity)
        logger.info(f"Topic '{topic['title'][:30]}' needs full consensus (Grok={grok_score}, Claude={claude_score})")

        gpt_score = await self._score_with_gpt4o(prompt)
        pplx_score = await self._score_with_perplexity(prompt)

        # Collect all scores
        all_scores = {
            'grok': grok_score,
            'claude': claude_score,
            'gpt4o': gpt_score,
            'perplexity': pplx_score,
        }

        if llama_score is not None:
            all_scores['llama'] = llama_score

        # Calculate consensus (need 3/4 ≥ min_score)
        api_scores = [grok_score, claude_score, gpt_score, pplx_score]
        passing_count = sum(1 for s in api_scores if s >= self.min_score)

        consensus_score = sum(api_scores) / len(api_scores)

        if passing_count >= 3:
            logger.info(
                f"Topic '{topic['title'][:30]}' APPROVED by consensus "
                f"({passing_count}/4 ≥{self.min_score}): {all_scores}"
            )
            return {
                'llm_scores': all_scores,
                'consensus_score': consensus_score,
                'consensus_approved': True,
            }

        # Try lower threshold fallback (≥70)
        passing_count_70 = sum(1 for s in api_scores if s >= self.fallback_score)

        if passing_count_70 >= 3:
            logger.info(
                f"Topic '{topic['title'][:30]}' APPROVED by fallback consensus "
                f"({passing_count_70}/4 ≥{self.fallback_score}): {all_scores}"
            )
            return {
                'llm_scores': all_scores,
                'consensus_score': consensus_score,
                'consensus_approved': True,
            }

        # Failed consensus - retry if allowed
        if retries < 2:
            logger.warning(
                f"Topic '{topic['title'][:30]}' failed consensus (attempt {retries+1}/3). "
                f"Scores: {all_scores}. Regenerating..."
            )

            # TODO: Regenerate segment with feedback
            # For now, just retry scoring
            await asyncio.sleep(1)
            return await self.score_topic(topic, retries + 1)

        # Permanent failure - log for manual review
        logger.warning(
            f"Topic '{topic['title'][:30]}' REJECTED after {retries+1} attempts. "
            f"Final scores: {all_scores}"
        )

        return {
            'llm_scores': all_scores,
            'consensus_score': consensus_score,
            'consensus_approved': False,
        }

    def _build_scoring_prompt(self, topic: Dict[str, Any]) -> str:
        """Build scoring prompt for LLMs"""
        return f"""Evaluate this video segment for quality and viral potential.

SEGMENT:
Title: {topic['title']}
Description: {topic['description']}
Category: {topic['category']}
Duration: {topic['end_time'] - topic['start_time']:.0f} seconds

EVALUATION CRITERIA:
1. Coherence (0-100): Does the segment flow logically? Clear structure?
2. Standalone Viability (0-100): Can this work as a standalone video without context?
3. Hook Strength (0-100): Does it have a compelling opening/hook?
4. Topic Relevance (0-100): Is the focus clear and specific?
5. Engagement Potential (0-100): Will viewers stay engaged? Interesting content?

Respond with ONLY a JSON object:
{{
  "coherence": 85,
  "standalone_viability": 90,
  "hook_strength": 75,
  "topic_relevance": 88,
  "engagement_potential": 80,
  "overall_score": 84,
  "reasoning": "Brief 1-sentence explanation"
}}

Overall score should be the average of all criteria."""

    async def _score_with_llama(self, prompt: str) -> Optional[float]:
        """
        Score with local Llama 8B (if available)

        Returns:
            Score 0-100 or None if not available
        """
        # TODO: Implement Ollama integration
        # For now, skip local Llama
        return None

    async def _score_with_grok(self, prompt: str) -> float:
        """Score with Grok API"""
        try:
            response = await self.http_client.post(
                "https://api.x.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.grok_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "grok-2-1212",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 500,
                },
            )

            response.raise_for_status()
            data = response.json()

            # Parse JSON response
            import json
            import re

            content = data['choices'][0]['message']['content']
            json_match = re.search(r'\{.*\}', content, re.DOTALL)

            if json_match:
                result = json.loads(json_match.group(0))
                return float(result.get('overall_score', 0))

            return 0.0

        except Exception as e:
            logger.error(f"Grok API error: {e}")
            return 50.0  # Neutral score on error

    async def _score_with_claude(self, prompt: str) -> float:
        """Score with Claude API"""
        try:
            response = await self.anthropic_client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=500,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}]
            )

            # Parse JSON response
            import json
            import re

            content = response.content[0].text
            json_match = re.search(r'\{.*\}', content, re.DOTALL)

            if json_match:
                result = json.loads(json_match.group(0))
                return float(result.get('overall_score', 0))

            return 0.0

        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return 50.0

    async def _score_with_gpt4o(self, prompt: str) -> float:
        """Score with GPT-4o API"""
        try:
            response = await self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500,
            )

            # Parse JSON response
            import json
            import re

            content = response.choices[0].message.content
            json_match = re.search(r'\{.*\}', content, re.DOTALL)

            if json_match:
                result = json.loads(json_match.group(0))
                return float(result.get('overall_score', 0))

            return 0.0

        except Exception as e:
            logger.error(f"GPT-4o API error: {e}")
            return 50.0

    async def _score_with_perplexity(self, prompt: str) -> float:
        """Score with Perplexity API"""
        try:
            response = await self.http_client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.perplexity_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.1-sonar-large-128k-online",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 500,
                },
            )

            response.raise_for_status()
            data = response.json()

            # Parse JSON response
            import json
            import re

            content = data['choices'][0]['message']['content']
            json_match = re.search(r'\{.*\}', content, re.DOTALL)

            if json_match:
                result = json.loads(json_match.group(0))
                return float(result.get('overall_score', 0))

            return 0.0

        except Exception as e:
            logger.error(f"Perplexity API error: {e}")
            return 50.0


logger.info("LLM scorer module loaded")
