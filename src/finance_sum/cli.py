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
@click.pass_context
def manual_bill(ctx: click.Context) -> None:
    """手動新增信用卡帳單 (適用於尚未支援的銀行)"""
    from datetime import date, datetime
    from decimal import Decimal
    from finance_sum.crawlers.base import CreditCardBill
    from finance_sum.notion import NotionClient
    from finance_sum.storage import LocalJsonStore
    from finance_sum.notify import SyncSummary, TelegramNotifier

    config: Config = ctx.obj["config"]

    # 1. 選擇或輸入銀行
    click.echo("\n請選擇或輸入銀行：")
    for num, name in _BANK_CHOICES.items():
        click.echo(f"  [{num}] {name}")
    click.echo(f"  [0] 其他 (手動輸入)")

    bank_num = click.prompt("輸入編號", default="0")
    if bank_num == "0":
        bank = click.prompt("輸入銀行名稱 (例: 渣打)")
    else:
        bank = _BANK_CHOICES.get(bank_num, "未知銀行")
    
    click.echo(f"✓ 已選擇: {bank}\n")

    # 2. 基本資訊
    billing_period = click.prompt("帳單期別 (例: 2024/04)", default=date.today().strftime("%Y/%m"))
    
    def parse_date(text: str) -> date:
        for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d", "%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                if fmt in ("%m/%d", "%m-%d"):
                    dt = dt.replace(year=date.today().year)
                return dt.date()
            except ValueError:
                continue
        raise ValueError("日期格式不正確 (請用 YYYY/MM/DD 或 MM/DD)")

    closing_date_raw = click.prompt("帳單結帳日 (例: 04/10)", type=str)
    closing_date = parse_date(closing_date_raw)

    due_date_raw = click.prompt("繳費截止日 (例: 04/25)", type=str)
    due_date = parse_date(due_date_raw)

    amount = click.prompt("應繳金額", type=Decimal)

    auto_debit_account = click.prompt(
        "自動扣繳帳戶 (留空表示無)",
        default="",
        show_default=False,
    )

    # 3. 建立帳單物件
    bill = CreditCardBill(
        bank=bank,
        billing_period=billing_period,
        closing_date=closing_date,
        due_date=due_date,
        amount=amount,
        auto_debit_account=auto_debit_account if auto_debit_account else "",
    )

    # 4. 儲存至後端
    click.echo(f"\n🚀 正在儲存至 {config.storage_backend}...")
    try:
        if config.storage_backend == "json":
            store_adapter = LocalJsonStore(config)
        else:
            store_adapter = NotionClient(config)
        
        new_count = store_adapter.sync_credit_card_bills([bill])
        
        if new_count > 0:
            click.echo(f"✅ 帳單已存入 {config.storage_backend}")
            
            # 5. 發送通知
            if click.confirm("\n是否要發送 Telegram 通知？", default=True):
                if not config.telegram_bot_token:
                    click.echo("⚠️ 未設定 TELEGRAM_BOT_TOKEN，略過通知")
                else:
                    summary = SyncSummary()
                    summary.new_bills = [bill]
                    notifier = TelegramNotifier(config)
                    asyncio.run(notifier.send_sync_summary(summary))
                    click.echo("✅ 通知已發送")
        else:
            click.echo("ℹ️ 帳單已存在，未重複新增")
            
    except Exception as e:
        click.echo(f"❌ 儲存失敗: {e}", err=True)


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
