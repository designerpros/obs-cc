"""
LLM Response Cache
Redis-backed caching for LLM responses to reduce API calls and costs
"""
import asyncio
import hashlib
import json
from typing import Optional, Dict, Any, Callable
from datetime import timedelta
import functools

from loguru import logger
import redis.asyncio as redis

from .config import config


class LLMCache:
    """
    LLM response caching with Redis

    Phase 4: Cost optimization through caching
    - Cache LLM responses by prompt hash
    - Configurable TTL per operation type
    - Hit/miss rate tracking
    - Automatic invalidation
    """

    def __init__(self):
        self.enabled = config.get('llm_cache.enabled', True)
        self.redis_url = config.get('redis.url', 'redis://localhost:6379')
        self.redis = None

        # TTL configuration (in seconds)
        self.ttl_by_purpose = {
            'topic_segmentation': 86400 * 30,  # 30 days (stable)
            'llm_scoring': 86400 * 30,         # 30 days (stable)
            'title_generation': 86400 * 7,     # 7 days (can change with trends)
            'description_generation': 86400 * 7,
            'metadata_approval': 86400 * 30,   # 30 days (consistent criteria)
            'virality_analysis': 86400 * 14,   # 14 days (evolving trends)
        }

        self.default_ttl = 86400 * 7  # 7 days default

        # Stats
        self.hits = 0
        self.misses = 0

    async def initialize(self):
        """Initialize Redis connection"""
        if not self.enabled:
            logger.info("LLM cache disabled")
            return

        try:
            self.redis = await redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )

            # Test connection
            await self.redis.ping()

            logger.info(f"LLM cache initialized (Redis: {self.redis_url})")

        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self.enabled = False

    async def cleanup(self):
        """Cleanup Redis connection"""
        if self.redis:
            await self.redis.close()
        logger.info("LLM cache cleaned up")

    def _generate_cache_key(
        self,
        model: str,
        prompt: str,
        purpose: str,
        **kwargs
    ) -> str:
        """
        Generate cache key from prompt and parameters

        Args:
            model: Model name
            prompt: Prompt text
            purpose: Purpose/operation type
            **kwargs: Additional parameters to include in hash

        Returns:
            Cache key string
        """
        # Create deterministic hash of inputs
        cache_data = {
            'model': model,
            'prompt': prompt,
            'purpose': purpose,
            **kwargs
        }

        # Sort keys for consistency
        cache_str = json.dumps(cache_data, sort_keys=True)
        cache_hash = hashlib.sha256(cache_str.encode()).hexdigest()

        return f"llm_cache:{purpose}:{model}:{cache_hash}"

    async def get(
        self,
        model: str,
        prompt: str,
        purpose: str,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached LLM response

        Args:
            model: Model name
            prompt: Prompt text
            purpose: Purpose/operation type
            **kwargs: Additional parameters

        Returns:
            Cached response dict or None if not found
        """
        if not self.enabled or not self.redis:
            return None

        cache_key = self._generate_cache_key(model, prompt, purpose, **kwargs)

        try:
            cached_data = await self.redis.get(cache_key)

            if cached_data:
                self.hits += 1
                logger.debug(f"LLM cache HIT: {purpose} ({model})")
                return json.loads(cached_data)
            else:
                self.misses += 1
                logger.debug(f"LLM cache MISS: {purpose} ({model})")
                return None

        except Exception as e:
            logger.warning(f"Error reading from cache: {e}")
            return None

    async def set(
        self,
        model: str,
        prompt: str,
        purpose: str,
        response: Dict[str, Any],
        ttl: Optional[int] = None,
        **kwargs
    ):
        """
        Store LLM response in cache

        Args:
            model: Model name
            prompt: Prompt text
            purpose: Purpose/operation type
            response: LLM response to cache
            ttl: Optional TTL override (seconds)
            **kwargs: Additional parameters
        """
        if not self.enabled or not self.redis:
            return

        cache_key = self._generate_cache_key(model, prompt, purpose, **kwargs)

        # Determine TTL
        if ttl is None:
            ttl = self.ttl_by_purpose.get(purpose, self.default_ttl)

        try:
            await self.redis.setex(
                cache_key,
                ttl,
                json.dumps(response)
            )

            logger.debug(f"LLM cache SET: {purpose} ({model}, TTL: {ttl}s)")

        except Exception as e:
            logger.warning(f"Error writing to cache: {e}")

    async def invalidate_by_purpose(self, purpose: str):
        """
        Invalidate all cache entries for a specific purpose

        Args:
            purpose: Purpose to invalidate
        """
        if not self.enabled or not self.redis:
            return

        try:
            # Scan for keys matching pattern
            pattern = f"llm_cache:{purpose}:*"
            cursor = 0
            deleted = 0

            while True:
                cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
                if keys:
                    deleted += await self.redis.delete(*keys)

                if cursor == 0:
                    break

            logger.info(f"Invalidated {deleted} cache entries for purpose: {purpose}")

        except Exception as e:
            logger.warning(f"Error invalidating cache: {e}")

    async def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0

        stats = {
            'enabled': self.enabled,
            'hits': self.hits,
            'misses': self.misses,
            'total_requests': total_requests,
            'hit_rate_percent': hit_rate,
        }

        # Get Redis info if available
        if self.redis:
            try:
                info = await self.redis.info('memory')
                stats['redis_memory_used_mb'] = info.get('used_memory', 0) / 1024 / 1024
                stats['redis_keys'] = await self.redis.dbsize()
            except Exception as e:
                logger.warning(f"Error getting Redis stats: {e}")

        return stats

    def cache_decorator(
        self,
        model: str,
        purpose: str,
        ttl: Optional[int] = None,
    ):
        """
        Decorator for caching LLM function calls

        Usage:
            @llm_cache.cache_decorator(model='claude-3-5-sonnet', purpose='scoring')
            async def score_topic(prompt: str) -> dict:
                ...

        Args:
            model: Model name
            purpose: Purpose/operation type
            ttl: Optional TTL override
        """
        def decorator(func: Callable):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                # Extract prompt from args/kwargs
                # Assume first arg or 'prompt' kwarg contains the prompt
                prompt = args[0] if args else kwargs.get('prompt', '')

                # Try to get from cache
                cached = await self.get(
                    model=model,
                    prompt=str(prompt),
                    purpose=purpose,
                    **kwargs
                )

                if cached is not None:
                    return cached

                # Cache miss, call function
                result = await func(*args, **kwargs)

                # Store in cache
                await self.set(
                    model=model,
                    prompt=str(prompt),
                    purpose=purpose,
                    response=result,
                    ttl=ttl,
                    **kwargs
                )

                return result

            return wrapper
        return decorator


# Global instance
llm_cache = LLMCache()


async def init_llm_cache():
    """Initialize global LLM cache"""
    await llm_cache.initialize()


async def close_llm_cache():
    """Cleanup global LLM cache"""
    await llm_cache.cleanup()


logger.info("LLM cache module loaded")
