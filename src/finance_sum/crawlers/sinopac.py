"""永豐銀行爬蟲."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import ddddocr
from playwright.async_api import Page

from finance_sum.crawlers.base import BalanceResult, BankCrawler, CreditCardBill
from finance_sum.store import BankCredential

logger = logging.getLogger(__name__)

_LOGIN_URL = "https://mma.sinopac.com/MemberPortal/Member/NextWebLogin.aspx"
_BILL_URL = "https://mma.sinopac.com/SinoCard/Account/StatementInquiry"

class SinopacCrawler(BankCrawler):
    """Crawler for 永豐 bank."""

    bank_name = "永豐"

    _ocr = None

    @classmethod
    def get_ocr(cls) -> ddddocr.DdddOcr:
        if cls._ocr is None:
            cls._ocr = ddddocr.DdddOcr(show_ad=False)
        return cls._ocr

    async def login(self, page: Page, credential: BankCredential) -> None:
        logger.info("永豐: 前往登入頁面")
        await page.goto(_LOGIN_URL, wait_until="domcontentloaded")

        for attempt in range(1, 6):
            logger.info("永豐: 登入嘗試 %d/5", attempt)

            id_xpath = '/html/body/form/div[3]/div[2]/section[2]/div[2]/div[2]/input'
            user_xpath = '/html/body/form/div[3]/div[2]/section[2]/div[2]/div[3]/input'
            pwd_xpath = '/html/body/form/div[3]/div[2]/section[2]/div[2]/div[4]/input'
            captcha_xpath = '/html/body/form/div[3]/div[2]/section[2]/div[2]/div[5]/input'
            img_xpath = '/html/body/form/div[3]/div[2]/section[2]/div[2]/div[5]/a/img'
            login_btn_xpath = '/html/body/form/div[3]/div[2]/section[2]/div[2]/div[7]/a'

            await page.locator(f"xpath={id_xpath}").wait_for(state="visible", timeout=15000)

            await page.locator(f"xpath={id_xpath}").fill(credential.id_number)
            await page.locator(f"xpath={user_xpath}").fill(credential.username)
            await page.locator(f"xpath={pwd_xpath}").fill(credential.password)

            # 處理驗證碼
            img_el = page.locator(f"xpath={img_xpath}")
            await img_el.wait_for(state="visible", timeout=10000)
            await page.wait_for_timeout(500)
            raw_bytes = await img_el.screenshot()

            img_bytes = bytes(raw_bytes) if raw_bytes else b""
            debug_path = Path("/tmp/sinopac_captcha.png")
            debug_path.write_bytes(img_bytes)

            ocr = self.get_ocr()
            captcha_text = ocr.classification(img_bytes)
            logger.debug("ddddocr 辨識結果: %r", captcha_text)

            await page.locator(f"xpath={captcha_xpath}").fill(captcha_text)

            # 點擊登入
            await page.locator(f"xpath={login_btn_xpath}").click()

            # 等待登入成功跳轉或錯誤提示
            try:
                for _ in range(15):
                    if not await page.locator(f"xpath={login_btn_xpath}").is_visible():
                        logger.info("永豐: 登入成功 (表單已消失)")
                        return
                    await page.wait_for_timeout(1000)
                logger.warning("永豐: 登入可能失敗或需重試")
            except Exception as e:
                logger.warning("永豐: 判斷登入結果時發生例外: %s", e)

            # 若沒成功，重新載入嘗試下一次
            await page.reload(wait_until="domcontentloaded")

        raise RuntimeError("永豐: 登入失敗 (超過重試次數)")

    async def fetch_balance(self, page: Page) -> list[BalanceResult]:
        # 依照使用者需求，目前只需處理信用卡帳單
        return []

    async def fetch_credit_card_bills(self, page: Page) -> list[CreditCardBill]:
        logger.info("永豐: 準備前往信用卡帳單頁面")
        await page.goto(_BILL_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        try:
            period_xpath = '/html/body/app-root/div/app-statement-inquiry/div/div/div/section/div[1]/table/tbody/tr[3]/td/div/span[1]'
            closing_xpath = '/html/body/app-root/div/app-statement-inquiry/div/div/div/section/div[1]/table/tbody/tr[3]/td/div/span[2]'
            due_xpath = '/html/body/app-root/div/app-statement-inquiry/div/div/div/section/div[1]/table/tbody/tr[3]/td/div/span[3]'
            amt_xpath = '/html/body/app-root/div/app-statement-inquiry/div/div/div/section/div[2]/table/tbody/tr[3]/td[7]'

            await page.locator(f"xpath={closing_xpath}").wait_for(state="attached", timeout=15000)

            period_raw = (await page.locator(f"xpath={period_xpath}").inner_text()).strip()
            closing_raw = (await page.locator(f"xpath={closing_xpath}").inner_text()).strip()
            due_raw = (await page.locator(f"xpath={due_xpath}").inner_text()).strip()
            amount_str = (await page.locator(f"xpath={amt_xpath}").inner_text()).strip()

            amount_str = amount_str.replace(",", "")

            # 解析期別: "2025/09信用卡帳單" -> "2025/09"
            billing_period = period_raw.replace("信用卡帳單", "").strip()

            # 解析結帳日: "結帳日：2025/09/21" -> "2025/09/21"
            if "：" in closing_raw:
                closing_date_str = closing_raw.split("：")[-1].strip()
            else:
                closing_date_str = closing_raw.replace("結帳日", "").replace(":", "").strip()

            # 解析繳款截止日: "繳款截止日：2025/10/07" -> "2025/10/07"
            if "：" in due_raw:
                due_date_str = due_raw.split("：")[-1].strip()
            else:
                due_date_str = due_raw.replace("繳款截止日", "").replace(":", "").strip()

            closing_date = datetime.strptime(closing_date_str, "%Y/%m/%d").date()
            due_date = datetime.strptime(due_date_str, "%Y/%m/%d").date()

        except Exception as e:
            logger.warning("永豐: 解析信用卡帳單發生錯誤或找不到該節點 - %s", e)
            return []

        if not amount_str:
            return []

        bill = CreditCardBill(
            bank="永豐",
            billing_period=billing_period,
            closing_date=closing_date,
            due_date=due_date,
            amount=Decimal(amount_str),
        )
        logger.info("永豐: 成功抓取信用卡帳單 (結帳日: %s, 應繳金額: %s)", closing_date, amount_str)
        return [bill]

    async def logout(self, page: Page) -> None:
        logger.info("永豐: 嘗試登出...")
        try:
            logout_btn = page.locator('xpath=//*[@id="user-logout"]')
            await logout_btn.click(timeout=5000)
            await page.wait_for_timeout(2000)
            logger.info("永豐: 成功登出...")
        except Exception as e:
            logger.debug("永豐: 登出錯誤 - %s", e)
