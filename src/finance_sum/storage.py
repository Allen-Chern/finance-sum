"""Local JSON storage for those who opt out of Notion."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from finance_sum.config import Config
from finance_sum.crawlers.base import BalanceResult, CreditCardBill

logger = logging.getLogger(__name__)


class LocalJsonStore:
    """Stores sync data to a local JSON file."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.path = config.local_data_path

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"balances": {}, "credit_card_bills": []}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("無法讀取 %s: %s", self.path, e)
            return {"balances": {}, "credit_card_bills": []}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def upsert_balances(self, balances: list[BalanceResult]) -> dict[str, Decimal]:
        """Upsert balances and return previous sums."""
        data = self._load()
        prev_balances: dict[str, Decimal] = {}
        
        db_balances = data.get("balances", {})
        
        for bal in balances:
            key = f"{bal.bank}_{bal.account_number}"
            # Record previous value for diff
            if key in db_balances:
                prev_balances[key] = Decimal(str(db_balances[key]))
            
            # Update current
            db_balances[key] = float(bal.balance)
            
        data["balances"] = db_balances
        self._save(data)
        return prev_balances

    def get_total_balance(self) -> Decimal:
        data = self._load()
        total = Decimal("0")
        for val in data.get("balances", {}).values():
            total += Decimal(str(val))
        return total

    def sync_credit_card_bills(self, bills: list[CreditCardBill]) -> int:
        data = self._load()
        db_bills = data.get("credit_card_bills", [])
        
        # We index existing bills by bank+period to avoid duplicates
        existing_keys = {f"{b.get('bank')}_{b.get('billing_period')}" for b in db_bills}
        
        new_count = 0
        for bill in bills:
            key = f"{bill.bank}_{bill.billing_period}"
            if key not in existing_keys:
                db_bills.append({
                    "bank": bill.bank,
                    "billing_period": bill.billing_period,
                    "amount": float(bill.amount),
                    "closing_date": bill.closing_date.isoformat(),
                    "due_date": bill.due_date.isoformat(),
                    "auto_debit_account": bill.auto_debit_account,
                })
                existing_keys.add(key)
                new_count += 1
                
        data["credit_card_bills"] = db_bills
        self._save(data)
        
        return new_count
