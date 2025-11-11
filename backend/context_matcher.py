"""
Context Matcher Module
Uses Claude AI to match news articles with streaming content context
"""
import logging
import json
from typing import List, Dict

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)


class ContextMatcher:
    """Matches news articles with content context using Claude AI"""

    def __init__(self, config: dict):
        self.config = config
        self.api_key = config['anthropic_api_key']
        self.model = config['matching']['model']

        # Initialize Anthropic client
        if self.api_key and self.api_key != "YOUR_ANTHROPIC_API_KEY_HERE":
            self.client = AsyncAnthropic(api_key=self.api_key)
        else:
            logger.warning("Anthropic API key not configured!")
            self.client = None

    async def match_news_with_context(
        self,
        news_articles: List[Dict],
        context: str,
        top_k: int = 4
    ) -> List[Dict]:
        """
        Match news articles with streaming content context using Claude AI

        Args:
            news_articles: List of news article dictionaries
            context: Transcribed content from the last 5 minutes
            top_k: Number of top matches to return

        Returns:
            List of top matching articles with relevance scores
        """
        if not self.client:
            logger.warning("No API key configured, returning first articles")
            return news_articles[:top_k]

        if not context:
            logger.warning("No context provided, returning first articles")
            return news_articles[:top_k]

        if not news_articles:
            logger.warning("No news articles to match")
            return []

        try:
            # Prepare articles for Claude
            articles_text = self._format_articles_for_prompt(news_articles)

            # Create the matching prompt
            prompt = self._create_matching_prompt(context, articles_text, top_k)

            logger.info(f"Sending {len(news_articles)} articles to Claude for matching...")
            logger.debug(f"Context length: {len(context)} chars")

            # Call Claude API
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                temperature=0.3,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )

            # Parse response
            result_text = response.content[0].text
            logger.debug(f"Claude response: {result_text}")

            # Extract matched articles
            matched_articles = self._parse_claude_response(result_text, news_articles)

            logger.info(f"Claude matched {len(matched_articles)} relevant articles")

            # Ensure we return exactly top_k articles
            if len(matched_articles) < top_k:
                # Add more articles if needed
                remaining = top_k - len(matched_articles)
                matched_titles = {a['title'] for a in matched_articles}
                for article in news_articles:
                    if article['title'] not in matched_titles:
                        matched_articles.append(article)
                        remaining -= 1
                        if remaining == 0:
                            break

            return matched_articles[:top_k]

        except Exception as e:
            logger.error(f"Error matching with Claude: {e}", exc_info=True)
            # Fallback to first articles
            return news_articles[:top_k]

    def _format_articles_for_prompt(self, articles: List[Dict]) -> str:
        """Format articles for the Claude prompt"""
        formatted = []

        for i, article in enumerate(articles, 1):
            formatted.append(
                f"{i}. [{article['source']}] {article['title']}\n"
                f"   Description: {article['description'][:200]}..."
            )

        return "\n\n".join(formatted)

    def _create_matching_prompt(self, context: str, articles_text: str, top_k: int) -> str:
        """Create the prompt for Claude to match articles with context"""
        return f"""You are analyzing a live stream's content and matching it with relevant news articles.

STREAMING CONTENT CONTEXT (last 5 minutes of transcribed speech):
{context[:3000]}

NEWS ARTICLES TO CONSIDER:
{articles_text}

TASK:
Analyze the streaming content and identify the top {top_k} most relevant news articles that relate to the topics, themes, or subjects being discussed in the stream.

Consider:
- Direct topic matches (e.g., if stream discusses AI, find AI-related news)
- Thematic relevance (e.g., if stream is about technology, prioritize tech news)
- Current events mentioned in the stream
- Industry/domain alignment
- Contextual connections that would interest the audience

Respond with ONLY a JSON array of the article numbers (1, 2, 3, etc.) in order of relevance, like this:
[1, 5, 12, 3]

Do not include any explanation or additional text, just the JSON array of numbers."""

    def _parse_claude_response(self, response_text: str, articles: List[Dict]) -> List[Dict]:
        """Parse Claude's response and return matched articles"""
        try:
            # Extract JSON array from response
            response_text = response_text.strip()

            # Try to find JSON array in the response
            start_idx = response_text.find('[')
            end_idx = response_text.rfind(']') + 1

            if start_idx == -1 or end_idx == 0:
                logger.warning("No JSON array found in Claude response")
                return []

            json_str = response_text[start_idx:end_idx]
            indices = json.loads(json_str)

            # Convert 1-based indices to 0-based and get articles
            matched_articles = []
            for idx in indices:
                if isinstance(idx, int) and 1 <= idx <= len(articles):
                    matched_articles.append(articles[idx - 1])

            return matched_articles

        except json.JSONDecodeError as e:
            logger.error(f"Error parsing Claude response as JSON: {e}")
            logger.error(f"Response was: {response_text}")
            return []
        except Exception as e:
            logger.error(f"Error parsing Claude response: {e}")
            return []
