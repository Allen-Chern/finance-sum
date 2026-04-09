"""聯邦銀行爬蟲."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import ddddocr
from playwright.async_api import Page, TimeoutError

from finance_sum.crawlers.base import BalanceResult, BankCrawler, CreditCardBill
from finance_sum.store import BankCredential

logger = logging.getLogger(__name__)

_LOGIN_URL = "https://www.ubot.com.tw/"

class UnionBankCrawler(BankCrawler):
    """Crawler for 聯邦 bank."""

    bank_name = "聯邦"
    _ocr = None
    
    @classmethod
    def get_ocr(cls) -> ddddocr.DdddOcr:
        if cls._ocr is None:
            cls._ocr = ddddocr.DdddOcr(show_ad=False)
        return cls._ocr

    async def login(self, page: Page, credential: BankCredential) -> None:
        logger.info("聯邦: 前往登入頁面")
        await page.goto(_LOGIN_URL, wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # 點擊展開登入區塊
        logger.info("聯邦: 點擊展開登入區塊")
        login_area_btn_xpath = "/html/body/div/div/nav[2]/div/div[2]/div[3]/button"
        await page.locator(f"xpath={login_area_btn_xpath}").click()
        await page.wait_for_timeout(1000)

        for attempt in range(1, 6):
            logger.info("聯邦: 登入嘗試 %d/5", attempt)
            
            # 填寫資料
            id_input = page.locator("xpath=/html/body/div/div/nav[2]/div/div[2]/div[2]/div/section/form/div[1]/input")
            user_input = page.locator("xpath=/html/body/div/div/nav[2]/div/div[2]/div[2]/div/section/form/div[2]/div/input")
            pwd_input = page.locator("xpath=/html/body/div/div/nav[2]/div/div[2]/div[2]/div/section/form/div[3]/div/div/input")
            
            await id_input.fill(credential.id_number)
            await user_input.fill(credential.username)
            await pwd_input.fill(credential.password)

            # 抓取驗證碼
            captcha_img = page.locator("xpath=/html/body/div/div/nav[2]/div/div[2]/div[2]/div/section/form/div[5]/div/img")
            await captcha_img.wait_for(state="visible", timeout=10000)
            await page.wait_for_timeout(500)
            img_bytes = await captcha_img.screenshot()
            
            # 儲存驗證碼截圖以便除錯
            debug_path = Path("/tmp/union_bank_captcha.png")
            debug_path.write_bytes(img_bytes)
            
            ocr_client = self.get_ocr()
            captcha_text = ocr_client.classification(img_bytes)
            logger.debug("聯邦: ddddocr 辨識結果: %r", captcha_text)

            captcha_input = page.locator("xpath=/html/body/div/div/nav[2]/div/div[2]/div[2]/div/section/form/div[5]/input")
            await captcha_input.fill(captcha_text)

            # 點擊登入
            login_btn = page.locator("xpath=/html/body/div/div/nav[2]/div/div[2]/div[2]/div/section/form/div[6]/button[2]")
            await login_btn.click()
            
            # 等待跳轉或彈窗
            try:
                # 若成功會進入主選單，或者跳出提示 modal
                await page.wait_for_timeout(3000)
                
                # 處理密碼過期或其他確認彈窗
                modal_confirm_xpath = "/html/body/div[3]/div/div[1]/div/div/footer/div/div[1]/button"
                modal_confirm = page.locator(f"xpath={modal_confirm_xpath}")
                if await modal_confirm.is_visible(timeout=5000):
                    logger.info("聯邦: 發現系統提示 Modal，點擊確認...")
                    await modal_confirm.click()
                    await page.wait_for_timeout(2000)
                
                # 檢查是否已成功登入 (網址改變或有特定選單)
                logger.info("聯邦: 登入成功 (URL=%s)", page.url)
                return
            except TimeoutError:
                # 判斷是否為密碼/驗證碼錯誤等 (依實際情況若有 error message element 可以擷取)
                logger.warning("聯邦: 登入逾時，可能是驗證碼錯誤，重試中...")
                # 點選圖片重新產生驗證碼
                if await captcha_img.is_visible():
                    await captcha_img.click()
                    await page.wait_for_timeout(1000)
                continue
                
        raise RuntimeError("聯邦: 登入失敗 (超過重試次數)")

    async def fetch_balance(self, page: Page) -> list[BalanceResult]:
        logger.info("聯邦: fetch_balance 尚未實作")
        return []

    async def fetch_credit_card_bills(self, page: Page) -> list[CreditCardBill]:
        logger.info("聯邦: 準備抓取信用卡帳單")

        try:
            # 點擊信用卡主選單
            menu1_xpath = "/html/body/div[2]/div[1]/div[1]/div[1]/ul/li[6]/a"
            await page.locator(f"xpath={menu1_xpath}").click()
            await page.wait_for_timeout(1000)
            
            # 點擊信用卡帳單子選單
            menu2_xpath = "/html/body/div[2]/div[1]/div[1]/div[1]/ul/li[6]/div/ul[2]/li[1]/a"
            await page.locator(f"xpath={menu2_xpath}").click()
            await page.wait_for_timeout(5000)
            
            # 點擊本期帳單或是特定 tab // 這邊是使用者提供的 section[1]/button[1]
            tab_btn_xpath = "/html/body/div[2]/div[1]/div[2]/div/div/div[2]/section[1]/button[1]"
            await page.locator(f"xpath={tab_btn_xpath}").wait_for(state="visible", timeout=15000)
            await page.locator(f"xpath={tab_btn_xpath}").click()
            await page.wait_for_timeout(3000)
            
            # 擷取資料
            closing_date_xpath = "/html/body/div[2]/div[1]/div[2]/div/div/div[2]/div[3]/div[1]/ul/li[2]"
            closing_date_str = (await page.locator(f"xpath={closing_date_xpath}").inner_text()).strip()
            
            due_date_xpath = "/html/body/div[2]/div[1]/div[2]/div/div/div[2]/div[3]/div[2]/ul/li[2]"
            due_date_str = (await page.locator(f"xpath={due_date_xpath}").inner_text()).strip()
            
            amount_xpath = "/html/body/div[2]/div[1]/div[2]/div/div/div[2]/div[3]/div[3]/ul/li[2]"
            amount_str = (await page.locator(f"xpath={amount_xpath}").inner_text()).strip()

        except Exception as e:
            logger.warning("聯邦: 解析信用卡帳單發生錯誤或找不到該節點 - %s", e)
            return []

        # Parse Dates
        try:
            closing_date = datetime.strptime(closing_date_str, "%Y/%m/%d").date()
        except ValueError:
            logger.warning("聯邦: 無法解析結帳日格式 %s", closing_date_str)
            closing_date = datetime.now().date()
            
        due_date = closing_date
        if "無需繳款" not in due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, "%Y/%m/%d").date()
            except ValueError:
                logger.warning("聯邦: 無法解析繳款截止日格式 %s", due_date_str)
        
        # 清理 amount_str
        amount_str = amount_str.replace(",", "").replace("$", "").strip()
        if not amount_str:
            return []

        # 用結帳日推算帳單期別
        billing_period = "/".join(closing_date_str.split("/")[:2])

        bill = CreditCardBill(
            bank="聯邦",
            billing_period=billing_period,
            closing_date=closing_date,
            due_date=due_date,
            amount=Decimal(amount_str),
        )
        logger.info("聯邦: 成功抓取信用卡帳單 (結帳日: %s, 應繳金額: %s)", closing_date, amount_str)
        return [bill]

    async def logout(self, page: Page) -> None:
        logger.info("聯邦: 嘗試登出...")
        try:
            # TODO: Add logout logic when available, for now just close
            logger.info("聯邦: 尚未實作登出邏輯")
        except Exception as e:
            logger.debug("聯邦: 登出過程中發生例外 - %s", e)
