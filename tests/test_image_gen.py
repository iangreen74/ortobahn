"""Tests for the AI image generation module."""

from __future__ import annotations

import base64
import io
import json
from unittest.mock import MagicMock, patch

import pytest

from ortobahn.config import Settings
from ortobahn.image_gen import ImageGenerator


@pytest.fixture()
def img_settings(tmp_path):
    """Settings with image generation enabled."""
    return Settings(
        anthropic_api_key="sk-ant-test",
        db_path=tmp_path / "img.db",
        image_generation_enabled=True,
        image_s3_bucket="test-images",
        bedrock_region="us-west-2",
        bedrock_image_model="amazon.titan-image-generator-v2:0",
    )


@pytest.fixture()
def disabled_settings(tmp_path):
    """Settings with image generation disabled."""
    return Settings(
        anthropic_api_key="sk-ant-test",
        db_path=tmp_path / "img.db",
        image_generation_enabled=False,
        image_s3_bucket="",
    )


def _fake_bedrock_response(image_bytes: bytes = b"FAKE_PNG_DATA"):
    """Create a mock Bedrock invoke_model response."""
    encoded = base64.b64encode(image_bytes).decode()
    body = json.dumps({"images": [encoded]}).encode()
    return {"body": io.BytesIO(body)}


class TestImageGenerator:
    def test_disabled_returns_none(self, disabled_settings):
        gen = ImageGenerator(disabled_settings)
        assert gen.generate("a photo of a cat", "client-1") is None

    def test_empty_prompt_returns_none(self, img_settings):
        gen = ImageGenerator(img_settings)
        assert gen.generate("", "client-1") is None
        assert gen.generate("   ", "client-1") is None

    @patch("ortobahn.image_gen.boto3")
    def test_successful_generation(self, mock_boto3, img_settings):
        mock_bedrock = MagicMock()
        mock_s3 = MagicMock()
        mock_boto3.client.side_effect = lambda svc, **kw: mock_bedrock if svc == "bedrock-runtime" else mock_s3
        mock_bedrock.invoke_model.return_value = _fake_bedrock_response()

        gen = ImageGenerator(img_settings)
        url = gen.generate("a sunset over mountains", "client-1", "post-123")

        assert url == "https://test-images.s3.us-west-2.amazonaws.com/images/client-1/post-123.png"
        mock_bedrock.invoke_model.assert_called_once()
        mock_s3.put_object.assert_called_once()

        # Verify the S3 upload had correct params
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "test-images"
        assert call_kwargs["Key"] == "images/client-1/post-123.png"
        assert call_kwargs["ContentType"] == "image/png"

    @patch("ortobahn.image_gen.boto3")
    def test_bedrock_failure_returns_none(self, mock_boto3, img_settings):
        mock_bedrock = MagicMock()
        mock_boto3.client.return_value = mock_bedrock
        mock_bedrock.invoke_model.side_effect = Exception("Bedrock unavailable")

        gen = ImageGenerator(img_settings)
        url = gen.generate("a photo", "client-1")

        assert url is None

    @patch("ortobahn.image_gen.boto3")
    def test_s3_failure_returns_none(self, mock_boto3, img_settings):
        mock_bedrock = MagicMock()
        mock_s3 = MagicMock()
        mock_boto3.client.side_effect = lambda svc, **kw: mock_bedrock if svc == "bedrock-runtime" else mock_s3
        mock_bedrock.invoke_model.return_value = _fake_bedrock_response()
        mock_s3.put_object.side_effect = Exception("S3 error")

        gen = ImageGenerator(img_settings)
        url = gen.generate("a photo", "client-1")

        assert url is None

    @patch("ortobahn.image_gen.boto3")
    def test_empty_images_response_returns_none(self, mock_boto3, img_settings):
        mock_bedrock = MagicMock()
        mock_boto3.client.return_value = mock_bedrock
        body = json.dumps({"images": []}).encode()
        mock_bedrock.invoke_model.return_value = {"body": io.BytesIO(body)}

        gen = ImageGenerator(img_settings)
        url = gen.generate("a photo", "client-1")

        assert url is None

    @patch("ortobahn.image_gen.boto3")
    def test_auto_generates_post_id(self, mock_boto3, img_settings):
        mock_bedrock = MagicMock()
        mock_s3 = MagicMock()
        mock_boto3.client.side_effect = lambda svc, **kw: mock_bedrock if svc == "bedrock-runtime" else mock_s3
        mock_bedrock.invoke_model.return_value = _fake_bedrock_response()

        gen = ImageGenerator(img_settings)
        url = gen.generate("a photo", "client-1")

        assert url is not None
        assert url.startswith("https://test-images.s3.us-west-2.amazonaws.com/images/client-1/")
        assert url.endswith(".png")

    @patch("ortobahn.image_gen.boto3")
    def test_bedrock_model_params(self, mock_boto3, img_settings):
        mock_bedrock = MagicMock()
        mock_s3 = MagicMock()
        mock_boto3.client.side_effect = lambda svc, **kw: mock_bedrock if svc == "bedrock-runtime" else mock_s3
        mock_bedrock.invoke_model.return_value = _fake_bedrock_response()

        gen = ImageGenerator(img_settings)
        gen.generate("beautiful landscape", "client-1", "post-1")

        call_kwargs = mock_bedrock.invoke_model.call_args[1]
        assert call_kwargs["modelId"] == "amazon.titan-image-generator-v2:0"
        body = json.loads(call_kwargs["body"])
        assert body["taskType"] == "TEXT_IMAGE"
        assert body["textToImageParams"]["text"] == "beautiful landscape"
        assert body["imageGenerationConfig"]["width"] == 1024
        assert body["imageGenerationConfig"]["height"] == 1024

    def test_no_bucket_disables(self, tmp_path):
        settings = Settings(
            anthropic_api_key="sk-ant-test",
            db_path=tmp_path / "img.db",
            image_generation_enabled=True,
            image_s3_bucket="",
        )
        gen = ImageGenerator(settings)
        assert not gen.enabled
        assert gen.generate("a photo", "client-1") is None
