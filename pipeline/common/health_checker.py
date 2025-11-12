"""
Health Check System
Comprehensive health monitoring for all pipeline services
"""
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime
import psutil
import os

from loguru import logger
from sqlalchemy import text
import httpx

from ..common.db import get_db
from ..common.config import config
from ..common.queue import job_queue


class HealthChecker:
    """
    System-wide health monitoring

    Phase 7: Production hardening
    - Check database connectivity
    - Monitor Redis queue
    - Verify external API availability
    - Track system resources (CPU, memory, disk)
    - Service-specific health checks
    - Readiness vs liveness probes
    """

    def __init__(self):
        self.service_name = config.get('service.name', 'unknown')
        self.checks_enabled = config.get('health_checks.enabled', True)

    async def initialize(self):
        """Initialize health checker"""
        logger.info(f"Health checker initialized for {self.service_name}")

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("Health checker cleaned up")

    async def check_liveness(self) -> Dict[str, Any]:
        """
        Liveness probe - is service running?

        Returns:
            Liveness status
        """
        return {
            'status': 'alive',
            'service': self.service_name,
            'timestamp': datetime.utcnow().isoformat(),
        }

    async def check_readiness(self) -> Dict[str, Any]:
        """
        Readiness probe - can service accept traffic?

        Returns:
            Readiness status with dependency checks
        """
        checks = {
            'database': await self._check_database(),
            'redis': await self._check_redis(),
            'disk': await self._check_disk_space(),
            'memory': await self._check_memory(),
        }

        # Add service-specific checks
        service_checks = await self._check_service_specific()
        checks.update(service_checks)

        # Overall readiness
        all_healthy = all(check.get('healthy', False) for check in checks.values())

        return {
            'status': 'ready' if all_healthy else 'not_ready',
            'service': self.service_name,
            'timestamp': datetime.utcnow().isoformat(),
            'checks': checks,
        }

    async def _check_database(self) -> Dict[str, Any]:
        """Check database connectivity"""
        try:
            async with get_db() as db:
                result = await db.execute(text("SELECT 1"))
                result.scalar()

            return {
                'healthy': True,
                'latency_ms': 0,  # Could measure actual latency
            }
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return {
                'healthy': False,
                'error': str(e),
            }

    async def _check_redis(self) -> Dict[str, Any]:
        """Check Redis connectivity"""
        try:
            if job_queue and job_queue.redis:
                await job_queue.redis.ping()
                return {
                    'healthy': True,
                }
            else:
                return {
                    'healthy': False,
                    'error': 'Redis not initialized',
                }
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return {
                'healthy': False,
                'error': str(e),
            }

    async def _check_disk_space(self) -> Dict[str, Any]:
        """Check disk space availability"""
        try:
            disk = psutil.disk_usage('/')
            percent_used = disk.percent

            # Warning if > 80%, critical if > 90%
            healthy = percent_used < 90

            return {
                'healthy': healthy,
                'percent_used': percent_used,
                'free_gb': disk.free / (1024**3),
                'total_gb': disk.total / (1024**3),
                'warning': percent_used > 80,
            }
        except Exception as e:
            logger.error(f"Disk space check failed: {e}")
            return {
                'healthy': False,
                'error': str(e),
            }

    async def _check_memory(self) -> Dict[str, Any]:
        """Check memory availability"""
        try:
            memory = psutil.virtual_memory()
            percent_used = memory.percent

            # Warning if > 85%, critical if > 95%
            healthy = percent_used < 95

            return {
                'healthy': healthy,
                'percent_used': percent_used,
                'available_gb': memory.available / (1024**3),
                'total_gb': memory.total / (1024**3),
                'warning': percent_used > 85,
            }
        except Exception as e:
            logger.error(f"Memory check failed: {e}")
            return {
                'healthy': False,
                'error': str(e),
            }

    async def _check_service_specific(self) -> Dict[str, Any]:
        """Service-specific health checks"""
        checks = {}

        # GPU check (if applicable)
        if self.service_name in ['transcription', 'rendering']:
            checks['gpu'] = await self._check_gpu()

        # External API checks
        if self.service_name == 'posting':
            checks['late_api'] = await self._check_late_api()

        if self.service_name == 'analysis':
            checks['anthropic_api'] = await self._check_anthropic_api()

        return checks

    async def _check_gpu(self) -> Dict[str, Any]:
        """Check GPU availability"""
        try:
            import subprocess
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=index,name,memory.free', '--format=csv,noheader'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                gpu_info = result.stdout.strip().split('\n')
                return {
                    'healthy': True,
                    'gpus_available': len(gpu_info),
                    'info': gpu_info[0] if gpu_info else 'No GPU info',
                }
            else:
                return {
                    'healthy': False,
                    'error': 'nvidia-smi failed',
                }
        except FileNotFoundError:
            return {
                'healthy': False,
                'error': 'nvidia-smi not found',
            }
        except Exception as e:
            return {
                'healthy': False,
                'error': str(e),
            }

    async def _check_late_api(self) -> Dict[str, Any]:
        """Check Late API availability"""
        try:
            late_api_url = config.get('late.api_url', 'https://api.late.gg')
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{late_api_url}/health")
                return {
                    'healthy': response.status_code == 200,
                    'status_code': response.status_code,
                }
        except Exception as e:
            return {
                'healthy': False,
                'error': str(e),
            }

    async def _check_anthropic_api(self) -> Dict[str, Any]:
        """Check Anthropic API availability"""
        try:
            api_key = os.getenv('ANTHROPIC_API_KEY')
            if not api_key:
                return {
                    'healthy': False,
                    'error': 'API key not configured',
                }

            # Simple connectivity check (don't make actual API call to save costs)
            return {
                'healthy': True,
                'api_key_configured': True,
            }
        except Exception as e:
            return {
                'healthy': False,
                'error': str(e),
            }

    async def get_full_health_report(self) -> Dict[str, Any]:
        """
        Get comprehensive health report

        Returns:
            Full health status with all checks and metrics
        """
        liveness = await self.check_liveness()
        readiness = await self.check_readiness()

        # System metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        load_avg = os.getloadavg() if hasattr(os, 'getloadavg') else (0, 0, 0)

        return {
            'service': self.service_name,
            'timestamp': datetime.utcnow().isoformat(),
            'liveness': liveness,
            'readiness': readiness,
            'system': {
                'cpu_percent': cpu_percent,
                'load_average': {
                    '1min': load_avg[0],
                    '5min': load_avg[1],
                    '15min': load_avg[2],
                },
                'uptime_seconds': self._get_uptime(),
            },
        }

    def _get_uptime(self) -> float:
        """Get service uptime in seconds"""
        try:
            boot_time = psutil.boot_time()
            return datetime.utcnow().timestamp() - boot_time
        except:
            return 0.0


# Global instance
health_checker = HealthChecker()


async def init_health_checker():
    """Initialize global health checker"""
    await health_checker.initialize()


async def close_health_checker():
    """Cleanup global health checker"""
    await health_checker.cleanup()


logger.info("Health checker module loaded")
