"""
LLM approver for metadata quality validation
Multi-LLM consensus for titles, descriptions, thumbnails
"""
import asyncio
from typing import Dict, Any, Tuple

from loguru import logger
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
import httpx

from ..common.config import config


class LLMApprover:
    """Multi-LLM consensus approver for metadata"""

    def __init__(self):
        self.anthropic_client = None
        self.openai_client = None
        self.http_client = None

        self.grok_api_key = None
        self.perplexity_api_key = None

        self.consensus_threshold = 0.75  # 3/4 must approve
        self.min_score = 80

    async def initialize(self):
        """Initialize LLM clients"""
        # Get API keys from environment
        import os

        anthropic_key = os.getenv('ANTHROPIC_API_KEY')
        openai_key = os.getenv('OPENAI_API_KEY')
        self.grok_api_key = os.getenv('GROK_API_KEY')
        self.perplexity_api_key = os.getenv('PERPLEXITY_API_KEY')

        if anthropic_key:
            self.anthropic_client = AsyncAnthropic(api_key=anthropic_key)

        if openai_key:
            self.openai_client = AsyncOpenAI(api_key=openai_key)

        self.http_client = httpx.AsyncClient(timeout=60.0)

        logger.info("LLM approver initialized")

    async def cleanup(self):
        """Cleanup resources"""
        if self.anthropic_client:
            await self.anthropic_client.close()

        if self.openai_client:
            await self.openai_client.close()

        if self.http_client:
            await self.http_client.aclose()

        logger.info("LLM approver cleaned up")

    async def approve_metadata(
        self,
        title: str,
        description: str,
        hashtags: list,
        platform: str,
        extraction_type: str,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Get multi-LLM approval for metadata

        Args:
            title: Generated title
            description: Generated description
            hashtags: Generated hashtags
            platform: Platform name
            extraction_type: 'long', 'short', etc.

        Returns:
            Tuple of (approved: bool, scores: dict)
        """
        # Build evaluation prompt
        prompt = f"""Evaluate this {platform} metadata for quality.

TITLE: {title}

DESCRIPTION: {description[:500]}...

HASHTAGS: {', '.join('#' + h for h in hashtags)}

PLATFORM: {platform}
TYPE: {extraction_type}

SCORE (0-100) on these criteria:
1. Clickability: Will people click? Is it compelling?
2. Accuracy: Does it accurately represent content?
3. SEO: Good keywords and searchability?
4. Platform Appropriateness: Fits platform style?

Respond with ONLY a JSON object:
{{
  "clickability": 85,
  "accuracy": 90,
  "seo": 80,
  "platform_appropriateness": 88,
  "overall_score": 86,
  "reasoning": "Brief 1-sentence explanation"
}}"""

        # Get scores from all LLMs
        scores = {}

        # Claude
        if self.anthropic_client:
            try:
                scores['claude'] = await self._score_with_claude(prompt)
            except Exception as e:
                logger.error(f"Claude scoring error: {e}")
                scores['claude'] = 50

        # GPT-4o
        if self.openai_client:
            try:
                scores['gpt4o'] = await self._score_with_gpt4o(prompt)
            except Exception as e:
                logger.error(f"GPT-4o scoring error: {e}")
                scores['gpt4o'] = 50

        # Grok
        if self.grok_api_key:
            try:
                scores['grok'] = await self._score_with_grok(prompt)
            except Exception as e:
                logger.error(f"Grok scoring error: {e}")
                scores['grok'] = 50

        # Perplexity
        if self.perplexity_api_key:
            try:
                scores['perplexity'] = await self._score_with_perplexity(prompt)
            except Exception as e:
                logger.error(f"Perplexity scoring error: {e}")
                scores['perplexity'] = 50

        # Calculate consensus
        api_scores = list(scores.values())
        passing_count = sum(1 for s in api_scores if s >= self.min_score)

        approved = passing_count >= 3  # 3/4 must pass

        logger.info(
            f"Metadata approval: {approved} "
            f"({passing_count}/{len(api_scores)} ≥{self.min_score}): {scores}"
        )

        return approved, scores

    async def _score_with_claude(self, prompt: str) -> float:
        """Score with Claude"""
        response = await self.anthropic_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}]
        )

        return self._parse_score(response.content[0].text)

    async def _score_with_gpt4o(self, prompt: str) -> float:
        """Score with GPT-4o"""
        response = await self.openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )

        return self._parse_score(response.choices[0].message.content)

    async def _score_with_grok(self, prompt: str) -> float:
        """Score with Grok"""
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
        return self._parse_score(data['choices'][0]['message']['content'])

    async def _score_with_perplexity(self, prompt: str) -> float:
        """Score with Perplexity"""
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
        return self._parse_score(data['choices'][0]['message']['content'])

    def _parse_score(self, content: str) -> float:
        """Parse overall score from JSON response"""
        import json
        import re

        json_match = re.search(r'\{.*\}', content, re.DOTALL)

        if json_match:
            try:
                result = json.loads(json_match.group(0))
                return float(result.get('overall_score', 0))
            except:
                pass

        return 50.0  # Neutral score on parse error


logger.info("LLM approver module loaded")
