from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialEncryptor:
    """AES-256-GCM encryption for at-rest credential storage.

    Nonce is prepended to ciphertext and base64-encoded for storage.
    Never logs plaintext. Never sends credentials to LLM.
    """

    _NONCE_BYTES = 12  # 96-bit nonce — required by GCM spec

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("Key must be exactly 32 bytes for AES-256")
        self._gcm = AESGCM(key)

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(self._NONCE_BYTES)
        ciphertext = self._gcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, token: str) -> str:
        raw = base64.b64decode(token.encode("ascii"))
        nonce, ciphertext = raw[: self._NONCE_BYTES], raw[self._NONCE_BYTES :]
        return self._gcm.decrypt(nonce, ciphertext, None).decode("utf-8")


class SynapseError(Exception):
    """Base exception for all SYNAPSE domain errors."""


class ParseError(SynapseError):
    """Raised when an API spec cannot be parsed."""


class SecurityError(SynapseError):
    """Raised on SSRF attempts or other security violations."""


class GraphError(SynapseError):
    """Raised on Neo4j operation failures."""


class LLMError(SynapseError):
    """Raised when LLM calls fail after all retries."""


class CompressionError(SynapseError):
    """Raised when the compression pipeline encounters an unrecoverable state."""


class SynthesisError(SynapseError):
    """Raised when code generation fails."""


class StorageError(SynapseError):
    """Raised on MinIO/object storage failures."""


class StageError(SynapseError):
    """Raised when a pipeline stage fails. Carries stage name for logging."""

    def __init__(self, stage: str, message: str, cause: Exception | None = None) -> None:
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
        self.cause = cause
