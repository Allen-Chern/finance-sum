"""Abstract base class for bank crawlers and shared data models."""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from playwright.async_api import Page

from finance_sum.store import BankCredential


class SyncItem(StrEnum):
    """Sync-able data categories."""

    BALANCE = "balance"
    CREDIT_CARD = "credit_card"


@dataclass
class BalanceResult:
    """A single bank account balance snapshot."""

    bank: str
    account_number: str
    balance: Decimal
    fetched_at: datetime = field(default_factory=lambda: datetime.now().astimezone())



@dataclass
class CreditCardBill:
    """A single credit card billing statement."""

    bank: str
    billing_period: str
    closing_date: date  # 帳單結帳日
    due_date: date  # 繳費截止日
    amount: Decimal  # 應繳金額
    auto_debit_account: str = ""  # 自動扣繳帳戶 (空 = 無)
    payment_date: date | None = None  # 實際繳費日期 (繳費後填入)
    dedup_id: str = ""  # SHA-256 hash, auto-generated

    def __post_init__(self) -> None:
        if not self.dedup_id:
            self.dedup_id = self._generate_dedup_id()

    def _generate_dedup_id(self) -> str:
        raw = f"{self.bank}_{self.closing_date.isoformat()}_{self.amount}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class BankCrawler(ABC):
    """Abstract base for all bank-specific crawlers.

    Subclasses must define ``bank_name`` and implement the three abstract
    methods.  The public ``run`` method orchestrates the full flow.
    """

    bank_name: str

    async def run(
        self,
        page: Page,
        credential: BankCredential,
        sync_items: list[SyncItem],
    ) -> tuple[list[BalanceResult], list[CreditCardBill]]:
        """Execute the full crawl cycle: login → fetch data."""
        balances: list[BalanceResult] = []
        bills: list[CreditCardBill] = []

        try:
            await self.login(page, credential)
            await self.handle_2fa(page)

            if SyncItem.BALANCE in sync_items:
                logging.info(f"{self.bank_name}: 同步餘額")
                balances = await self.fetch_balance(page)

            if SyncItem.CREDIT_CARD in sync_items:
                logging.info(f"{self.bank_name}: 同步信用卡帳單")
                bills = await self.fetch_credit_card_bills(page)
                # Inject auto_debit_account from credential if available
                if credential.auto_debit_account:
                    for bill in bills:
                        if not bill.auto_debit_account:
                            bill.auto_debit_account = credential.auto_debit_account
        finally:
            try:
                await self.logout(page)
            except Exception:
                pass

        return balances, bills

    # -- abstract methods (subclass must implement) ---------------------------

    @abstractmethod
    async def login(self, page: Page, credential: BankCredential) -> None:
        """Navigate to the bank site and perform login."""

    @abstractmethod
    async def fetch_balance(self, page: Page) -> list[BalanceResult]:
        """Scrape account balances after login."""

    @abstractmethod
    async def fetch_credit_card_bills(self, page: Page) -> list[CreditCardBill]:
        """Scrape credit card billing statements after login."""

    # -- hooks ----------------------------------------------------------------

    async def handle_2fa(self, page: Page) -> None:
        """Called after login.  Override for bank-specific 2FA handling.

        Default implementation pauses and waits for manual intervention.
        """
        # In headless mode this will block; concrete crawlers should override
        # if the bank requires 2FA.
        pass

    async def logout(self, page: Page) -> None:
        """Safely log out of the bank session."""
        pass
