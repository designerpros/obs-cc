"""
S3 uploader for Deep Glacier archival
"""
import asyncio
import os
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from uuid import UUID

import boto3
from botocore.config import Config
from loguru import logger

from ..common.config import config


class S3Uploader:
    """
    S3 uploader with lifecycle management

    - Sources → Deep Glacier (perpetual)
    - Extractions → Glacier (5 year auto-delete)
    """

    def __init__(self):
        self.s3_client = None
        self.bucket_sources = config.get('infrastructure.s3.bucket_sources')
        self.bucket_extractions = config.get('infrastructure.s3.bucket_extractions')
        self.region = config.get('infrastructure.s3.region', 'us-east-1')

    async def initialize(self):
        """Initialize S3 client"""
        endpoint = config.get('infrastructure.s3.endpoint')
        access_key = config.get('infrastructure.s3.access_key')
        secret_key = config.get('infrastructure.s3.secret_key')

        if not all([access_key, secret_key]):
            raise Exception("S3 credentials not configured")

        # Create S3 client
        self.s3_client = boto3.client(
            's3',
            endpoint_url=f"https://{endpoint}" if endpoint else None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=self.region,
            config=Config(
                retries={'max_attempts': 5, 'mode': 'adaptive'},
                max_pool_connections=50,
            ),
        )

        logger.info(f"S3 client initialized (region: {self.region})")

        # Ensure buckets exist and have lifecycle policies
        await self._ensure_buckets()

    async def cleanup(self):
        """Cleanup resources"""
        if self.s3_client:
            self.s3_client = None
        logger.info("S3 uploader cleaned up")

    async def _ensure_buckets(self):
        """Ensure buckets exist with correct lifecycle policies"""
        from botocore.exceptions import ClientError
        loop = asyncio.get_event_loop()

        # Check/create sources bucket
        try:
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.head_bucket(Bucket=self.bucket_sources)
            )
            logger.info(f"Sources bucket exists: {self.bucket_sources}")
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                logger.info(f"Creating sources bucket: {self.bucket_sources}")

                # Prepare bucket creation arguments
                create_bucket_args = {'Bucket': self.bucket_sources}
                # AWS quirk: us-east-1 doesn't accept LocationConstraint
                if self.region != 'us-east-1':
                    create_bucket_args['CreateBucketConfiguration'] = {
                        'LocationConstraint': self.region
                    }

                await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.create_bucket(**create_bucket_args)
                )

                # Set lifecycle policy for Deep Glacier
                await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.put_bucket_lifecycle_configuration(
                        Bucket=self.bucket_sources,
                        LifecycleConfiguration={
                            'Rules': [{
                                'Id': 'deep-glacier-transition',
                                'Status': 'Enabled',
                                'Prefix': '',
                                'Transitions': [{
                                    'Days': 0,
                                    'StorageClass': 'DEEP_ARCHIVE',
                                }],
                            }]
                        }
                    )
                )
            else:
                logger.error(f"Error checking sources bucket: {e}")
                raise
        except Exception as e:
            logger.error(f"Unexpected error with sources bucket: {e}")
            raise

        # Check/create extractions bucket
        try:
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.head_bucket(Bucket=self.bucket_extractions)
            )
            logger.info(f"Extractions bucket exists: {self.bucket_extractions}")
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                logger.info(f"Creating extractions bucket: {self.bucket_extractions}")

                # Prepare bucket creation arguments
                create_bucket_args = {'Bucket': self.bucket_extractions}
                # AWS quirk: us-east-1 doesn't accept LocationConstraint
                if self.region != 'us-east-1':
                    create_bucket_args['CreateBucketConfiguration'] = {
                        'LocationConstraint': self.region
                    }

                await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.create_bucket(**create_bucket_args)
                )

                # Set lifecycle policy for Glacier + 5 year deletion
                await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.put_bucket_lifecycle_configuration(
                        Bucket=self.bucket_extractions,
                        LifecycleConfiguration={
                            'Rules': [{
                                'Id': 'glacier-5year-delete',
                                'Status': 'Enabled',
                                'Prefix': '',
                                'Transitions': [{
                                    'Days': 0,
                                    'StorageClass': 'GLACIER',
                                }],
                                'Expiration': {
                                    'Days': 1825,  # 5 years
                                }
                            }]
                        }
                    )
                )
            else:
                logger.error(f"Error checking extractions bucket: {e}")
                raise
        except Exception as e:
            logger.error(f"Unexpected error with extractions bucket: {e}")
            raise

    async def upload_sources(
        self,
        stream_id: UUID,
        stream_date: datetime,
        file_mapping: Dict[str, Path],
    ) -> Dict[str, str]:
        """
        Upload source files to Deep Glacier

        Args:
            stream_id: Stream ID
            stream_date: Stream date
            file_mapping: Dict of source_type -> file_path

        Returns:
            Dict of source_type -> S3 key
        """
        # Build S3 prefix: sources/YYYY/MM/YYYY-MM-DD_stream-id/
        year = stream_date.strftime('%Y')
        month = stream_date.strftime('%m')
        date_str = stream_date.strftime('%Y-%m-%d')
        prefix = f"sources/{year}/{month}/{date_str}_{stream_id}/"

        logger.info(f"Uploading {len(file_mapping)} source files to {self.bucket_sources}/{prefix}")

        uploaded_keys = {}

        for source_type, file_path in file_mapping.items():
            if not file_path.exists():
                logger.warning(f"Source file not found: {file_path}")
                continue

            # Determine S3 key
            filename = file_path.name
            s3_key = f"{prefix}{filename}"

            # Upload file
            logger.info(f"Uploading {source_type}: {filename} ({file_path.stat().st_size / (1024**3):.2f} GB)")

            await self._upload_file(
                file_path=file_path,
                bucket=self.bucket_sources,
                key=s3_key,
                storage_class='DEEP_ARCHIVE',
            )

            uploaded_keys[source_type] = s3_key

        logger.info(f"Uploaded {len(uploaded_keys)} source files")
        return uploaded_keys

    async def upload_extraction(
        self,
        stream_id: UUID,
        stream_date: datetime,
        extraction_id: UUID,
        video_path: Path,
        thumbnail_path: Optional[Path] = None,
    ) -> Dict[str, str]:
        """
        Upload extraction files to Glacier

        Args:
            stream_id: Stream ID
            stream_date: Stream date
            extraction_id: Extraction ID
            video_path: Path to video file
            thumbnail_path: Optional path to thumbnail

        Returns:
            Dict with 'video' and optional 'thumbnail' S3 keys
        """
        # Build S3 prefix: extractions/YYYY/MM/YYYY-MM-DD_stream-id/extraction-id/
        year = stream_date.strftime('%Y')
        month = stream_date.strftime('%m')
        date_str = stream_date.strftime('%Y-%m-%d')
        prefix = f"extractions/{year}/{month}/{date_str}_{stream_id}/{extraction_id}/"

        logger.info(f"Uploading extraction to {self.bucket_extractions}/{prefix}")

        uploaded_keys = {}

        # Upload video
        video_filename = video_path.name
        video_key = f"{prefix}{video_filename}"

        await self._upload_file(
            file_path=video_path,
            bucket=self.bucket_extractions,
            key=video_key,
            storage_class='GLACIER',
        )

        uploaded_keys['video'] = video_key

        # Upload thumbnail if provided
        if thumbnail_path and thumbnail_path.exists():
            thumbnail_filename = thumbnail_path.name
            thumbnail_key = f"{prefix}{thumbnail_filename}"

            await self._upload_file(
                file_path=thumbnail_path,
                bucket=self.bucket_extractions,
                key=thumbnail_key,
                storage_class='GLACIER',
            )

            uploaded_keys['thumbnail'] = thumbnail_key

        logger.info(f"Uploaded extraction files")
        return uploaded_keys

    async def _upload_file(
        self,
        file_path: Path,
        bucket: str,
        key: str,
        storage_class: str = 'STANDARD',
    ):
        """
        Upload file to S3

        Args:
            file_path: Local file path
            bucket: S3 bucket name
            key: S3 object key
            storage_class: Storage class (STANDARD, GLACIER, DEEP_ARCHIVE)
        """
        loop = asyncio.get_event_loop()

        # Upload in thread pool (blocking I/O)
        await loop.run_in_executor(
            None,
            lambda: self.s3_client.upload_file(
                str(file_path),
                bucket,
                key,
                ExtraArgs={
                    'StorageClass': storage_class,
                    'ServerSideEncryption': 'AES256',
                }
            )
        )

        logger.info(f"Uploaded to s3://{bucket}/{key} ({storage_class})")

    async def verify_upload(
        self,
        bucket: str,
        key: str,
    ) -> bool:
        """
        Verify file was uploaded successfully

        Args:
            bucket: S3 bucket name
            key: S3 object key

        Returns:
            True if file exists
        """
        from botocore.exceptions import ClientError
        loop = asyncio.get_event_loop()

        try:
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.head_object(Bucket=bucket, Key=key)
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                logger.debug(f"Object not found: s3://{bucket}/{key}")
                return False
            else:
                logger.error(f"Error verifying upload: {e}")
                return False
        except Exception as e:
            logger.error(f"Unexpected error verifying upload: {e}")
            return False


logger.info("S3 uploader module loaded")
