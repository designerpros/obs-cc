"""
B-roll inserter - inserts AI-generated B-roll panels into video
"""
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List
import json
import re
import cv2

from loguru import logger

from ..common.config import config
from .comfyui_client import ComfyUIClient


class BRollInserter:
    """
    Insert B-roll panels into video

    Phase 1: Placeholder (no B-roll)
    Phase 3: ComfyUI/SDXL integration for generation
    Phase 4: Library reuse with pgvector search
    """

    def __init__(self):
        self.enabled = config.get('broll.enabled', False)
        self.trigger_duration = config.get('broll.insertion.trigger_duration_seconds', 7)
        self.comfyui_client = None
        self.generation_enabled = config.get('broll.generation.enabled', True)
        self.layout_mode = config.get('broll.insertion.layout', 'pip')  # 'pip' or 'replace'

    async def initialize(self):
        """Initialize B-roll inserter"""
        if self.enabled:
            if self.generation_enabled:
                self.comfyui_client = ComfyUIClient()
                await self.comfyui_client.initialize()
                logger.info(f"B-roll inserter initialized with ComfyUI generation (layout: {self.layout_mode})")
            else:
                logger.info("B-roll inserter initialized (generation disabled)")
        else:
            logger.info("B-roll inserter disabled")

    async def cleanup(self):
        """Cleanup resources"""
        if self.comfyui_client:
            await self.comfyui_client.cleanup()
        logger.info("B-roll inserter cleaned up")

    async def insert_broll(
        self,
        video_path: Path,
        audio_path: Path,
        topic_data: Dict[str, Any],
        output_dir: Path,
        format: str,
    ) -> Dict[str, Any]:
        """
        Insert B-roll into video

        Args:
            video_path: Input video path
            audio_path: Processed audio path
            topic_data: Topic data with metadata
            output_dir: Output directory
            format: 'landscape' or 'portrait'

        Returns:
            Dict with video_path, style, count
        """
        if not self.enabled or not self.generation_enabled or not self.comfyui_client:
            logger.info("B-roll disabled or generation not available, skipping")
            return {
                'video_path': video_path,
                'style': None,
                'count': 0,
            }

        # Phase 3: Full B-roll generation and insertion
        logger.info("Detecting talking head segments for B-roll insertion")

        # Detect segments that need B-roll
        talking_segments = await self._detect_talking_head_segments(video_path)

        if not talking_segments:
            logger.info("No segments detected for B-roll insertion")
            return {
                'video_path': video_path,
                'style': topic_data.get('category', 'general'),
                'count': 0,
            }

        # Generate B-roll panels
        style = topic_data.get('category', 'general')
        panels = []

        for i, segment in enumerate(talking_segments):
            # Extract key phrases from topic for prompt
            prompt = self._build_prompt_from_topic(topic_data, segment)

            # Determine dimensions based on format
            if format == 'landscape':
                width, height = 1920, 1080
            else:
                width, height = 1080, 1920

            # Generate panel
            panel_path = output_dir / f"broll_panel_{i:03d}.png"
            generated = await self._generate_broll_panel(
                prompt=prompt,
                style=style,
                output_dir=output_dir,
                width=width,
                height=height,
                panel_path=panel_path,
            )

            if generated:
                panels.append({
                    'image_path': generated,
                    'start_time': segment['start'],
                    'duration': segment['end'] - segment['start'],
                    'layout': self.layout_mode,
                })

        if not panels:
            logger.warning("No B-roll panels generated")
            return {
                'video_path': video_path,
                'style': style,
                'count': 0,
            }

        # Insert panels into video
        output_path = output_dir / f"broll_final_{format}.mp4"
        await self._insert_panels(
            video_path=video_path,
            panels=panels,
            output_path=output_path,
            format=format,
        )

        logger.info(f"Inserted {len(panels)} B-roll panels into video")

        return {
            'video_path': output_path,
            'style': style,
            'count': len(panels),
        }

    def _build_prompt_from_topic(
        self,
        topic_data: Dict[str, Any],
        segment: Dict[str, float],
    ) -> str:
        """Build generation prompt from topic data"""
        # Extract topic summary
        summary = topic_data.get('summary', 'abstract concept')

        # Clean and simplify
        prompt = re.sub(r'[^\w\s,.-]', '', summary[:200])

        # Add visual context
        category = topic_data.get('category', 'general')
        if category == 'crypto':
            prompt = f"cryptocurrency blockchain technology, {prompt}"
        elif category == 'finance':
            prompt = f"financial charts markets trading, {prompt}"
        elif category == 'politics':
            prompt = f"political discussion debate, {prompt}"
        elif category == 'technology':
            prompt = f"technology innovation digital, {prompt}"

        return prompt

    async def _detect_talking_head_segments(
        self,
        video_path: Path,
    ) -> List[Dict[str, float]]:
        """
        Detect segments where talking head exceeds trigger duration

        Returns:
            List of segments [{'start': float, 'end': float}]
        """
        # Phase 3: Scene detection with OpenCV
        segments = []

        try:
            cap = cv2.VideoCapture(str(video_path))
            if not cap.isOpened():
                logger.error(f"Failed to open video: {video_path}")
                return []

            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = frame_count / fps

            # Simple approach: Sample every second and look for low motion (static talking head)
            current_segment_start = None
            sample_interval_frames = int(fps)  # Sample every 1 second

            prev_frame = None
            for frame_idx in range(0, frame_count, sample_interval_frames):
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()

                if not ret:
                    break

                # Calculate motion score
                if prev_frame is not None:
                    motion_score = self._calculate_motion(prev_frame, frame)

                    # Low motion = talking head
                    if motion_score < 0.05:  # Threshold for static scene
                        if current_segment_start is None:
                            current_segment_start = frame_idx / fps
                    else:
                        # Motion detected, end segment if we have one
                        if current_segment_start is not None:
                            segment_end = frame_idx / fps
                            segment_duration = segment_end - current_segment_start

                            if segment_duration >= self.trigger_duration:
                                segments.append({
                                    'start': current_segment_start,
                                    'end': segment_end,
                                })

                            current_segment_start = None

                prev_frame = frame.copy()

            # Close final segment if exists
            if current_segment_start is not None:
                segment_duration = duration - current_segment_start
                if segment_duration >= self.trigger_duration:
                    segments.append({
                        'start': current_segment_start,
                        'end': duration,
                    })

            cap.release()

            logger.info(f"Detected {len(segments)} talking head segments")
            return segments

        except Exception as e:
            logger.exception(f"Error detecting segments: {e}")
            return []

    def _calculate_motion(self, prev_frame, curr_frame) -> float:
        """Calculate motion score between frames"""
        # Convert to grayscale
        prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
        curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)

        # Calculate absolute difference
        diff = cv2.absdiff(prev_gray, curr_gray)

        # Calculate mean difference
        motion_score = diff.mean() / 255.0

        return motion_score

    async def _generate_broll_panel(
        self,
        prompt: str,
        style: str,
        output_dir: Path,
        width: int,
        height: int,
        panel_path: Path,
    ) -> Optional[Path]:
        """
        Generate B-roll panel using ComfyUI/SDXL

        Args:
            prompt: Generation prompt
            style: Style (ghibli, cyberpunk, etc.)
            output_dir: Output directory
            width: Image width
            height: Image height
            panel_path: Output path for panel

        Returns:
            Path to generated image or None
        """
        if not self.comfyui_client:
            logger.warning("ComfyUI client not available")
            return None

        return await self.comfyui_client.generate_broll(
            prompt=prompt,
            style=style,
            width=width,
            height=height,
            output_path=panel_path,
        )

    async def _insert_panels(
        self,
        video_path: Path,
        panels: List[Dict[str, Any]],
        output_path: Path,
        format: str,
    ):
        """
        Insert B-roll panels into video using FFmpeg

        Args:
            video_path: Input video
            panels: List of panels with {image_path, start_time, duration, layout}
            output_path: Output video path
            format: 'landscape' or 'portrait'
        """
        # Build FFmpeg filter for overlay
        filter_parts = []

        for i, panel in enumerate(panels):
            image_path = panel['image_path']
            start_time = panel['start_time']
            duration = panel['duration']
            layout = panel.get('layout', 'pip')

            # Load image as input
            filter_parts.append(f"movie={image_path}:loop=1")

            if layout == 'pip':
                # Picture-in-picture (bottom-right corner, 30% size)
                filter_parts.append(
                    f"[{i+1}:v]scale=iw*0.3:ih*0.3,setpts=PTS-STARTPTS+{start_time}/TB"
                    f"[img{i}];"
                )
                # Overlay at bottom-right
                filter_parts.append(
                    f"[0:v][img{i}]overlay=W-w-10:H-h-10:enable='between(t,{start_time},{start_time+duration})'"
                    f"[v{i}];"
                )
            else:  # replace
                # Full screen replace
                filter_parts.append(
                    f"[{i+1}:v]scale={format=='landscape' and '1920:1080' or '1080:1920'},"
                    f"setpts=PTS-STARTPTS+{start_time}/TB[img{i}];"
                )
                # Replace video with image during time range
                filter_parts.append(
                    f"[0:v][img{i}]overlay=0:0:enable='between(t,{start_time},{start_time+duration})'"
                    f"[v{i}];"
                )

        # Build final filter
        filter_complex = "".join(filter_parts)
        final_output = f"v{len(panels)-1}" if panels else "0:v"

        # Build FFmpeg command
        cmd = ['ffmpeg', '-y']

        # Add video input
        cmd.extend(['-i', str(video_path)])

        # Add image inputs
        for panel in panels:
            cmd.extend(['-i', str(panel['image_path'])])

        # Apply filter
        if filter_complex:
            cmd.extend(['-filter_complex', filter_complex, '-map', f'[{final_output}]'])
        else:
            cmd.extend(['-map', '0:v'])

        # Copy audio
        cmd.extend(['-map', '0:a'])

        # Encoding settings
        cmd.extend([
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-c:a', 'copy',
            str(output_path),
        ])

        # Execute FFmpeg
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise Exception(f"FFmpeg B-roll insertion failed: {error_msg}")

        logger.debug(f"Inserted B-roll panels into {output_path}")


logger.info("B-roll inserter module loaded")
