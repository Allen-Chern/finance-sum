"""Fernet symmetric encryption for credential storage."""

from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet


def generate_key(key_path: Path) -> bytes:
    """Generate a new Fernet key and persist it to *key_path*.

    Raises ``FileExistsError`` if the key file already exists to prevent
    accidental overwrite (which would render all stored credentials
    unrecoverable).
    """
    if key_path.exists():
        raise FileExistsError(f"金鑰檔案已存在: {key_path}")

    key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    key_path.chmod(0o600)  # owner-only read/write
    return key


def load_key(key_path: Path) -> bytes:
    """Load an existing Fernet key from *key_path*.

    Raises ``FileNotFoundError`` if the key file does not exist.
    """
    if not key_path.exists():
        raise FileNotFoundError(f"找不到金鑰檔案: {key_path}，請先執行 `financesum login`")
    return key_path.read_bytes()


def ensure_key(key_path: Path) -> bytes:
    """Load the key if it exists, otherwise generate a new one."""
    if key_path.exists():
        return load_key(key_path)
    return generate_key(key_path)


def encrypt(plaintext: str, key: bytes) -> str:
    """Encrypt *plaintext* and return a URL-safe base64 encoded string."""
    f = Fernet(key)
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str, key: bytes) -> str:
    """Decrypt a Fernet token back to the original plaintext string."""
    f = Fernet(key)
    return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
