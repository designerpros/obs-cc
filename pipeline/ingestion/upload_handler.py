"""
Upload handler for transferring stream files to processing nodes
"""
import asyncio
import os
from pathlib import Path
from typing import Dict, Any, Optional
from uuid import UUID
import shutil
import hashlib

from loguru import logger

from ..common.config import config
from ..common.notifications import notifier


class UploadHandler:
    """Handle file uploads from local machine to Node A"""

    def __init__(self):
        self.working_dir = Path(config.get('ingestion.working_dir', '/mnt/pipeline/work'))
        self.working_dir.mkdir(parents=True, exist_ok=True)

    async def upload_stream(
        self,
        stream_id: UUID,
        files_path: Path,
        file_mapping: Dict[str, Path],
    ) -> Dict[str, Any]:
        """
        Upload stream files to working directory

        Args:
            stream_id: Stream ID
            files_path: Source folder path
            file_mapping: Validated file mapping

        Returns:
            Upload result with work directory path
        """
        try:
            # Create stream work directory
            stream_work_dir = self.working_dir / str(stream_id)
            stream_work_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"Uploading stream {stream_id} to {stream_work_dir}")

            # Calculate total size for progress tracking
            total_size = sum(
                f.stat().st_size for f in file_mapping.values()
                if f.exists() and f.is_file()
            )
            total_size_mb = total_size / (1024 * 1024)
            total_size_gb = total_size / (1024 * 1024 * 1024)

            logger.info(f"Total upload size: {total_size_gb:.2f} GB")

            # Upload each file
            uploaded_files = {}
            uploaded_size = 0

            for source_type, source_path in file_mapping.items():
                if not source_path.exists():
                    logger.warning(f"Source file not found: {source_path}")
                    continue

                # Determine destination filename
                if source_type == 'context_cache':
                    dest_filename = '.context_cache.json'
                else:
                    dest_filename = f"{source_type}.mp4"

                dest_path = stream_work_dir / dest_filename

                logger.info(f"Uploading {source_type}: {source_path.name} -> {dest_filename}")

                # Copy file (with progress)
                await self._copy_file_with_progress(
                    source_path,
                    dest_path,
                    stream_id,
                    source_type,
                    uploaded_size,
                    total_size,
                )

                uploaded_files[source_type] = dest_path
                uploaded_size += source_path.stat().st_size

                # Verify file integrity
                if not await self._verify_file_integrity(source_path, dest_path):
                    raise Exception(f"File integrity check failed for {source_type}")

            logger.info(f"Upload completed for stream {stream_id}")

            return {
                'success': True,
                'work_dir': str(stream_work_dir),
                'uploaded_files': {k: str(v) for k, v in uploaded_files.items()},
                'total_size_gb': total_size_gb,
            }

        except Exception as e:
            logger.exception(f"Upload failed for stream {stream_id}: {e}")
            return {
                'success': False,
                'error': str(e),
            }

    async def _copy_file_with_progress(
        self,
        source: Path,
        dest: Path,
        stream_id: UUID,
        source_type: str,
        uploaded_so_far: int,
        total_size: int,
    ):
        """
        Copy file with progress notifications

        Args:
            source: Source file path
            dest: Destination file path
            stream_id: Stream ID
            source_type: Source type (for logging)
            uploaded_so_far: Bytes uploaded so far
            total_size: Total bytes to upload
        """
        # Get file size
        file_size = source.stat().st_size
        file_size_mb = file_size / (1024 * 1024)

        # Copy file in chunks
        chunk_size = 1024 * 1024 * 10  # 10 MB chunks
        copied = 0

        with open(source, 'rb') as src_f:
            with open(dest, 'wb') as dest_f:
                while True:
                    chunk = src_f.read(chunk_size)
                    if not chunk:
                        break

                    dest_f.write(chunk)
                    copied += len(chunk)

                    # Calculate progress
                    file_progress = (copied / file_size) * 100
                    overall_progress = ((uploaded_so_far + copied) / total_size) * 100

                    # Estimate time remaining
                    # TODO: Track upload speed and estimate ETA

                    # Log progress every 100MB
                    if copied % (100 * 1024 * 1024) < chunk_size:
                        logger.info(
                            f"Upload progress - {source_type}: {file_progress:.1f}% "
                            f"({copied / (1024*1024):.1f}/{file_size_mb:.1f} MB)"
                        )

        logger.info(f"Copied {source_type}: {file_size_mb:.1f} MB")

    async def _verify_file_integrity(self, source: Path, dest: Path) -> bool:
        """
        Verify file was copied correctly using checksum

        Args:
            source: Original file
            dest: Copied file

        Returns:
            True if files match
        """
        try:
            # Quick check: file size
            if source.stat().st_size != dest.stat().st_size:
                logger.error(f"File size mismatch: {source} vs {dest}")
                return False

            # For small files, do full checksum
            if source.stat().st_size < 100 * 1024 * 1024:  # 100 MB
                source_hash = await self._calculate_md5(source)
                dest_hash = await self._calculate_md5(dest)

                if source_hash != dest_hash:
                    logger.error(f"Checksum mismatch: {source} vs {dest}")
                    return False

            return True

        except Exception as e:
            logger.exception(f"Error verifying file integrity: {e}")
            return False

    async def _calculate_md5(self, file_path: Path) -> str:
        """Calculate MD5 checksum of file"""
        md5 = hashlib.md5()

        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                md5.update(chunk)

        return md5.hexdigest()


logger.info("Upload handler module loaded")
