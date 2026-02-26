"""AWS SES email client for sending digest and notification emails."""

from __future__ import annotations

import logging

logger = logging.getLogger("ortobahn.ses")


class SESClient:
    """Thin wrapper around AWS SES for sending HTML emails."""

    def __init__(self, region: str = "us-west-2", sender_email: str = "") -> None:
        self.region = region
        self.sender_email = sender_email
        self._client = None

    def _get_client(self):
        """Lazily initialize the boto3 SES client."""
        if self._client is None:
            try:
                import boto3

                self._client = boto3.client("ses", region_name=self.region)
            except ImportError:
                logger.error("boto3 not installed — cannot send emails")
                raise
        return self._client

    def send_html_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: str = "",
    ) -> str | None:
        """Send an HTML email via SES.

        Returns the SES message ID on success, None on failure.
        """
        if not self.sender_email:
            logger.error("SES sender email not configured")
            return None
        if not to_email:
            logger.warning("No recipient email provided")
            return None

        try:
            client = self._get_client()
            response = client.send_email(
                Source=self.sender_email,
                Destination={"ToAddresses": [to_email]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": html_body, "Charset": "UTF-8"},
                        "Text": {"Data": text_body or subject, "Charset": "UTF-8"},
                    },
                },
            )
            message_id = response.get("MessageId", "")
            logger.info("Sent email to %s (MessageId: %s)", to_email, message_id)
            return message_id
        except Exception as e:
            logger.error("Failed to send email to %s: %s", to_email, e)
            return None
