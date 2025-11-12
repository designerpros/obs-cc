"""
ComfyUI API client for B-roll generation
Integrates with ComfyUI for SDXL/Flux image generation
"""
import asyncio
import httpx
import json
import base64
from pathlib import Path
from typing import Dict, Any, Optional
import time

from loguru import logger

from ..common.config import config


class ComfyUIClient:
    """
    ComfyUI API client for B-roll panel generation

    Phase 3: SDXL/Flux integration
    Phase 4: pgvector library reuse
    """

    def __init__(self):
        self.base_url = config.get('broll.comfyui.url', 'http://localhost:8188')
        self.timeout = config.get('broll.comfyui.timeout', 120)
        self.client = None

        # Style presets
        self.style_presets = {
            'ghibli': {
                'style_prompt': 'Studio Ghibli style, hand-drawn animation, watercolor, soft colors, dreamy atmosphere',
                'negative': 'photorealistic, 3d render, low quality',
            },
            'cyberpunk': {
                'style_prompt': 'cyberpunk style, neon lights, futuristic city, dark atmosphere, high contrast',
                'negative': 'bright, daylight, natural, low quality',
            },
            'minimalist': {
                'style_prompt': 'minimalist style, clean lines, simple shapes, flat design, modern',
                'negative': 'cluttered, detailed, complex, low quality',
            },
            'cinematic': {
                'style_prompt': 'cinematic style, film grain, dramatic lighting, wide angle, professional',
                'negative': 'amateur, poorly lit, low quality',
            },
            'general': {
                'style_prompt': 'high quality, detailed, professional',
                'negative': 'low quality, blurry, distorted',
            },
        }

    async def initialize(self):
        """Initialize ComfyUI client"""
        self.client = httpx.AsyncClient(timeout=self.timeout)

        # Check if ComfyUI is available
        try:
            response = await self.client.get(f"{self.base_url}/system_stats")
            if response.status_code == 200:
                logger.info(f"ComfyUI client initialized: {self.base_url}")
            else:
                logger.warning(f"ComfyUI connection issue: HTTP {response.status_code}")
        except Exception as e:
            logger.warning(f"ComfyUI not available: {e}")

    async def cleanup(self):
        """Cleanup resources"""
        if self.client:
            await self.client.aclose()
        logger.info("ComfyUI client cleaned up")

    async def generate_broll(
        self,
        prompt: str,
        style: str = 'general',
        width: int = 1920,
        height: int = 1080,
        output_path: Path = None,
    ) -> Optional[Path]:
        """
        Generate B-roll panel using ComfyUI

        Args:
            prompt: Generation prompt (content description)
            style: Style preset (ghibli, cyberpunk, minimalist, cinematic, general)
            width: Image width
            height: Image height
            output_path: Output file path

        Returns:
            Path to generated image or None if failed
        """
        logger.info(f"Generating B-roll: {prompt[:50]}... (style: {style})")

        # Get style preset
        style_config = self.style_presets.get(style, self.style_presets['general'])

        # Build full prompt
        full_prompt = f"{prompt}, {style_config['style_prompt']}"
        negative_prompt = style_config['negative']

        # Build ComfyUI workflow
        workflow = self._build_workflow(
            prompt=full_prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
        )

        try:
            # Queue prompt
            prompt_id = await self._queue_prompt(workflow)

            if not prompt_id:
                logger.error("Failed to queue ComfyUI prompt")
                return None

            # Wait for completion and get result
            image_data = await self._wait_for_completion(prompt_id)

            if not image_data:
                logger.error("Failed to get ComfyUI result")
                return None

            # Save image
            if output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, 'wb') as f:
                    f.write(image_data)

                logger.info(f"B-roll generated: {output_path}")
                return output_path
            else:
                return None

        except Exception as e:
            logger.exception(f"ComfyUI generation error: {e}")
            return None

    def _build_workflow(
        self,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
    ) -> Dict[str, Any]:
        """
        Build ComfyUI workflow JSON

        This is a basic SDXL workflow. Can be customized based on ComfyUI setup.
        """
        # Model selection from config
        checkpoint = config.get('broll.comfyui.checkpoint', 'sd_xl_base_1.0.safetensors')
        steps = config.get('broll.comfyui.steps', 25)
        cfg = config.get('broll.comfyui.cfg', 7.0)
        sampler = config.get('broll.comfyui.sampler', 'euler')
        scheduler = config.get('broll.comfyui.scheduler', 'normal')

        workflow = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {
                    "ckpt_name": checkpoint
                }
            },
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": prompt,
                    "clip": ["1", 1]
                }
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": negative_prompt,
                    "clip": ["1", 1]
                }
            },
            "4": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": width,
                    "height": height,
                    "batch_size": 1
                }
            },
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": int(time.time() * 1000) % 2**32,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": sampler,
                    "scheduler": scheduler,
                    "denoise": 1.0,
                    "model": ["1", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["4", 0]
                }
            },
            "6": {
                "class_type": "VAEDecode",
                "inputs": {
                    "samples": ["5", 0],
                    "vae": ["1", 2]
                }
            },
            "7": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": "broll",
                    "images": ["6", 0]
                }
            }
        }

        return workflow

    async def _queue_prompt(self, workflow: Dict[str, Any]) -> Optional[str]:
        """Queue prompt with ComfyUI API"""
        try:
            response = await self.client.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow},
            )

            if response.status_code == 200:
                data = response.json()
                prompt_id = data.get('prompt_id')
                logger.debug(f"Queued ComfyUI prompt: {prompt_id}")
                return prompt_id
            else:
                logger.error(f"Failed to queue prompt: HTTP {response.status_code}")
                return None

        except Exception as e:
            logger.exception(f"Error queuing prompt: {e}")
            return None

    async def _wait_for_completion(
        self,
        prompt_id: str,
        poll_interval: float = 2.0,
        max_wait: float = 300.0,
    ) -> Optional[bytes]:
        """Wait for prompt completion and get result image"""
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                # Check history for this prompt
                response = await self.client.get(f"{self.base_url}/history/{prompt_id}")

                if response.status_code == 200:
                    history = response.json()

                    if prompt_id in history:
                        # Prompt completed
                        outputs = history[prompt_id].get('outputs', {})

                        # Find SaveImage output node (node 7 in our workflow)
                        for node_id, output in outputs.items():
                            if 'images' in output:
                                images = output['images']
                                if images:
                                    # Get first image
                                    image_info = images[0]
                                    filename = image_info['filename']
                                    subfolder = image_info.get('subfolder', '')
                                    folder_type = image_info.get('type', 'output')

                                    # Download image
                                    return await self._download_image(
                                        filename=filename,
                                        subfolder=subfolder,
                                        folder_type=folder_type,
                                    )

            except Exception as e:
                logger.warning(f"Error checking prompt status: {e}")

            await asyncio.sleep(poll_interval)

        logger.error(f"Timeout waiting for ComfyUI completion: {prompt_id}")
        return None

    async def _download_image(
        self,
        filename: str,
        subfolder: str = '',
        folder_type: str = 'output',
    ) -> Optional[bytes]:
        """Download image from ComfyUI"""
        try:
            url = f"{self.base_url}/view"
            params = {
                'filename': filename,
                'type': folder_type,
            }
            if subfolder:
                params['subfolder'] = subfolder

            response = await self.client.get(url, params=params)

            if response.status_code == 200:
                return response.content
            else:
                logger.error(f"Failed to download image: HTTP {response.status_code}")
                return None

        except Exception as e:
            logger.exception(f"Error downloading image: {e}")
            return None


logger.info("ComfyUI client module loaded")
