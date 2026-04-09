"""Encrypted credential storage backed by a JSON file."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from finance_sum import crypto


@dataclass
class BankCredential:
    """Represents decrypted credentials for a single bank."""

    bank: str
    sync_items: list[str] = field(default_factory=list)  # ["balance", "credit_card"]
    id_number: str = ""
    username: str = ""
    password: str = ""
    auto_debit_account: str | None = None  # None = 不適用 / 無自動扣繳

    def to_encrypted_dict(self, key: bytes) -> dict:
        """Serialise to a dict with sensitive fields encrypted."""
        return {
            "sync_items": self.sync_items,
            "id_number": crypto.encrypt(self.id_number, key),
            "username": crypto.encrypt(self.username, key),
            "password": crypto.encrypt(self.password, key),
            "auto_debit_account": (
                crypto.encrypt(self.auto_debit_account, key)
                if self.auto_debit_account
                else None
            ),
        }

    @classmethod
    def from_encrypted_dict(cls, bank: str, data: dict, key: bytes) -> BankCredential:
        """Deserialise from an encrypted dict."""
        return cls(
            bank=bank,
            sync_items=data["sync_items"],
            id_number=crypto.decrypt(data["id_number"], key),
            username=crypto.decrypt(data["username"], key),
            password=crypto.decrypt(data["password"], key),
            auto_debit_account=(
                crypto.decrypt(data["auto_debit_account"], key)
                if data.get("auto_debit_account")
                else None
            ),
        )


class CredentialStore:
    """Read / write encrypted credentials to a JSON file."""

    def __init__(self, credentials_path: Path, key: bytes) -> None:
        self._path = credentials_path
        self._key = key

    # -- public API -----------------------------------------------------------

    def save(self, credential: BankCredential) -> None:
        """Persist a single bank credential (create or overwrite)."""
        all_data = self._read_raw()
        all_data[credential.bank] = credential.to_encrypted_dict(self._key)
        self._write_raw(all_data)

    def load(self, bank: str) -> BankCredential:
        """Load & decrypt a credential for *bank*."""
        all_data = self._read_raw()
        if bank not in all_data:
            raise KeyError(f"找不到銀行 '{bank}' 的憑證，請先執行 `financesum login`")
        return BankCredential.from_encrypted_dict(bank, all_data[bank], self._key)

    def load_all(self) -> dict[str, BankCredential]:
        """Load & decrypt credentials for every stored bank."""
        all_data = self._read_raw()
        return {
            bank: BankCredential.from_encrypted_dict(bank, data, self._key)
            for bank, data in all_data.items()
        }

    def delete(self, bank: str) -> bool:
        """Remove a bank credential.  Returns ``True`` if it existed."""
        all_data = self._read_raw()
        if bank in all_data:
            del all_data[bank]
            self._write_raw(all_data)
            return True
        return False

    def list_banks(self) -> list[str]:
        """Return the names of all stored banks."""
        return list(self._read_raw().keys())

    # -- internal helpers -----------------------------------------------------

    def _read_raw(self) -> dict:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _write_raw(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
