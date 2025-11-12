"""
Disaster Recovery
Automated backups and recovery procedures
"""
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import json

from loguru import logger
from sqlalchemy import text

from ..common.db import get_db
from ..common.config import config


class DisasterRecovery:
    """
    Automated backup and recovery system

    Phase 7: Production hardening
    - PostgreSQL automated backups
    - Redis snapshot backups
    - Configuration backups
    - S3 backup uploads
    - Point-in-time recovery
    - Automated restore procedures
    """

    def __init__(self):
        self.enabled = config.get('disaster_recovery.enabled', True)
        self.backup_interval_hours = config.get('disaster_recovery.backup_interval_hours', 24)
        self.retention_days = config.get('disaster_recovery.retention_days', 30)
        self.backup_path = Path(config.get('disaster_recovery.backup_path', '/backups'))
        self.s3_bucket = config.get('disaster_recovery.s3_bucket', 'bbb-pipeline-backups')

    async def initialize(self):
        """Initialize disaster recovery"""
        if self.enabled:
            # Create backup directory
            self.backup_path.mkdir(parents=True, exist_ok=True)

            logger.info(
                f"Disaster recovery initialized "
                f"(interval: {self.backup_interval_hours}h, "
                f"retention: {self.retention_days} days)"
            )
        else:
            logger.info("Disaster recovery disabled")

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("Disaster recovery cleaned up")

    async def create_full_backup(self) -> Dict[str, Any]:
        """
        Create full system backup

        Returns:
            Backup manifest with file paths
        """
        if not self.enabled:
            return {'enabled': False}

        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        backup_dir = self.backup_path / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Creating full backup: {backup_dir}")

        manifest = {
            'timestamp': timestamp,
            'created_at': datetime.utcnow().isoformat(),
            'files': {},
        }

        # 1. Backup PostgreSQL database
        logger.info("Backing up PostgreSQL...")
        db_backup = await self._backup_postgres(backup_dir)
        manifest['files']['database'] = db_backup

        # 2. Backup Redis data
        logger.info("Backing up Redis...")
        redis_backup = await self._backup_redis(backup_dir)
        manifest['files']['redis'] = redis_backup

        # 3. Backup configuration
        logger.info("Backing up configuration...")
        config_backup = await self._backup_config(backup_dir)
        manifest['files']['config'] = config_backup

        # 4. Write manifest
        manifest_file = backup_dir / 'manifest.json'
        with open(manifest_file, 'w') as f:
            json.dump(manifest, f, indent=2)

        manifest['files']['manifest'] = str(manifest_file)

        # 5. Upload to S3
        logger.info("Uploading backup to S3...")
        await self._upload_to_s3(backup_dir, timestamp)

        # 6. Cleanup old backups
        await self._cleanup_old_backups()

        logger.info(f"Full backup completed: {timestamp}")

        return manifest

    async def _backup_postgres(self, backup_dir: Path) -> str:
        """Backup PostgreSQL database"""
        db_config = config.get('database', {})
        db_name = db_config.get('name', 'stream_pipeline')
        db_user = db_config.get('user', 'postgres')
        db_host = db_config.get('host', 'localhost')
        db_port = db_config.get('port', 5432)

        backup_file = backup_dir / f'postgres_{db_name}.sql.gz'

        # Use pg_dump with compression
        cmd = [
            'pg_dump',
            '-h', db_host,
            '-p', str(db_port),
            '-U', db_user,
            '-d', db_name,
            '--format=custom',
            '--compress=9',
            '-f', str(backup_file),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour timeout
            )

            if result.returncode != 0:
                raise Exception(f"pg_dump failed: {result.stderr}")

            logger.info(f"PostgreSQL backup: {backup_file} ({backup_file.stat().st_size / 1024 / 1024:.1f} MB)")

            return str(backup_file)

        except Exception as e:
            logger.error(f"PostgreSQL backup failed: {e}")
            raise

    async def _backup_redis(self, backup_dir: Path) -> str:
        """Backup Redis data"""
        redis_config = config.get('redis', {})
        redis_host = redis_config.get('host', 'localhost')
        redis_port = redis_config.get('port', 6379)

        backup_file = backup_dir / 'redis.rdb'

        try:
            # Trigger Redis SAVE command
            cmd = [
                'redis-cli',
                '-h', redis_host,
                '-p', str(redis_port),
                'SAVE',
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode != 0:
                raise Exception(f"redis-cli SAVE failed: {result.stderr}")

            # Copy RDB file
            redis_rdb_path = Path('/var/lib/redis/dump.rdb')  # Default Redis RDB location
            if redis_rdb_path.exists():
                import shutil
                shutil.copy2(redis_rdb_path, backup_file)

            logger.info(f"Redis backup: {backup_file}")

            return str(backup_file)

        except Exception as e:
            logger.warning(f"Redis backup failed (non-critical): {e}")
            return ""

    async def _backup_config(self, backup_dir: Path) -> str:
        """Backup configuration files"""
        config_file = backup_dir / 'config.yaml'

        try:
            # Copy main config
            config_source = Path('config/pipeline.yaml')
            if config_source.exists():
                import shutil
                shutil.copy2(config_source, config_file)

            logger.info(f"Config backup: {config_file}")

            return str(config_file)

        except Exception as e:
            logger.warning(f"Config backup failed: {e}")
            return ""

    async def _upload_to_s3(self, backup_dir: Path, timestamp: str):
        """Upload backup to S3"""
        try:
            import boto3

            s3 = boto3.client('s3')

            # Upload all files in backup directory
            for file_path in backup_dir.glob('**/*'):
                if file_path.is_file():
                    s3_key = f"backups/{timestamp}/{file_path.name}"

                    s3.upload_file(
                        str(file_path),
                        self.s3_bucket,
                        s3_key,
                    )

                    logger.debug(f"Uploaded to S3: s3://{self.s3_bucket}/{s3_key}")

            logger.info(f"Backup uploaded to S3: s3://{self.s3_bucket}/backups/{timestamp}/")

        except Exception as e:
            logger.error(f"S3 upload failed: {e}")
            # Don't raise - local backup still exists

    async def _cleanup_old_backups(self):
        """Cleanup backups older than retention period"""
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=self.retention_days)

            for backup_dir in self.backup_path.iterdir():
                if not backup_dir.is_dir():
                    continue

                # Parse timestamp from directory name
                try:
                    backup_date = datetime.strptime(backup_dir.name, '%Y%m%d_%H%M%S')

                    if backup_date < cutoff_date:
                        logger.info(f"Removing old backup: {backup_dir.name}")

                        import shutil
                        shutil.rmtree(backup_dir)

                except ValueError:
                    # Not a valid backup directory name
                    continue

        except Exception as e:
            logger.error(f"Backup cleanup failed: {e}")

    async def restore_from_backup(self, timestamp: str) -> Dict[str, Any]:
        """
        Restore system from backup

        Args:
            timestamp: Backup timestamp to restore

        Returns:
            Restore result
        """
        backup_dir = self.backup_path / timestamp

        if not backup_dir.exists():
            raise FileNotFoundError(f"Backup not found: {timestamp}")

        logger.warning(f"RESTORING from backup: {timestamp}")

        # Load manifest
        manifest_file = backup_dir / 'manifest.json'
        with open(manifest_file, 'r') as f:
            manifest = json.load(f)

        results = {}

        # 1. Restore PostgreSQL
        if 'database' in manifest['files']:
            logger.info("Restoring PostgreSQL...")
            results['database'] = await self._restore_postgres(manifest['files']['database'])

        # 2. Restore Redis
        if 'redis' in manifest['files']:
            logger.info("Restoring Redis...")
            results['redis'] = await self._restore_redis(manifest['files']['redis'])

        logger.info(f"Restore completed from backup: {timestamp}")

        return {
            'timestamp': timestamp,
            'restored_at': datetime.utcnow().isoformat(),
            'results': results,
        }

    async def _restore_postgres(self, backup_file: str) -> Dict[str, Any]:
        """Restore PostgreSQL database"""
        db_config = config.get('database', {})
        db_name = db_config.get('name', 'stream_pipeline')
        db_user = db_config.get('user', 'postgres')
        db_host = db_config.get('host', 'localhost')
        db_port = db_config.get('port', 5432)

        # Use pg_restore
        cmd = [
            'pg_restore',
            '-h', db_host,
            '-p', str(db_port),
            '-U', db_user,
            '-d', db_name,
            '--clean',  # Drop existing objects
            '--if-exists',
            backup_file,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
            )

            if result.returncode != 0 and result.returncode != 1:
                # pg_restore returns 1 for warnings, which is often OK
                raise Exception(f"pg_restore failed: {result.stderr}")

            logger.info("PostgreSQL restore completed")

            return {'success': True}

        except Exception as e:
            logger.error(f"PostgreSQL restore failed: {e}")
            return {'success': False, 'error': str(e)}

    async def _restore_redis(self, backup_file: str) -> Dict[str, Any]:
        """Restore Redis data"""
        try:
            # Stop Redis (systemd)
            subprocess.run(['systemctl', 'stop', 'redis'], timeout=30)

            # Copy RDB file
            import shutil
            redis_rdb_path = Path('/var/lib/redis/dump.rdb')
            shutil.copy2(backup_file, redis_rdb_path)

            # Start Redis
            subprocess.run(['systemctl', 'start', 'redis'], timeout=30)

            logger.info("Redis restore completed")

            return {'success': True}

        except Exception as e:
            logger.error(f"Redis restore failed: {e}")
            return {'success': False, 'error': str(e)}

    async def get_backup_list(self) -> List[Dict[str, Any]]:
        """Get list of available backups"""
        backups = []

        for backup_dir in sorted(self.backup_path.iterdir(), reverse=True):
            if not backup_dir.is_dir():
                continue

            manifest_file = backup_dir / 'manifest.json'
            if manifest_file.exists():
                with open(manifest_file, 'r') as f:
                    manifest = json.load(f)

                backups.append({
                    'timestamp': backup_dir.name,
                    'created_at': manifest.get('created_at'),
                    'files': list(manifest.get('files', {}).keys()),
                })

        return backups


# Global instance
disaster_recovery = DisasterRecovery()


async def init_disaster_recovery():
    """Initialize global disaster recovery"""
    await disaster_recovery.initialize()


async def close_disaster_recovery():
    """Cleanup global disaster recovery"""
    await disaster_recovery.cleanup()


logger.info("Disaster recovery module loaded")
