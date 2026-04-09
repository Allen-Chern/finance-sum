"""FinanceSum CLI — interactive login and sync commands."""

from __future__ import annotations

import asyncio
import logging
import sys

import click

from finance_sum.config import Config
from finance_sum.crawlers.base import SyncItem
from finance_sum.crawlers.registry import BANK_REGISTRY
from finance_sum.crypto import ensure_key
from finance_sum.store import BankCredential, CredentialStore

# Bank display list for interactive selection
_BANK_CHOICES = {str(i): name for i, name in enumerate(BANK_REGISTRY, 1)}


@click.group()
@click.option("--debug/--no-debug", default=False, help="啟用除錯模式")
@click.pass_context
def cli(ctx: click.Context, debug: bool) -> None:
    """FinanceSum — 私用財務自動化 CLI 工具"""
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    ctx.ensure_object(dict)
    ctx.obj["config"] = Config.load()


# ─── login ──────────────────────────────────────────────────────────────────


@cli.command()
@click.pass_context
def login(ctx: click.Context) -> None:
    """互動式設定銀行帳密"""
    config: Config = ctx.obj["config"]
    key = ensure_key(config.key_path)
    store = CredentialStore(config.credentials_path, key)

    # 1. Select bank
    click.echo("\n請選擇銀行：")
    for num, name in _BANK_CHOICES.items():
        click.echo(f"  [{num}] {name}")

    bank_num = click.prompt("輸入編號", type=click.Choice(list(_BANK_CHOICES.keys())))
    bank = _BANK_CHOICES[bank_num]
    click.echo(f"✓ 已選擇: {bank}\n")

    # 2. Select sync items
    click.echo("要同步的項目 (可多選，以逗號分隔)：")
    click.echo("  [1] 銀行餘額")
    click.echo("  [2] 信用卡帳單")

    items_input = click.prompt("輸入編號", default="1,2")
    sync_items: list[str] = []
    for item in items_input.split(","):
        item = item.strip()
        if item == "1":
            sync_items.append(SyncItem.BALANCE)
        elif item == "2":
            sync_items.append(SyncItem.CREDIT_CARD)
    click.echo(f"✓ 同步項目: {', '.join(sync_items)}\n")

    # 3. Input credentials
    id_number = click.prompt("身分證字號")
    username = click.prompt("使用者代號")
    password = click.prompt("網銀密碼", hide_input=True)

    # 4. Auto-debit account (only if credit_card is selected)
    auto_debit_account: str | None = None
    if SyncItem.CREDIT_CARD in sync_items:
        auto_debit_raw = click.prompt(
            "自動扣繳帳戶 (留空表示無自動扣繳)",
            default="",
            show_default=False,
        )
        auto_debit_account = auto_debit_raw if auto_debit_raw else None

    # 5. Save encrypted credential
    credential = BankCredential(
        bank=bank,
        sync_items=sync_items,
        id_number=id_number,
        username=username,
        password=password,
        auto_debit_account=auto_debit_account,
    )
    store.save(credential)
    click.echo(f"\n✅ {bank} 憑證已加密儲存至 {config.credentials_path}")


# ─── sync ───────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--bank", "bank_name", default=None, help="僅同步指定銀行 (例: 台新)")
@click.pass_context
def sync(ctx: click.Context, bank_name: str | None) -> None:
    """同步銀行資料至 Notion 並發送通知"""
    from finance_sum.sync import SyncOrchestrator

    config: Config = ctx.obj["config"]

    # Pre-flight checks
    if config.storage_backend == "notion" and not config.notion_api_key:
        click.echo("❌ 儲存方式設為 notion，請先設定 NOTION_API_KEY 環境變數", err=True)
        sys.exit(1)

    key = ensure_key(config.key_path)
    store = CredentialStore(config.credentials_path, key)

    banks = [bank_name] if bank_name else None

    if banks:
        for b in banks:
            if b not in BANK_REGISTRY:
                click.echo(f"❌ 不支援的銀行: {b}", err=True)
                sys.exit(1)

    orchestrator = SyncOrchestrator(config, store)
    click.echo("🔄 開始同步...\n")
    summary = asyncio.run(orchestrator.run(banks))

    # Print summary
    if summary.balance_changes:
        click.echo("📊 餘額更新：")
        for bc in summary.balance_changes:
            click.echo(f"  🏦 {bc.bank}: NT${bc.current:,.0f} ({bc.delta_str})")

    if summary.new_bills:
        click.echo(f"\n💳 新帳單 ({len(summary.new_bills)} 筆)：")
        for bill in summary.new_bills:
            debit_info = (
                f"自扣: {bill.auto_debit_account}" if bill.auto_debit_account else "無自動扣繳"
            )
            click.echo(
                f"  • {bill.bank} {bill.billing_period}: "
                f"NT${bill.amount:,.0f} (截止: {bill.due_date:%m/%d}, {debit_info})"
            )

    if summary.errors:
        click.echo(f"\n⚠️ 錯誤 ({len(summary.errors)} 個)：")
        for err in summary.errors:
            click.echo(f"  • {err}", err=True)

    click.echo("\n✅ 同步完成")


if __name__ == "__main__":
    cli()
