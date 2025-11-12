"""
Scene detector for intelligent multi-camera switching
Analyzes video frames to determine optimal camera angles
"""
import asyncio
import cv2
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import json

from loguru import logger

from ..common.config import config


class SceneDetector:
    """
    Intelligent scene detection for camera switching

    Phase 3: AI-powered scene analysis
    - Detects speaker activity using face detection
    - Measures visual interest (motion, composition)
    - Recommends camera switches based on content
    """

    def __init__(self):
        self.face_cascade = None
        self.min_scene_duration = config.get('rendering.camera_switching.min_scene_duration', 3.0)
        self.motion_threshold = config.get('rendering.camera_switching.motion_threshold', 0.15)
        self.face_detector_enabled = config.get('rendering.camera_switching.face_detection', True)

    async def initialize(self):
        """Initialize scene detector"""
        if self.face_detector_enabled:
            try:
                # Load OpenCV face detector
                cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
                self.face_cascade = cv2.CascadeClassifier(cascade_path)
                logger.info("Scene detector initialized with face detection")
            except Exception as e:
                logger.warning(f"Face detection unavailable: {e}")
                self.face_detector_enabled = False
                logger.info("Scene detector initialized (no face detection)")
        else:
            logger.info("Scene detector initialized (face detection disabled)")

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("Scene detector cleaned up")

    async def analyze_segment(
        self,
        video_paths: Dict[str, Path],
        start_time: float,
        end_time: float,
        fps: float = 30.0,
    ) -> List[Dict[str, Any]]:
        """
        Analyze video segment and recommend camera switches

        Args:
            video_paths: Dict of {camera_name: video_path}
            start_time: Segment start time in seconds
            end_time: Segment end time in seconds
            fps: Frames per second

        Returns:
            List of camera switch recommendations:
            [{
                'timestamp': 15.5,
                'camera': 'cam_tight',
                'reason': 'single_speaker',
                'confidence': 0.85,
            }]
        """
        logger.info(f"Analyzing segment {start_time:.1f}s - {end_time:.1f}s")

        # Sample frames at 1 FPS for analysis
        sample_interval = 1.0
        sample_times = np.arange(start_time, end_time, sample_interval)

        # Extract frames from each camera
        camera_frames = {}
        for camera_name, video_path in video_paths.items():
            if video_path and Path(video_path).exists():
                frames = await self._extract_frames(video_path, sample_times)
                camera_frames[camera_name] = frames

        if not camera_frames:
            logger.warning("No camera frames available for analysis")
            return []

        # Analyze frames and generate switch recommendations
        recommendations = []
        current_camera = 'live_mix'
        last_switch_time = start_time

        for i, timestamp in enumerate(sample_times):
            # Get frame from each camera at this timestamp
            frame_analysis = {}
            for camera_name, frames in camera_frames.items():
                if i < len(frames) and frames[i] is not None:
                    analysis = await self._analyze_frame(frames[i], camera_name)
                    frame_analysis[camera_name] = analysis

            # Determine best camera for this moment
            best_camera = self._select_best_camera(
                frame_analysis=frame_analysis,
                current_camera=current_camera,
                time_since_switch=timestamp - last_switch_time,
            )

            # Recommend switch if camera changed and minimum duration met
            if best_camera != current_camera:
                if timestamp - last_switch_time >= self.min_scene_duration:
                    recommendations.append({
                        'timestamp': timestamp,
                        'camera': best_camera,
                        'reason': frame_analysis.get(best_camera, {}).get('reason', 'unknown'),
                        'confidence': frame_analysis.get(best_camera, {}).get('confidence', 0.5),
                    })
                    current_camera = best_camera
                    last_switch_time = timestamp

        logger.info(f"Generated {len(recommendations)} camera switch recommendations")
        return recommendations

    async def _extract_frames(
        self,
        video_path: Path,
        timestamps: np.ndarray,
    ) -> List[Optional[np.ndarray]]:
        """Extract frames at specific timestamps"""
        frames = []

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            logger.error(f"Failed to open video: {video_path}")
            return [None] * len(timestamps)

        fps = cap.get(cv2.CAP_PROP_FPS)

        for timestamp in timestamps:
            frame_number = int(timestamp * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ret, frame = cap.read()

            if ret:
                frames.append(frame)
            else:
                frames.append(None)

        cap.release()
        return frames

    async def _analyze_frame(
        self,
        frame: np.ndarray,
        camera_name: str,
    ) -> Dict[str, Any]:
        """
        Analyze a single frame

        Returns:
            {
                'faces': int,
                'motion_score': float,
                'composition_score': float,
                'confidence': float,
                'reason': str,
            }
        """
        # Detect faces
        face_count = 0
        if self.face_detector_enabled and self.face_cascade is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self.face_cascade.detectMultiScale(gray, 1.1, 4)
            face_count = len(faces)

        # Calculate motion score (using edge detection as proxy)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 100, 200)
        motion_score = np.mean(edges) / 255.0

        # Calculate composition score (rule of thirds)
        composition_score = self._calculate_composition_score(frame, faces if face_count > 0 else [])

        # Determine reason and confidence
        reason, confidence = self._determine_camera_reason(
            camera_name=camera_name,
            face_count=face_count,
            motion_score=motion_score,
            composition_score=composition_score,
        )

        return {
            'faces': face_count,
            'motion_score': motion_score,
            'composition_score': composition_score,
            'confidence': confidence,
            'reason': reason,
        }

    def _calculate_composition_score(
        self,
        frame: np.ndarray,
        faces: np.ndarray,
    ) -> float:
        """Calculate composition quality (rule of thirds)"""
        h, w = frame.shape[:2]

        if len(faces) == 0:
            return 0.5  # Neutral score

        # Check if faces are near rule of thirds points
        third_x = [w // 3, 2 * w // 3]
        third_y = [h // 3, 2 * h // 3]

        scores = []
        for (x, y, w_face, h_face) in faces:
            center_x = x + w_face // 2
            center_y = y + h_face // 2

            # Distance to nearest third point
            min_dist_x = min(abs(center_x - tx) for tx in third_x)
            min_dist_y = min(abs(center_y - ty) for ty in third_y)

            # Normalize (closer = better)
            score = 1.0 - (min_dist_x + min_dist_y) / (w + h)
            scores.append(score)

        return np.mean(scores) if scores else 0.5

    def _determine_camera_reason(
        self,
        camera_name: str,
        face_count: int,
        motion_score: float,
        composition_score: float,
    ) -> Tuple[str, float]:
        """Determine why this camera is good and confidence"""

        # Camera-specific heuristics
        if camera_name == 'cam_tight' and face_count == 1:
            return 'single_speaker', 0.9
        elif camera_name == 'cam_wide' and face_count >= 2:
            return 'multiple_speakers', 0.85
        elif camera_name == 'cam_main' and face_count >= 1:
            return 'main_angle', 0.8
        elif motion_score > self.motion_threshold:
            return 'high_motion', 0.7
        elif composition_score > 0.7:
            return 'good_composition', 0.75
        else:
            return 'default', 0.5

    def _select_best_camera(
        self,
        frame_analysis: Dict[str, Dict[str, Any]],
        current_camera: str,
        time_since_switch: float,
    ) -> str:
        """Select best camera based on analysis"""

        if not frame_analysis:
            return current_camera

        # Score each camera
        camera_scores = {}
        for camera_name, analysis in frame_analysis.items():
            score = analysis['confidence']

            # Bonus for current camera (avoid excessive switching)
            if camera_name == current_camera and time_since_switch < 10.0:
                score *= 1.2

            camera_scores[camera_name] = score

        # Select highest scoring camera
        best_camera = max(camera_scores, key=camera_scores.get)

        # Only switch if significantly better (20% improvement)
        if best_camera != current_camera:
            if camera_scores[best_camera] < camera_scores.get(current_camera, 0) * 1.2:
                return current_camera

        return best_camera


logger.info("Scene detector module loaded")
