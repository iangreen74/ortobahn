import os
import time
from datetime import datetime, timedelta
from typing import Optional

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from flask import Flask, request, jsonify
import base64
import json

app = Flask(__name__)

# Configuration from environment variables
CLOUDFRONT_DOMAIN = os.getenv("CLOUDFRONT_DOMAIN", "")
CLOUDFRONT_KEY_PAIR_ID = os.getenv("CLOUDFRONT_KEY_PAIR_ID", "")
CLOUDFRONT_PRIVATE_KEY_PATH = os.getenv("CLOUDFRONT_PRIVATE_KEY_PATH", "")


def load_private_key():
    """Load CloudFront private key from file."""
    if not CLOUDFRONT_PRIVATE_KEY_PATH or not os.path.exists(CLOUDFRONT_PRIVATE_KEY_PATH):
        return None
    
    with open(CLOUDFRONT_PRIVATE_KEY_PATH, "rb") as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(),
            password=None,
            backend=default_backend()
        )
    return private_key


def rsa_signer(message: bytes) -> bytes:
    """Sign message with RSA private key."""
    private_key = load_private_key()
    if not private_key:
        raise ValueError("CloudFront private key not configured")
    
    return private_key.sign(message, padding.PKCS1v15(), hashes.SHA1())


def generate_signed_url(object_key: str, expiration_minutes: int = 60) -> Optional[str]:
    """Generate CloudFront signed URL for private S3 object.
    
    Args:
        object_key: S3 object key (path)
        expiration_minutes: URL expiration time in minutes
        
    Returns:
        Signed CloudFront URL or None if configuration is missing
    """
    if not all([CLOUDFRONT_DOMAIN, CLOUDFRONT_KEY_PAIR_ID, CLOUDFRONT_PRIVATE_KEY_PATH]):
        return None
    
    # Generate expiration timestamp
    expire_date = datetime.utcnow() + timedelta(minutes=expiration_minutes)
    expire_timestamp = int(time.mktime(expire_date.timetuple()))
    
    # Construct URL
    cloudfront_url = f"https://{CLOUDFRONT_DOMAIN}/{object_key}"
    
    # Create policy
    policy = {
        "Statement": [{
            "Resource": cloudfront_url,
            "Condition": {
                "DateLessThan": {"AWS:EpochTime": expire_timestamp}
            }
        }]
    }
    
    # Encode policy
    policy_json = json.dumps(policy, separators=(',', ':'))
    policy_64 = base64.b64encode(policy_json.encode('utf-8')).decode('utf-8')
    policy_64 = policy_64.replace('+', '-').replace('=', '_').replace('/', '~')
    
    # Sign policy
    signature = rsa_signer(policy_json.encode('utf-8'))
    signature_64 = base64.b64encode(signature).decode('utf-8')
    signature_64 = signature_64.replace('+', '-').replace('=', '_').replace('/', '~')
    
    # Build signed URL
    signed_url = (
        f"{cloudfront_url}?"
        f"Policy={policy_64}&"
        f"Signature={signature_64}&"
        f"Key-Pair-Id={CLOUDFRONT_KEY_PAIR_ID}"
    )
    
    return signed_url


@app.route("/api/images/signed-url", methods=["POST"])
def get_signed_url():
    """Generate signed URL for image access."""
    data = request.get_json()
    
    if not data or "object_key" not in data:
        return jsonify({"error": "object_key required"}), 400
    
    object_key = data["object_key"]
    expiration_minutes = data.get("expiration_minutes", 60)
    
    try:
        signed_url = generate_signed_url(object_key, expiration_minutes)
        
        if not signed_url:
            return jsonify({"error": "CloudFront not configured"}), 500
        
        return jsonify({
            "signed_url": signed_url,
            "expires_in_minutes": expiration_minutes
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
