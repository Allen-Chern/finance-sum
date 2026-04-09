"""Telegram Bot notification — sends sync summaries."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from decimal import Decimal
from typing import TYPE_CHECKING
import html

import telegram

if TYPE_CHECKING:
    from finance_sum.config import Config
    from finance_sum.crawlers.base import CreditCardBill


@dataclass
class BalanceChange:
    """Tracks balance before/after for a single bank."""

    bank: str
    account_number: str
    current: Decimal
    previous: Decimal | None = None

    @property
    def delta(self) -> Decimal | None:
        if self.previous is None:
            return None
        return self.current - self.previous

    @property
    def delta_str(self) -> str:
        d = self.delta
        if d is None:
            return "NEW"
        if d == 0:
            return ""
        sign = "▲ +" if d > 0 else "▼ "
        return f"{sign}{d:,.0f}"


@dataclass
class SyncSummary:
    """Aggregated result of a sync run."""

    balance_changes: list[BalanceChange] = field(default_factory=list)
    new_bills: list[CreditCardBill] = field(default_factory=list)
    total_assets: Decimal | None = None
    errors: list[str] = field(default_factory=list)


class TelegramNotifier:
    """Sends formatted sync summaries via Telegram Bot API."""

    def __init__(self, config: Config) -> None:
        self._bot = telegram.Bot(token=config.telegram_bot_token)
        self._chat_id = config.telegram_chat_id

    async def send_sync_summary(self, summary: SyncSummary) -> None:
        """Format and send the sync summary message."""
        msg = self._format_message(summary)
        await self._bot.send_message(
            chat_id=self._chat_id,
            text=msg,
            parse_mode="HTML",
        )

    @staticmethod
    def _format_message(summary: SyncSummary) -> str:
        lines = ["📊 <b>FinanceSum 同步完成</b>", "━" * 20]

        total_balance = Decimal(0)
        # Balances
        if summary.balance_changes:
            for bc in summary.balance_changes:
                # 提取帳號中所有數字，取末四碼
                nums = re.sub(r"\D", "", bc.account_number)
                if len(nums) >= 4:
                    acct_suffix = f" (*{nums[-4:]})"
                elif nums:
                    acct_suffix = f" (*{nums})"
                else:
                    acct_suffix = f" ({bc.account_number})" if bc.account_number else ""
                
                if bc.delta_str:
                    lines.append(f"🏦 {bc.bank}{acct_suffix}: NT${bc.current:,.0f} ({bc.delta_str})")
                else:
                    lines.append(f"🏦 {bc.bank}{acct_suffix}: NT${bc.current:,.0f}")
            
            if summary.total_assets is not None:
                lines.append(f"💰 <b>總資產: NT${summary.total_assets:,.0f}</b>")
            lines.append("")

        # New bills
        if summary.new_bills:
            lines.append(f"💳 <b>新帳單 ({len(summary.new_bills)} 筆)</b>")
            for bill in summary.new_bills:
                debit_info = (
                    f"自扣: {bill.auto_debit_account}" if bill.auto_debit_account else "無自動扣繳"
                )
                lines.append(
                    f"• {bill.bank} {bill.billing_period}: "
                    f"NT${bill.amount:,.0f} "
                    f"(截止: {bill.due_date:%m/%d}, {debit_info})"
                )
            lines.append("")

        # Errors
        if summary.errors:
            lines.append(f"⚠️ <b>錯誤 ({len(summary.errors)} 個)</b>")
            for err in summary.errors:
                lines.append(f"• {html.escape(str(err))}")

        return "\n".join(lines)
