"""Notion API integration — balance upsert and credit card bill dedup."""

from __future__ import annotations

from typing import TYPE_CHECKING
from decimal import Decimal

from notion_client import Client

if TYPE_CHECKING:
    from finance_sum.config import Config
    from finance_sum.crawlers.base import BalanceResult, CreditCardBill


class NotionClient:
    """Thin wrapper around the Notion SDK for FinanceSum operations."""

    def __init__(self, config: Config) -> None:
        # notion-client 3.0.0 defaults to 2025-09-03 API version which removes databases/*/query.
        # We pin the API version to 2022-06-28 to keep compatibility.
        self._client = Client(auth=config.notion_api_key, notion_version="2022-06-28")
        self._balance_db = config.notion_balance_db_id
        self._cc_db = config.notion_credit_card_db_id

    # -- Balance (覆蓋更新) ---------------------------------------------------

    def upsert_balances(self, balances: list[BalanceResult]) -> dict[str, Decimal]:
        """Overwrite-update balances — one row per bank/account. Returns mapping of previous balances."""
        previous_balances: dict[str, Decimal] = {}
        
        for bal in balances:
            existing_page = self._find_balance_page(bal.bank, bal.account_number)
            properties = self._balance_properties(bal)
            key = f"{bal.bank}_{bal.account_number}"

            if existing_page:
                try:
                    old_num = existing_page["properties"].get("餘額", {}).get("number")
                    if old_num is not None:
                        previous_balances[key] = Decimal(str(old_num))
                except Exception:
                    pass
                
                self._client.pages.update(page_id=existing_page["id"], properties=properties)
            else:
                self._client.pages.create(
                    parent={"database_id": self._balance_db},
                    properties=properties,
                )
        return previous_balances

    def get_total_balance(self) -> Decimal:
        """Fetch all balance rows from Notion and sum the '餘額' field."""
        total = Decimal(0)
        has_more = True
        next_cursor = None
        
        while has_more:
            body: dict = {}
            if next_cursor:
                body["start_cursor"] = next_cursor
                
            resp = self._client.request(
                path=f"databases/{self._balance_db}/query",
                method="POST",
                body=body
            )
            
            for page in resp.get("results", []):
                val = page["properties"].get("餘額", {}).get("number")
                if val is not None:
                    total += Decimal(str(val))
            
            has_more = resp.get("has_more", False)
            next_cursor = resp.get("next_cursor")
            
        return total

    def _find_balance_page(self, bank: str, account_number: str) -> dict | None:
        """Find existing balance page by bank name and account number."""
        if account_number:
            acct_filter = {"property": "帳號", "rich_text": {"equals": account_number}}
        else:
            acct_filter = {"property": "帳號", "rich_text": {"is_empty": True}}

        filter_spec = {"and": [
            {"property": "銀行", "title": {"equals": bank}},
            acct_filter
        ]}
        
        resp = self._client.request(
            path=f"databases/{self._balance_db}/query",
            method="POST",
            body={"filter": filter_spec},
        )
        pages = resp.get("results", [])
        return pages[0] if pages else None

    @staticmethod
    def _balance_properties(bal: BalanceResult) -> dict:
        return {
            "銀行": {"title": [{"text": {"content": bal.bank}}]},
            "帳號": {"rich_text": [{"text": {"content": bal.account_number}}]},
            "餘額": {"number": float(bal.balance)},
            "更新時間": {"date": {"start": bal.fetched_at.isoformat()}},
        }

    # -- Credit Card Bills (去重新增) -----------------------------------------

    def sync_credit_card_bills(self, bills: list[CreditCardBill]) -> int:
        """Add new bills that don't already exist.  Returns count of new bills."""
        new_count = 0
        for bill in bills:
            if not self._bill_exists(bill.dedup_id):
                self._client.pages.create(
                    parent={"database_id": self._cc_db},
                    properties=self._bill_properties(bill),
                )
                new_count += 1
        return new_count

    def _bill_exists(self, dedup_id: str) -> bool:
        """Check if a bill with this dedup_id already exists."""
        # In notion-client v3, databases.query was removed. Use raw request.
        resp = self._client.request(
            path=f"databases/{self._cc_db}/query",
            method="POST",
            body={"filter": {"property": "帳單ID", "title": {"equals": dedup_id}}},
        )
        return len(resp.get("results", [])) > 0

    @staticmethod
    def _bill_properties(bill: CreditCardBill) -> dict:
        props: dict = {
            "帳單ID": {"title": [{"text": {"content": bill.dedup_id}}]},
            "銀行": {"select": {"name": bill.bank}},
            "期別": {"rich_text": [{"text": {"content": bill.billing_period}}]},
            "帳單結帳日": {"date": {"start": bill.closing_date.isoformat()}},
            "繳費截止日": {"date": {"start": bill.due_date.isoformat()}},
            "應繳金額": {"number": float(bill.amount)},
        }
        if bill.auto_debit_account:
            props["自動扣繳帳戶"] = {
                "rich_text": [{"text": {"content": bill.auto_debit_account}}]
            }
        if bill.payment_date:
            props["繳費日期"] = {"date": {"start": bill.payment_date.isoformat()}}
        return props
