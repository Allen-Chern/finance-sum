"""Tests for the crypto module."""

from __future__ import annotations

import pytest
from pathlib import Path

from finance_sum.crypto import decrypt, encrypt, ensure_key, generate_key, load_key


class TestKeyGeneration:
    def test_generate_and_load_roundtrip(self, tmp_path: Path) -> None:
        key_path = tmp_path / ".key"
        key = generate_key(key_path)

        assert key_path.exists()
        assert load_key(key_path) == key

    def test_generate_key_refuses_overwrite(self, tmp_path: Path) -> None:
        key_path = tmp_path / ".key"
        generate_key(key_path)

        with pytest.raises(FileExistsError):
            generate_key(key_path)

    def test_load_key_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_key(tmp_path / "nonexistent.key")

    def test_ensure_key_creates_if_missing(self, tmp_path: Path) -> None:
        key_path = tmp_path / ".key"
        key = ensure_key(key_path)

        assert key_path.exists()
        assert ensure_key(key_path) == key  # second call loads existing

    def test_key_file_permissions(self, tmp_path: Path) -> None:
        key_path = tmp_path / ".key"
        generate_key(key_path)

        # Owner read/write only (0o600)
        mode = key_path.stat().st_mode & 0o777
        assert mode == 0o600


class TestEncryptDecrypt:
    def test_roundtrip(self, tmp_path: Path) -> None:
        key = generate_key(tmp_path / ".key")
        plaintext = "my-secret-password"

        ciphertext = encrypt(plaintext, key)
        assert ciphertext != plaintext
        assert decrypt(ciphertext, key) == plaintext

    def test_different_keys_fail(self, tmp_path: Path) -> None:
        key1 = generate_key(tmp_path / ".key1")
        key2 = generate_key(tmp_path / ".key2")

        ciphertext = encrypt("secret", key1)

        with pytest.raises(Exception):
            decrypt(ciphertext, key2)

    def test_unicode_support(self, tmp_path: Path) -> None:
        key = generate_key(tmp_path / ".key")
        plaintext = "密碼測試 🔐 パスワード"

        assert decrypt(encrypt(plaintext, key), key) == plaintext

    def test_empty_string(self, tmp_path: Path) -> None:
        key = generate_key(tmp_path / ".key")
        assert decrypt(encrypt("", key), key) == ""
