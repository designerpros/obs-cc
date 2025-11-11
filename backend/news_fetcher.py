"""
News Fetcher Module
Fetches news from various free APIs and RSS feeds
"""
import asyncio
import logging
from typing import List, Dict
from datetime import datetime

import aiohttp
try:
    import feedparser
    FEEDPARSER_AVAILABLE = True
except ImportError:
    FEEDPARSER_AVAILABLE = False

logger = logging.getLogger(__name__)


class NewsFetcher:
    """Fetches news from multiple free sources"""

    def __init__(self, config: dict):
        self.config = config
        self.session: aiohttp.ClientSession = None

        # API keys (optional)
        self.newsapi_key = config['news_apis'].get('newsapi_key')
        self.gnews_key = config['news_apis'].get('gnews_key')

        # Configuration
        self.max_headlines = config['news']['max_headlines']
        self.categories = config['news']['categories']

    async def start(self):
        """Initialize the HTTP session"""
        self.session = aiohttp.ClientSession()
        logger.info("News fetcher initialized")

    async def stop(self):
        """Close the HTTP session"""
        if self.session:
            await self.session.close()
            logger.info("News fetcher stopped")

    async def fetch_all(self) -> List[Dict]:
        """Fetch news from all available sources"""
        all_articles = []

        # Fetch from each source concurrently
        tasks = [
            self._fetch_rss_feeds(),
            self._fetch_newsapi(),
            self._fetch_gnews(),
            self._fetch_hacker_news(),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Combine results
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Error fetching news: {result}")
            elif isinstance(result, list):
                all_articles.extend(result)

        # Remove duplicates (by title) and limit
        seen_titles = set()
        unique_articles = []

        for article in all_articles:
            title = article['title'].lower().strip()
            if title not in seen_titles:
                seen_titles.add(title)
                unique_articles.append(article)

        # Sort by timestamp (newest first) and limit
        unique_articles.sort(key=lambda x: x['timestamp'], reverse=True)
        limited_articles = unique_articles[:self.max_headlines]

        logger.info(f"Fetched {len(all_articles)} total, {len(unique_articles)} unique, returning {len(limited_articles)}")

        return limited_articles

    async def _fetch_rss_feeds(self) -> List[Dict]:
        """Fetch from various free RSS feeds"""
        if not FEEDPARSER_AVAILABLE:
            logger.warning("feedparser not available, skipping RSS feeds")
            return []

        articles = []

        # Free RSS feeds
        feeds = [
            "http://rss.cnn.com/rss/cnn_topstories.rss",
            "http://feeds.bbci.co.uk/news/rss.xml",
            "https://feeds.reuters.com/reuters/topNews",
            "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
            "http://feeds.arstechnica.com/arstechnica/index",
            "https://www.theguardian.com/world/rss",
            "https://techcrunch.com/feed/",
            "https://www.wired.com/feed/rss",
        ]

        for feed_url in feeds:
            try:
                # Use feedparser (it handles HTTP requests internally)
                feed = await asyncio.to_thread(feedparser.parse, feed_url)

                for entry in feed.entries[:10]:  # Limit per feed
                    articles.append({
                        'title': entry.get('title', 'No title'),
                        'description': entry.get('summary', entry.get('description', ''))[:300],
                        'source': feed.feed.get('title', 'RSS Feed'),
                        'url': entry.get('link', ''),
                        'timestamp': datetime.now().isoformat(),
                        'published': entry.get('published', ''),
                    })

            except Exception as e:
                logger.debug(f"Error fetching RSS feed {feed_url}: {e}")

        logger.info(f"Fetched {len(articles)} articles from RSS feeds")
        return articles

    async def _fetch_newsapi(self) -> List[Dict]:
        """Fetch from NewsAPI.org (free tier)"""
        if not self.newsapi_key or self.newsapi_key == "YOUR_NEWSAPI_ORG_KEY_HERE (optional)":
            logger.debug("NewsAPI key not configured, skipping")
            return []

        articles = []

        try:
            url = "https://newsapi.org/v2/top-headlines"
            params = {
                'apiKey': self.newsapi_key,
                'language': 'en',
                'pageSize': 20,
            }

            async with self.session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()

                    for item in data.get('articles', []):
                        articles.append({
                            'title': item.get('title', 'No title'),
                            'description': item.get('description', '')[:300],
                            'source': item.get('source', {}).get('name', 'NewsAPI'),
                            'url': item.get('url', ''),
                            'timestamp': item.get('publishedAt', datetime.now().isoformat()),
                            'published': item.get('publishedAt', ''),
                        })

                    logger.info(f"Fetched {len(articles)} articles from NewsAPI")
                else:
                    logger.warning(f"NewsAPI returned status {response.status}")

        except Exception as e:
            logger.error(f"Error fetching from NewsAPI: {e}")

        return articles

    async def _fetch_gnews(self) -> List[Dict]:
        """Fetch from GNews API (free tier)"""
        if not self.gnews_key or self.gnews_key == "YOUR_GNEWS_API_KEY_HERE (optional)":
            logger.debug("GNews API key not configured, skipping")
            return []

        articles = []

        try:
            url = "https://gnews.io/api/v4/top-headlines"
            params = {
                'token': self.gnews_key,
                'lang': 'en',
                'max': 20,
            }

            async with self.session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()

                    for item in data.get('articles', []):
                        articles.append({
                            'title': item.get('title', 'No title'),
                            'description': item.get('description', '')[:300],
                            'source': item.get('source', {}).get('name', 'GNews'),
                            'url': item.get('url', ''),
                            'timestamp': item.get('publishedAt', datetime.now().isoformat()),
                            'published': item.get('publishedAt', ''),
                        })

                    logger.info(f"Fetched {len(articles)} articles from GNews")
                else:
                    logger.warning(f"GNews returned status {response.status}")

        except Exception as e:
            logger.error(f"Error fetching from GNews: {e}")

        return articles

    async def _fetch_hacker_news(self) -> List[Dict]:
        """Fetch from Hacker News API (free, no key required)"""
        articles = []

        try:
            # Get top stories
            top_stories_url = "https://hacker-news.firebaseio.com/v0/topstories.json"

            async with self.session.get(top_stories_url, timeout=10) as response:
                if response.status == 200:
                    story_ids = await response.json()

                    # Fetch details for top 15 stories
                    for story_id in story_ids[:15]:
                        try:
                            story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
                            async with self.session.get(story_url, timeout=5) as story_response:
                                if story_response.status == 200:
                                    story = await story_response.json()

                                    if story.get('type') == 'story':
                                        articles.append({
                                            'title': story.get('title', 'No title'),
                                            'description': story.get('text', '')[:300] if story.get('text') else 'No description',
                                            'source': 'Hacker News',
                                            'url': story.get('url', f"https://news.ycombinator.com/item?id={story_id}"),
                                            'timestamp': datetime.fromtimestamp(story.get('time', 0)).isoformat(),
                                            'published': datetime.fromtimestamp(story.get('time', 0)).isoformat(),
                                        })

                        except Exception as e:
                            logger.debug(f"Error fetching HN story {story_id}: {e}")

                    logger.info(f"Fetched {len(articles)} articles from Hacker News")

        except Exception as e:
            logger.error(f"Error fetching from Hacker News: {e}")

        return articles
