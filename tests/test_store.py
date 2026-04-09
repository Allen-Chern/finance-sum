"""Tests for the credential store module."""

from __future__ import annotations

import json

import pytest
from pathlib import Path

from finance_sum.crypto import generate_key
from finance_sum.store import BankCredential, CredentialStore


@pytest.fixture()
def key(tmp_path: Path) -> bytes:
    return generate_key(tmp_path / ".key")


@pytest.fixture()
def store(tmp_path: Path, key: bytes) -> CredentialStore:
    return CredentialStore(tmp_path / "credentials.json", key)


@pytest.fixture()
def sample_credential() -> BankCredential:
    return BankCredential(
        bank="台新",
        sync_items=["balance", "credit_card"],
        id_number="A123456789",
        username="testuser",
        password="testpass",
        auto_debit_account="1234-5678-9012",
    )


class TestCredentialStore:
    def test_save_and_load(
        self, store: CredentialStore, sample_credential: BankCredential
    ) -> None:
        store.save(sample_credential)
        loaded = store.load("台新")

        assert loaded.bank == "台新"
        assert loaded.id_number == "A123456789"
        assert loaded.username == "testuser"
        assert loaded.password == "testpass"
        assert loaded.auto_debit_account == "1234-5678-9012"
        assert loaded.sync_items == ["balance", "credit_card"]

    def test_load_missing_bank(self, store: CredentialStore) -> None:
        with pytest.raises(KeyError, match="找不到銀行"):
            store.load("不存在的銀行")

    def test_load_all(
        self, store: CredentialStore, sample_credential: BankCredential
    ) -> None:
        store.save(sample_credential)
        store.save(BankCredential(
            bank="國泰", sync_items=["balance"],
            id_number="B987654321", username="user2", password="pass2",
        ))

        all_creds = store.load_all()
        assert len(all_creds) == 2
        assert "台新" in all_creds
        assert "國泰" in all_creds

    def test_delete(
        self, store: CredentialStore, sample_credential: BankCredential
    ) -> None:
        store.save(sample_credential)
        assert store.delete("台新") is True
        assert store.delete("台新") is False

        with pytest.raises(KeyError):
            store.load("台新")

    def test_list_banks(
        self, store: CredentialStore, sample_credential: BankCredential
    ) -> None:
        assert store.list_banks() == []
        store.save(sample_credential)
        assert store.list_banks() == ["台新"]

    def test_encrypted_json_has_no_plaintext(
        self, store: CredentialStore, sample_credential: BankCredential, tmp_path: Path
    ) -> None:
        store.save(sample_credential)

        raw = (tmp_path / "credentials.json").read_text(encoding="utf-8")

        # Sensitive values must NOT appear in plaintext
        assert "A123456789" not in raw
        assert "testuser" not in raw
        assert "testpass" not in raw
        assert "1234-5678-9012" not in raw

        # But the bank name and sync items are not encrypted
        data = json.loads(raw)
        assert "台新" in data
        assert data["台新"]["sync_items"] == ["balance", "credit_card"]

    def test_null_auto_debit_account(
        self, store: CredentialStore
    ) -> None:
        cred = BankCredential(
            bank="國泰", sync_items=["balance"],
            id_number="B987654321", username="user2", password="pass2",
            auto_debit_account=None,
        )
        store.save(cred)
        loaded = store.load("國泰")
        assert loaded.auto_debit_account is None

    def test_overwrite_existing(
        self, store: CredentialStore, sample_credential: BankCredential
    ) -> None:
        store.save(sample_credential)
        updated = BankCredential(
            bank="台新", sync_items=["balance"],
            id_number="A123456789", username="newuser", password="newpass",
        )
        store.save(updated)

        loaded = store.load("台新")
        assert loaded.username == "newuser"
        assert loaded.sync_items == ["balance"]
