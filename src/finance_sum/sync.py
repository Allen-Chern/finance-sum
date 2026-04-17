"""Sync orchestrator — coordinates crawlers, Notion writes, and notifications."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from playwright.async_api import async_playwright

from finance_sum.config import Config
from finance_sum.crawlers.base import BalanceResult, CreditCardBill, SyncItem
from finance_sum.crawlers.registry import BANK_REGISTRY, get_crawler
from finance_sum.notify import BalanceChange, SyncSummary, TelegramNotifier
from finance_sum.notion import NotionClient
from finance_sum.storage import LocalJsonStore
from finance_sum.store import CredentialStore

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SyncOrchestrator:
    """Runs the full sync pipeline for one or more banks."""

    def __init__(self, config: Config, store: CredentialStore) -> None:
        self._config = config
        self._store = store

    async def run(self, banks: list[str] | None = None) -> SyncSummary:
        """Execute sync for *banks* (default: all stored credentials)."""
        if banks is None:
            banks = self._store.list_banks()

        # Validate bank names
        for bank in banks:
            if bank not in BANK_REGISTRY:
                raise ValueError(f"不支援的銀行: {bank}")

        summary = SyncSummary()
        all_balances: list[BalanceResult] = []
        all_bills: list[CreditCardBill] = []

        browser_state_dir = self._config.credentials_path.parent / "browser_state"
        browser_state_dir.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._config.headless)

            for bank in banks:
                try:
                    credential = self._store.load(bank)
                    sync_items = [SyncItem(item) for item in credential.sync_items]
                    crawler = get_crawler(bank)

                    state_path = browser_state_dir / f"{bank}.json"
                    context = await browser.new_context(
                        storage_state=str(state_path) if state_path.exists() else None
                    )
                    page = await context.new_page()
                    balances, bills = await crawler.run(page, credential, sync_items)
                    await context.storage_state(path=str(state_path))
                    await page.close()
                    await context.close()

                    all_balances.extend(balances)
                    all_bills.extend(bills)

                    logger.info(
                        f"{bank}: {len(balances)} 筆餘額, {len(bills)} 筆帳單"
                    )
                except Exception as e:
                    error_msg = f"{bank}: {e}"
                    logger.error(error_msg)
                    summary.errors.append(error_msg)

            await browser.close()

        # Write to Backend
        try:
            if self._config.storage_backend == "json":
                store_adapter = LocalJsonStore(self._config)
            else:
                store_adapter = NotionClient(self._config)

            if all_balances:
                prev_balances = store_adapter.upsert_balances(all_balances)
                for bal in all_balances:
                    key = f"{bal.bank}_{bal.account_number}"
                    summary.balance_changes.append(
                        BalanceChange(
                            bank=bal.bank,
                            account_number=bal.account_number,
                            current=bal.balance,
                            previous=prev_balances.get(key)
                        )
                    )
                # 獲取資料庫中所有帳戶的總額 (包含本次未同步的帳戶)
                summary.total_assets = store_adapter.get_total_balance()
            if all_bills:
                new_count = store_adapter.sync_credit_card_bills(all_bills)
                # Only include newly added bills in summary
                summary.new_bills = all_bills[:new_count] if new_count else []
        except Exception as e:
            summary.errors.append(f"儲存失敗: {e}")

        # Send Telegram notification
        try:
            if self._config.telegram_bot_token:
                notifier = TelegramNotifier(self._config)
                await notifier.send_sync_summary(summary)
        except Exception as e:
            summary.errors.append(f"Telegram 通知失敗: {e}")

        return summary
