"""星展銀行爬蟲 (stub)."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from playwright.async_api import Page

from finance_sum.crawlers.base import BalanceResult, BankCrawler, CreditCardBill
from finance_sum.store import BankCredential

logger = logging.getLogger(__name__)

_LOGIN_URL = "https://internet-banking.dbs.com.tw/cardplus"


class DBSCrawler(BankCrawler):
    """Crawler for 星展 bank — not yet implemented."""

    bank_name = "星展"

    async def login(self, page: Page, credential: BankCredential) -> None:
        logger.info("星展: 前往 Card+ 登入頁面")
        await page.goto(_LOGIN_URL, wait_until="networkidle")
        await page.wait_for_timeout(2000)

        logger.info("星展: 填寫登入資訊")
        # 填寫帳號 - 該欄位也是 readonly
        account_loc = page.locator('input[name="account"]')
        await account_loc.wait_for(state="visible", timeout=10000)
        await page.evaluate("(el) => el.removeAttribute('readonly')", await account_loc.element_handle())
        await account_loc.fill(credential.username)

        # 密碼欄位被設為 readonly，我們移除它後再填寫（或是透過 force=True）
        pcode_loc = page.locator('input[name="pcode"]')
        await page.evaluate("(el) => el.removeAttribute('readonly')", await pcode_loc.element_handle())
        await pcode_loc.fill(credential.password)

        # 點擊登入按鈕
        login_btn = page.locator('xpath=//*[@id="app"]/div/div/div[3]/div[2]/div/div/button[1]')
        await login_btn.click()

        # 等待轉跳或錯誤
        logger.info("星展: 點擊登入，等待畫面跳轉...")
        try:
            # 等待登入表單消失或跳轉到特定頁面
            await page.wait_for_function(
                "() => !document.querySelector('input[name=\"account\"]')",
                timeout=15000,
            )
            await page.wait_for_timeout(2000)
            logger.info("星展: 登入成功 (URL=%s)", page.url)
        except Exception as e:
            # 可以根據實際的錯誤提示 XPath 再補上擷取錯誤訊息的邏輯
            logger.error("星展: 等待登入成功逾時，可能是密碼錯誤或需要其它驗證 - %s", e)
            raise RuntimeError("星展: 登入失敗或逾時")

    async def fetch_balance(self, page: Page) -> list[BalanceResult]:
        # 這是信用卡網銀，沒有餘額
        return []

    async def fetch_credit_card_bills(self, page: Page) -> list[CreditCardBill]:
        logger.info("星展: 準備抓取信用卡帳單")

        try:
            # 點擊本期帳單
            btn_xpath = '//*[@id="main"]/div[1]/div[2]/div/div/div[2]/div/div/div[2]/div[1]/div/button'
            await page.locator(f"xpath={btn_xpath}").click()
            
            # 等待某個元素出現，以確定轉跳完成
            closing_xpath = '//*[@id="main"]/div[1]/div[2]/div[1]/div/div[2]/div[2]/div/div[1]/div[1]/div[2]/span'
            await page.locator(f"xpath={closing_xpath}").wait_for(state="attached", timeout=15000)
            await page.wait_for_timeout(2000)  # 給予一點 Ajax 渲染時間

            # 抓取文字
            closing_date_str = (await page.locator(f"xpath={closing_xpath}").inner_text()).strip()
            
            due_xpath = '//*[@id="main"]/div[1]/div[1]/div[2]/div/div[1]/div[1]/div[3]/div/div/span[2]/span'
            due_date_str = (await page.locator(f"xpath={due_xpath}").inner_text()).strip()
            
            amt_xpath = '//*[@id="main"]/div[1]/div[2]/div[1]/div/div[2]/div[1]/div[5]/div/div/div[2]/div/div[2]/span'
            amount_str = (await page.locator(f"xpath={amt_xpath}").inner_text()).strip()
            amount_str = amount_str.replace(",", "")
            
        except Exception as e:
            logger.warning("星展: 解析信用卡帳單發生錯誤或找不到該節點 - %s", e)
            return []
            
        # Parse Dates
        try:
            closing_date = datetime.strptime(closing_date_str, "%Y/%m/%d").date()
        except ValueError:
            logger.warning("星展: 無法解析結帳日格式 %s", closing_date_str)
            closing_date = datetime.now().date()
            
        try:
            due_date = datetime.strptime(due_date_str, "%Y/%m/%d").date()
        except ValueError:
            logger.warning("星展: 無法解析繳款截止日格式 %s", due_date_str)
            due_date = datetime.now().date()

        if not amount_str:
            return []

        # 這裡期別解析成 yyyy/MM (直接從結帳日來切)
        billing_period = "/".join(closing_date_str.split("/")[:2])

        bill = CreditCardBill(
            bank="星展",
            billing_period=billing_period,
            closing_date=closing_date,
            due_date=due_date,
            amount=Decimal(amount_str),
        )
        logger.info("星展: 成功抓取信用卡帳單 (結帳日: %s, 應繳金額: %s)", closing_date, amount_str)
        return [bill]

    async def logout(self, page: Page) -> None:
        logger.info("星展: 嘗試登出...")
        try:
            logout_btn = page.locator('xpath=//*[@id="top"]/div/div[1]/div[2]')
            await logout_btn.click(timeout=5000)
            await page.wait_for_timeout(2000)
            logger.info("星展: 成功登出...")
        except Exception as e:
            logger.debug("星展: 登出錯誤 - %s", e)
