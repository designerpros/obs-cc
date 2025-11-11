"""
File validator with smart source detection
"""
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any
import json
import subprocess

from loguru import logger


class FileValidator:
    """Validate and detect stream source files"""

    # Expected file names (exact matches preferred)
    EXACT_NAMES = {
        'cam_main_me': 'cam_main_me.mp4',
        'cam_guest': 'cam_guest.mp4',
        'cam_overhead': 'cam_overhead.mp4',
        'cam_screen': 'cam_screen.mp4',
        'cam_online_caller': 'cam_online_caller.mp4',
        'live_mix': 'live_mix.mp4',
        'context_cache': '.context_cache.json',
    }

    # Required files (minimum)
    REQUIRED_SOURCES = ['cam_main_me', 'cam_overhead', 'cam_screen', 'live_mix', 'context_cache']

    def __init__(self):
        pass

    async def validate_folder(self, folder_path: Path) -> Dict[str, Any]:
        """
        Validate stream folder and detect source files

        Args:
            folder_path: Path to folder containing stream files

        Returns:
            Validation result with file mapping
        """
        if not folder_path.exists() or not folder_path.is_dir():
            return {
                'valid': False,
                'errors': [f"Folder does not exist: {folder_path}"],
                'file_mapping': {},
            }

        # Get all video files in folder
        video_files = list(folder_path.glob('*.mp4')) + list(folder_path.glob('*.mkv'))
        json_files = list(folder_path.glob('.context_cache.json'))

        if not video_files:
            return {
                'valid': False,
                'errors': ["No video files found in folder"],
                'file_mapping': {},
            }

        # Try exact name matching first
        file_mapping = await self._exact_match(folder_path)

        # If missing required files, try smart detection
        missing_required = [
            src for src in self.REQUIRED_SOURCES
            if src not in file_mapping
        ]

        if missing_required:
            logger.info(f"Exact match incomplete, using smart detection for: {missing_required}")
            detected_files = await self._smart_detect(folder_path, video_files, missing_required)
            file_mapping.update(detected_files)

        # Check if all required files are present
        still_missing = [
            src for src in self.REQUIRED_SOURCES
            if src not in file_mapping
        ]

        if still_missing:
            return {
                'valid': False,
                'errors': [f"Missing required files: {', '.join(still_missing)}"],
                'file_mapping': file_mapping,
                'requires_confirmation': False,
            }

        # Validate file sizes and formats
        validation_errors = []
        for source_type, file_path in file_mapping.items():
            if source_type == 'context_cache':
                # Validate JSON
                if not file_path.exists():
                    validation_errors.append(f"Context cache not found: {file_path}")
                continue

            # Check file exists and has content
            if not file_path.exists():
                validation_errors.append(f"{source_type} not found: {file_path}")
                continue

            file_size = file_path.stat().st_size
            if file_size < 1024 * 1024:  # Less than 1MB
                validation_errors.append(f"{source_type} is too small: {file_size} bytes")
                continue

            # Validate it's a valid video file (check with ffprobe)
            is_valid_video = await self._validate_video_file(file_path)
            if not is_valid_video:
                validation_errors.append(f"{source_type} is not a valid video file: {file_path}")

        if validation_errors:
            return {
                'valid': False,
                'errors': validation_errors,
                'file_mapping': file_mapping,
            }

        # All validations passed
        return {
            'valid': True,
            'errors': [],
            'file_mapping': file_mapping,
            'requires_confirmation': len(detected_files) > 0 if 'detected_files' in locals() else False,
        }

    async def _exact_match(self, folder_path: Path) -> Dict[str, Path]:
        """Try to match files by exact expected names"""
        file_mapping = {}

        for source_type, exact_name in self.EXACT_NAMES.items():
            file_path = folder_path / exact_name
            if file_path.exists():
                file_mapping[source_type] = file_path
                logger.debug(f"Exact match: {source_type} -> {exact_name}")

        return file_mapping

    async def _smart_detect(
        self,
        folder_path: Path,
        video_files: List[Path],
        missing_sources: List[str],
    ) -> Dict[str, Path]:
        """
        Smart detection using video analysis

        Analyzes resolution, audio channels, content to guess file types
        """
        detected = {}

        # Analyze each video file
        file_metadata = {}
        for video_file in video_files:
            # Skip already matched files
            if any(str(video_file) == str(f) for f in detected.values()):
                continue

            metadata = await self._get_video_metadata(video_file)
            if metadata:
                file_metadata[video_file] = metadata

        # Detection rules
        for source_type in missing_sources:
            if source_type == 'context_cache':
                # Look for any .json file
                json_files = list(folder_path.glob('*.json'))
                if json_files:
                    detected['context_cache'] = json_files[0]
                continue

            best_match = None
            best_score = 0

            for video_file, metadata in file_metadata.items():
                score = self._score_match(source_type, metadata, video_file.name)

                if score > best_score:
                    best_score = score
                    best_match = video_file

            if best_match and best_score > 0.5:  # Threshold for confidence
                detected[source_type] = best_match
                logger.info(f"Smart detected: {source_type} -> {best_match.name} (score: {best_score:.2f})")

                # Remove from pool
                del file_metadata[best_match]

        return detected

    async def _get_video_metadata(self, video_file: Path) -> Optional[Dict[str, Any]]:
        """Get video metadata using ffprobe"""
        try:
            result = await asyncio.create_subprocess_exec(
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                '-show_format',
                str(video_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await result.communicate()

            if result.returncode != 0:
                logger.warning(f"ffprobe failed for {video_file}: {stderr.decode()}")
                return None

            data = json.loads(stdout.decode())

            # Extract relevant info
            video_stream = next((s for s in data.get('streams', []) if s['codec_type'] == 'video'), None)
            audio_streams = [s for s in data.get('streams', []) if s['codec_type'] == 'audio']

            if not video_stream:
                return None

            return {
                'width': int(video_stream.get('width', 0)),
                'height': int(video_stream.get('height', 0)),
                'duration': float(data.get('format', {}).get('duration', 0)),
                'audio_channels': sum(int(s.get('channels', 0)) for s in audio_streams),
                'audio_stream_count': len(audio_streams),
                'codec': video_stream.get('codec_name', ''),
                'bitrate': int(data.get('format', {}).get('bit_rate', 0)),
            }

        except Exception as e:
            logger.exception(f"Error getting metadata for {video_file}: {e}")
            return None

    def _score_match(self, source_type: str, metadata: Dict[str, Any], filename: str) -> float:
        """
        Score how well a file matches a source type

        Returns:
            Score between 0 and 1
        """
        score = 0.0

        # Filename heuristics
        filename_lower = filename.lower()

        if source_type == 'cam_main_me':
            if any(word in filename_lower for word in ['main', 'me', 'host', 'primary']):
                score += 0.4
            # Usually 1080p or higher
            if metadata['width'] >= 1920 and metadata['height'] >= 1080:
                score += 0.3
            # Has audio
            if metadata['audio_stream_count'] > 0:
                score += 0.3

        elif source_type == 'cam_guest':
            if any(word in filename_lower for word in ['guest', 'visitor', 'caller']):
                score += 0.5
            # Usually 1080p
            if metadata['width'] >= 1920 and metadata['height'] >= 1080:
                score += 0.3
            # Has audio
            if metadata['audio_stream_count'] > 0:
                score += 0.2

        elif source_type == 'cam_overhead':
            if any(word in filename_lower for word in ['overhead', 'desk', 'top', 'writing']):
                score += 0.5
            # Usually 1080p
            if metadata['width'] >= 1920 and metadata['height'] >= 1080:
                score += 0.3
            # May or may not have audio
            score += 0.2

        elif source_type == 'cam_screen':
            if any(word in filename_lower for word in ['screen', 'display', 'desktop', 'monitor']):
                score += 0.5
            # Usually 1080p or higher (screen resolution)
            if metadata['width'] >= 1920:
                score += 0.3
            # Usually no audio or system audio
            if metadata['audio_stream_count'] == 0:
                score += 0.2
            else:
                score += 0.1

        elif source_type == 'cam_online_caller':
            if any(word in filename_lower for word in ['online', 'remote', 'zoom', 'meet']):
                score += 0.6
            # Usually 720p or 1080p
            if 1280 <= metadata['width'] <= 1920:
                score += 0.2
            # Has audio
            if metadata['audio_stream_count'] > 0:
                score += 0.2

        elif source_type == 'live_mix':
            if any(word in filename_lower for word in ['mix', 'output', 'final', 'stream', 'live']):
                score += 0.5
            # Usually 1080p final output
            if metadata['width'] == 1920 and metadata['height'] == 1080:
                score += 0.3
            # Has mixed audio (multiple channels)
            if metadata['audio_channels'] >= 2:
                score += 0.2

        return min(score, 1.0)

    async def _validate_video_file(self, file_path: Path) -> bool:
        """Validate that file is a readable video"""
        try:
            result = await asyncio.create_subprocess_exec(
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=codec_type',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                str(file_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, _ = await result.communicate()

            return stdout.decode().strip() == 'video'

        except Exception as e:
            logger.exception(f"Error validating video file {file_path}: {e}")
            return False


logger.info("File validator module loaded")
