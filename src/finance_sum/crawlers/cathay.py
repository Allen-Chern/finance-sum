"""國泰銀行爬蟲 (stub)."""

from __future__ import annotations

import logging
from playwright.async_api import Page

from finance_sum.crawlers.base import BalanceResult, BankCrawler, CreditCardBill
from finance_sum.store import BankCredential

logger = logging.getLogger(__name__)

_LOGIN_URL = "https://www.cathaybk.com.tw/mybank"


class CathayCrawler(BankCrawler):
    """Crawler for 國泰 bank — not yet implemented."""

    bank_name = "國泰"

    async def login(self, page: Page, credential: BankCredential) -> None:
        logger.info("國泰: 前往登入頁面")
        await page.goto(_LOGIN_URL, wait_until="networkidle")

        # 這裡我們等待一下，以免頁面還沒準備好彈出視窗
        await page.wait_for_timeout(2000)

        # 如果這個 XPath 的 button 可見就點一下 (系統通知)
        msg_btn = page.locator('xpath=//*[@id="divSystemLoginMsgList"]/div/div/div[2]/div[2]/button[2]')
        if await msg_btn.is_visible():
            logger.info("國泰: 發現登入提示通知，點擊關閉")
            await msg_btn.click()
            await page.wait_for_timeout(1000)

        logger.info("國泰: 填寫登入資訊")
        # 身分證
        id_input = page.locator('xpath=//*[@id="CustID"]')
        await id_input.wait_for(state="visible", timeout=10000)
        await id_input.fill(credential.id_number)

        # 使用者代號
        await page.locator('xpath=//*[@id="UserIdKeyin"]').fill(credential.username)

        # 密碼
        pwd_input = page.locator('xpath=//*[@id="PasswordKeyin"]')
        await pwd_input.fill(credential.password)

        # 使用者尚未提供登入按鈕的 XPath，我們先嘗試對密碼欄位按下 Enter
        logger.info("國泰: 嘗試送出 (Press Enter)")
        await pwd_input.press("Enter")
        
        # 等待一點時間
        await page.wait_for_timeout(3000)

    async def fetch_balance(self, page: Page) -> list[BalanceResult]:
        raise NotImplementedError("國泰 fetch_balance 尚未實作")

    async def fetch_credit_card_bills(self, page: Page) -> list[CreditCardBill]:
        raise NotImplementedError("國泰 fetch_credit_card_bills 尚未實作")

    async def logout(self, page: Page) -> None:
        logger.info("國泰: 嘗試登出...")
        try:
            logout_btn = page.locator('xpath=//*[@id="root"]/div/div[1]/nav/div/div/div[2]/div/button[2]')
            await logout_btn.click(timeout=5000)
            await page.wait_for_timeout(2000)
            logger.info("國泰: 成功登出")
        except Exception as e:
            logger.debug("國泰: 登出錯誤 - %s", e)
