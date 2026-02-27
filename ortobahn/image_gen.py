"""AI image generation via Amazon Bedrock Titan Image Generator v2."""

from __future__ import annotations

import base64
import json
import logging
import uuid

import boto3

from ortobahn.config import Settings

logger = logging.getLogger("ortobahn.image_gen")


class ImageGenerator:
    """Generate images using Bedrock and store them in S3."""

    def __init__(self, settings: Settings) -> None:
        self.enabled = bool(settings.image_generation_enabled and settings.image_s3_bucket)
        self.model_id = settings.bedrock_image_model
        self.region = settings.bedrock_region
        self.bucket = settings.image_s3_bucket

    def generate(self, prompt: str, client_id: str, post_id: str | None = None) -> str | None:
        """Generate an image from *prompt*, upload to S3, return the public URL.

        Returns ``None`` on any failure — the caller should fall back to
        text-only publishing.
        """
        if not self.enabled:
            return None

        if not prompt or not prompt.strip():
            return None

        image_id = post_id or str(uuid.uuid4())
        key = f"images/{client_id}/{image_id}.png"

        try:
            image_bytes = self._generate_image(prompt)
            if not image_bytes:
                return None
            url = self._upload_to_s3(image_bytes, key)
            logger.info("Generated image for %s: %s", client_id, key)
            return url
        except Exception:
            logger.warning("Image generation failed (non-fatal)", exc_info=True)
            return None

    def _generate_image(self, prompt: str) -> bytes | None:
        """Call Bedrock Titan Image Generator and return raw PNG bytes."""
        bedrock = boto3.client("bedrock-runtime", region_name=self.region)

        body = json.dumps(
            {
                "taskType": "TEXT_IMAGE",
                "textToImageParams": {"text": prompt},
                "imageGenerationConfig": {
                    "numberOfImages": 1,
                    "width": 1024,
                    "height": 1024,
                    "cfgScale": 8.0,
                },
            }
        )

        response = bedrock.invoke_model(
            modelId=self.model_id,
            contentType="application/json",
            accept="application/json",
            body=body,
        )

        result = json.loads(response["body"].read())
        images = result.get("images", [])
        if not images:
            logger.warning("Bedrock returned no images")
            return None

        return base64.b64decode(images[0])

    def _upload_to_s3(self, image_bytes: bytes, key: str) -> str:
        """Upload image bytes to S3 and return the public URL."""
        s3 = boto3.client("s3", region_name=self.region)
        s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=image_bytes,
            ContentType="image/png",
        )
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{key}"
