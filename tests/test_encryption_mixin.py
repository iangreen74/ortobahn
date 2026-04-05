from __future__ import annotations

import pytest
from cryptography.fernet import InvalidToken

from ortobahn.db.mixins.encryption import EncryptedString, EncryptionMixin


class TestEncryptedString:
    """Test suite for EncryptedString type decorator."""

    @pytest.fixture
    def encrypted_field(self) -> EncryptedString:
        """Create an encrypted field for testing."""
        return EncryptedString(master_key="test-master-key-12345")

    def test_encrypt_decrypt_roundtrip(self, encrypted_field: EncryptedString) -> None:
        """Test that encryption and decryption work correctly."""
        original_value = "sensitive-data-123"
        
        # Encrypt
        encrypted = encrypted_field.process_bind_param(original_value, None)
        assert encrypted is not None
        assert encrypted != original_value
        assert ":") in encrypted  # Should contain salt separator
        
        # Decrypt
        decrypted = encrypted_field.process_result_value(encrypted, None)
        assert decrypted == original_value

    def test_none_value_handling(self, encrypted_field: EncryptedString) -> None:
        """Test that None values are handled correctly."""
        encrypted = encrypted_field.process_bind_param(None, None)
        assert encrypted is None
        
        decrypted = encrypted_field.process_result_value(None, None)
        assert decrypted is None

    def test_different_salts_produce_different_ciphertexts(
        self, encrypted_field: EncryptedString
    ) -> None:
        """Test that same plaintext encrypts to different values (due to random salt)."""
        original_value = "same-plaintext"
        
        encrypted1 = encrypted_field.process_bind_param(original_value, None)
        encrypted2 = encrypted_field.process_bind_param(original_value, None)
        
        # Different salts mean different encrypted outputs
        assert encrypted1 != encrypted2
        
        # But both decrypt to same value
        assert encrypted_field.process_result_value(encrypted1, None) == original_value
        assert encrypted_field.process_result_value(encrypted2, None) == original_value

    def test_invalid_encrypted_data_raises_error(
        self, encrypted_field: EncryptedString
    ) -> None:
        """Test that corrupted data raises appropriate error."""
        with pytest.raises(ValueError, match="Failed to decrypt"):
            encrypted_field.process_result_value("invalid:data", None)

    def test_different_master_keys_cannot_decrypt(
        self, encrypted_field: EncryptedString
    ) -> None:
        """Test that data encrypted with one key cannot be decrypted with another."""
        original_value = "secret"
        encrypted = encrypted_field.process_bind_param(original_value, None)
        
        # Try to decrypt with different master key
        wrong_key_field = EncryptedString(master_key="wrong-master-key")
        with pytest.raises(ValueError, match="Failed to decrypt"):
            wrong_key_field.process_result_value(encrypted, None)


class TestEncryptionMixin:
    """Test suite for EncryptionMixin."""

    def test_get_encrypted_field_creates_instance(self) -> None:
        """Test that get_encrypted_field creates proper EncryptedString instance."""
        field = EncryptionMixin.get_encrypted_field(
            master_key="test-key", length=512
        )
        
        assert isinstance(field, EncryptedString)
        assert field.impl.length == 512
