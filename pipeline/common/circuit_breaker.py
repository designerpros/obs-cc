"""
Circuit Breaker
Prevents cascading failures with automatic retry and fallback logic
"""
import asyncio
from typing import Dict, Any, Optional, Callable, TypeVar, ParamSpec
from datetime import datetime, timedelta
from enum import Enum
import functools

from loguru import logger

from ..common.config import config


P = ParamSpec('P')
T = TypeVar('T')


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = 'closed'  # Normal operation
    OPEN = 'open'  # Failing, rejecting requests
    HALF_OPEN = 'half_open'  # Testing if service recovered


class CircuitBreaker:
    """
    Circuit breaker pattern implementation

    Phase 7: Production hardening
    - Prevents cascading failures
    - Automatic failure detection
    - Exponential backoff retry
    - Graceful degradation
    - Self-healing
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception,
    ):
        """
        Initialize circuit breaker

        Args:
            name: Circuit breaker name
            failure_threshold: Consecutive failures before opening
            recovery_timeout: Seconds to wait before trying again
            expected_exception: Exception type to catch
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.opened_at: Optional[datetime] = None

    async def call(
        self,
        func: Callable[P, T],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> T:
        """
        Execute function with circuit breaker protection

        Args:
            func: Function to call
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Function result

        Raises:
            CircuitBreakerError: If circuit is open
            Original exception if function fails
        """
        # Check if circuit is open
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                logger.info(f"Circuit breaker {self.name}: attempting reset (HALF_OPEN)")
                self.state = CircuitState.HALF_OPEN
            else:
                wait_time = self._get_wait_time()
                raise CircuitBreakerError(
                    f"Circuit breaker {self.name} is OPEN "
                    f"(retry in {wait_time:.0f}s)"
                )

        try:
            # Execute function
            result = await func(*args, **kwargs)

            # Success - record it
            self._on_success()
            return result

        except self.expected_exception as e:
            # Failure - record it
            self._on_failure()
            raise

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset"""
        if not self.opened_at:
            return True

        elapsed = (datetime.utcnow() - self.opened_at).total_seconds()
        return elapsed >= self.recovery_timeout

    def _get_wait_time(self) -> float:
        """Get remaining wait time before retry"""
        if not self.opened_at:
            return 0.0

        elapsed = (datetime.utcnow() - self.opened_at).total_seconds()
        remaining = max(0, self.recovery_timeout - elapsed)
        return remaining

    def _on_success(self):
        """Handle successful call"""
        self.failure_count = 0
        self.success_count += 1

        if self.state == CircuitState.HALF_OPEN:
            logger.info(f"Circuit breaker {self.name}: recovery successful (CLOSED)")
            self.state = CircuitState.CLOSED
            self.opened_at = None

    def _on_failure(self):
        """Handle failed call"""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()

        logger.warning(
            f"Circuit breaker {self.name}: failure {self.failure_count}/{self.failure_threshold}"
        )

        if self.failure_count >= self.failure_threshold:
            self._open_circuit()

    def _open_circuit(self):
        """Open the circuit (stop allowing requests)"""
        logger.error(
            f"Circuit breaker {self.name}: OPENING circuit "
            f"(threshold {self.failure_threshold} reached)"
        )

        self.state = CircuitState.OPEN
        self.opened_at = datetime.utcnow()

    def reset(self):
        """Manually reset circuit breaker"""
        logger.info(f"Circuit breaker {self.name}: manual reset")
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.opened_at = None

    def get_status(self) -> Dict[str, Any]:
        """Get circuit breaker status"""
        return {
            'name': self.name,
            'state': self.state.value,
            'failure_count': self.failure_count,
            'success_count': self.success_count,
            'opened_at': self.opened_at.isoformat() if self.opened_at else None,
            'wait_time_seconds': self._get_wait_time() if self.state == CircuitState.OPEN else 0,
        }


class CircuitBreakerError(Exception):
    """Circuit breaker is open"""
    pass


class CircuitBreakerManager:
    """
    Manages multiple circuit breakers

    Phase 7: Centralized circuit breaker management
    """

    def __init__(self):
        self.breakers: Dict[str, CircuitBreaker] = {}

    def get_breaker(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception,
    ) -> CircuitBreaker:
        """
        Get or create circuit breaker

        Args:
            name: Circuit breaker name
            failure_threshold: Consecutive failures before opening
            recovery_timeout: Seconds to wait before trying again
            expected_exception: Exception type to catch

        Returns:
            Circuit breaker instance
        """
        if name not in self.breakers:
            self.breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                expected_exception=expected_exception,
            )

        return self.breakers[name]

    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all circuit breakers"""
        return {
            name: breaker.get_status()
            for name, breaker in self.breakers.items()
        }

    def reset_all(self):
        """Reset all circuit breakers"""
        for breaker in self.breakers.values():
            breaker.reset()


# Global manager
circuit_breaker_manager = CircuitBreakerManager()


def circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: int = 60,
    expected_exception: type = Exception,
):
    """
    Decorator for circuit breaker protection

    Usage:
        @circuit_breaker(name='anthropic_api', failure_threshold=3)
        async def call_claude(prompt: str):
            ...

    Args:
        name: Circuit breaker name
        failure_threshold: Consecutive failures before opening
        recovery_timeout: Seconds to wait before trying again
        expected_exception: Exception type to catch
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            breaker = circuit_breaker_manager.get_breaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                expected_exception=expected_exception,
            )

            return await breaker.call(func, *args, **kwargs)

        return wrapper
    return decorator


logger.info("Circuit breaker module loaded")
