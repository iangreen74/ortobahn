from __future__ import annotations

import base64
import os
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import String, TypeDecorator


class EncryptedString(TypeDecorator):
    """SQLAlchemy type decorator for encrypted string fields.
    
    Uses Fernet symmetric encryption with PBKDF2HMAC key derivation.
    Each encrypted value stores its own salt for maximum security.
    """

    impl = String
    cache_ok = True

    def __init__(self, master_key: str, *args: Any, **kwargs: Any) -> None:
        """Initialize with master encryption key.
        
        Args:
            master_key: Master key for deriving encryption keys
            *args: Additional positional arguments for String type
            **kwargs: Additional keyword arguments for String type
        """
        super().__init__(*args, **kwargs)
        self._master_key = master_key.encode()

    def _derive_fernet_key(self, salt: bytes) -> bytes:
        """Derive a Fernet key using PBKDF2HMAC.
        
        Args:
            salt: Random salt for key derivation
            
        Returns:
            32-byte key suitable for Fernet encryption
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        return base64.urlsafe_b64encode(kdf.derive(self._master_key))

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        """Encrypt value before storing in database.
        
        Args:
            value: Plain text value to encrypt
            dialect: SQLAlchemy dialect (unused)
            
        Returns:
            Base64-encoded string containing salt and encrypted data
        """
        if value is None:
            return None

        # Generate random salt for this encryption operation
        salt = os.urandom(16)
        
        # Derive encryption key from master key and salt
        fernet_key = self._derive_fernet_key(salt)
        fernet = Fernet(fernet_key)
        
        # Encrypt the value
        encrypted_data = fernet.encrypt(value.encode())
        
        # Store salt + encrypted data together
        # Format: base64(salt) + ":" + base64(encrypted_data)
        combined = base64.urlsafe_b64encode(salt).decode() + ":" + encrypted_data.decode()
        return combined

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        """Decrypt value retrieved from database.
        
        Args:
            value: Encrypted value from database
            dialect: SQLAlchemy dialect (unused)
            
        Returns:
            Decrypted plain text string
        """
        if value is None:
            return None

        try:
            # Split salt and encrypted data
            salt_b64, encrypted_data_b64 = value.split(":", 1)
            salt = base64.urlsafe_b64decode(salt_b64.encode())
            encrypted_data = encrypted_data_b64.encode()
            
            # Derive the same key using stored salt
            fernet_key = self._derive_fernet_key(salt)
            fernet = Fernet(fernet_key)
            
            # Decrypt the data
            decrypted = fernet.decrypt(encrypted_data)
            return decrypted.decode()
        except Exception as e:
            # Log error in production; for now raise to catch issues
            raise ValueError(f"Failed to decrypt value: {e}") from e


class EncryptionMixin:
    """Mixin for models requiring encrypted fields.
    
    Provides helper methods for working with encrypted data.
    """

    @classmethod
    def get_encrypted_field(cls, master_key: str, length: int = 255) -> EncryptedString:
        """Create an encrypted string field.
        
        Args:
            master_key: Master encryption key
            length: Maximum length for database column
            
        Returns:
            EncryptedString type decorator instance
        """
        return EncryptedString(master_key, length=length)
