"""Taishin Bank crawler."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import ddddocr
from playwright.async_api import Page, FrameLocator

from finance_sum.crawlers.base import BalanceResult, BankCrawler, CreditCardBill

if TYPE_CHECKING:
    from finance_sum.crawlers.base import SyncItem
    from finance_sum.store import BankCredential

logger = logging.getLogger(__name__)

_LOGIN_URL = "https://my.taishinbank.com.tw/TIBNetBank/"


class TaishinCrawler(BankCrawler):
    """Crawler for Taishin Bank (台新銀行)."""
    
    bank_name = "台新"
    
    _ocr = None
    
    @classmethod
    def get_ocr(cls) -> ddddocr.DdddOcr:
        if cls._ocr is None:
            cls._ocr = ddddocr.DdddOcr(show_ad=False)
        return cls._ocr

    async def _clear_popups(self, base_loc: Page | FrameLocator, timeout: int = 1500) -> bool:
        """Check and clear any popups that might block interaction."""
        found = False
        try:
            # 1. Target the specific announcement popup from the user request
            # Selector: ._popup.announcement.active
            # Button: ._btn._btn-primary with text "我知道了"
            announcement = base_loc.locator("._popup.announcement.active")
            if await announcement.count() > 0:
                # Use a more flexible selector for the button inside the announcement
                btn = announcement.locator("button", has_text="我知道了").first
                if await btn.is_visible(timeout=timeout):
                    logger.info("台新: 發現公告彈窗，點擊「我知道了」")
                    await btn.click(force=True)
                    found = True
            
            # 2. Existing popup logic (e.g. "Previous session not closed")
            popup_close = base_loc.locator(".js-popup.active .js-popup-close, #reloginbtn").first
            if await popup_close.is_visible(timeout=timeout):
                logger.info("台新: 發現系統提示或重複登入彈窗，嘗試關閉...")
                await popup_close.click(force=True)
                found = True
                
            # 3. Generic modals (often seen during/after login)
            generic_close = base_loc.locator(".swal2-confirm, .close, .btn-primary, button.confirm").first
            if await generic_close.is_visible(timeout=timeout):
                # Don't click if it's part of the login form itself (unlikely with these classes)
                logger.info("台新: 發現通用彈窗按鈕，嘗試關閉...")
                await generic_close.click(force=True)
                found = True
        except Exception as e:
            logger.debug("台新: 檢查彈窗時發生例外: %s", e)
            
        return found

    async def login(self, page: Page, credential: BankCredential) -> None:
        logger.info("台新: 前往登入頁面")
        await page.goto(_LOGIN_URL, wait_until="domcontentloaded")
        
        for attempt in range(1, 6):
            logger.info("台新: 登入嘗試 %d/5", attempt)
            
            # 尋找 base_loc
            base_loc = page
            for _ in range(15):
                if await page.locator("#loginBtn").count() > 0:
                    base_loc = page
                    break
                try:
                    if await page.frame_locator("iframe").first.locator("#loginBtn").count() > 0:
                        base_loc = page.frame_locator("iframe").first
                        break
                except Exception:
                    pass
                await page.wait_for_timeout(1000)
            else:
                base_loc = page.frame_locator("iframe").first

            # 處理可能出現的彈出視窗（例如公告、未正常登出提示等）
            if await self._clear_popups(base_loc):
                await page.wait_for_timeout(2000)
                # 關閉後重新檢查 base_loc
                continue

            # Wait for the login button
            await base_loc.locator("#loginBtn").wait_for(state="visible", timeout=15000)
            
            # Taishin often has a virtual keyboard pop-up when typing in password fields.
            await base_loc.get_by_placeholder("身分證字號").fill(credential.id_number)
            await base_loc.get_by_placeholder("使用者代號").fill(credential.username)
            await base_loc.get_by_placeholder("使用者密碼").fill(credential.password)
            
            # Solve CAPTCHA
            img_el = base_loc.locator("img._field_item__verify-code")
            await img_el.wait_for(state="visible", timeout=10000)
            await page.wait_for_timeout(500)  # Give image time to load fully
            raw_bytes = await img_el.screenshot()
            
            if not raw_bytes:
                img_bytes = b""
            else:
                img_bytes = bytes(raw_bytes)
            
            # Save debug copy
            debug_path = Path("/tmp/taishin_captcha.png")
            debug_path.write_bytes(img_bytes)
            logger.debug("驗證碼已儲存至 %s", debug_path)
            
            ocr = self.get_ocr()
            captcha_text = ocr.classification(img_bytes)
            logger.debug("ddddocr 辨識結果: %r", captcha_text)
            
            await base_loc.get_by_placeholder("驗證碼").fill(captcha_text)
            logger.debug("已填入驗證碼: %s", captcha_text)
            
            # Click Login
            await base_loc.locator("#loginBtn").click()
            
            # Check for success or error
            try:
                for _ in range(15):
                    # Check if login button is gone
                    if not await base_loc.locator("#loginBtn").is_visible():
                        logger.info("台新: 登入成功 (表單已消失)")
                        return
                    
                    if await self._clear_popups(base_loc, timeout=2000):
                        await page.wait_for_timeout(1000)
                        # 觸發了代表有彈出視窗，跳出 15 秒檢查，直接 retry 登入或重新檢查狀態
                        break
                    else:
                        # 這是 for-else, 沒觸發 break 的話就繼續等
                        await page.wait_for_timeout(1000)
                        continue
                    
                    # 觸發了 break，代表有彈出視窗並關閉了，跳出 15 秒檢查，直接 retry
                    break
                else:
                    logger.warning("台新: 等待登入結果逾時，重試")
            except Exception as e:
                logger.warning("台新: 判斷登入結果時發生例外: %s", e)
                
        raise RuntimeError("台新: 登入失敗 (超過重試次數)")

    async def fetch_balance(self, page: Page) -> list[BalanceResult]:
        logger.info("台新: 開始解析餘額頁面")
        
        table_sel = "#savingAccountTable"
        base_loc = page
        
        # 動態尋找包含 #savingAccountTable 的 frame 或 page
        for _ in range(20):
            if await page.locator(table_sel).count() > 0:
                base_loc = page
                break
            try:
                if await page.frame_locator("iframe").first.locator(table_sel).count() > 0:
                    base_loc = page.frame_locator("iframe").first
                    break
            except Exception:
                pass
            await page.wait_for_timeout(1000)
        else:
            base_loc = page.frame_locator("iframe").first
            
        try:
            await base_loc.locator(table_sel).wait_for(state="attached", timeout=20000)
            await page.wait_for_timeout(1000)  # 等待資料渲染
        except Exception:
            # 嘗試清除可能遮擋的公告
            await self._clear_popups(base_loc)
            try:
                await base_loc.locator(table_sel).wait_for(state="attached", timeout=10000)
            except Exception:
                raise RuntimeError("台新: 找不到帳戶餘額表格 (#savingAccountTable)")
            
        rows = await base_loc.locator(f"{table_sel} tbody tr._table_tbody__row").all()
        logger.info("台新: 找到 %d 個帳戶列", len(rows))
        
        results: list[BalanceResult] = []
        for row in rows:
            # 取得帳號
            acct_el = row.locator('td[data-title="帳號"] span.accountNum')
            if await acct_el.count() == 0:
                continue
            account_str = (await acct_el.inner_text()).strip()
            
            # 取得餘額
            # <div data-amount="262,709">262,709</div>
            bal_el = row.locator('td[data-title="帳戶餘額"] div[data-amount]')
            if await bal_el.count() == 0:
                continue
            
            bal_str = await bal_el.get_attribute("data-amount")
            if not bal_str:
                bal_str = await bal_el.inner_text()
                
            bal_str = bal_str.replace(",", "").strip()
            
            if not bal_str:
                continue
                
            results.append(
                BalanceResult(
                    bank="台新",
                    account_number=account_str,
                    balance=Decimal(bal_str),
                )
            )
            logger.debug("台新: 解析到帳號 %s, 餘額 %s", account_str, bal_str)
            
        return results

    async def fetch_credit_card_bills(self, page: Page) -> list[CreditCardBill]:
        logger.info("台新: 準備前往信用卡帳單頁面")
        
        # 動態判斷 base_loc
        base_loc = page
        if await page.locator("iframe").count() > 0:
            base_loc = page.frame_locator("iframe").first
        
        # Hover 首先在外層選單
        menu_hover_xpath = '//*[@id="app"]/div/div[1]/div/nav/div/div[2]/div[1]/div[1]/div[3]/ul/li[6]'
        # 點擊 User 提供的 Menu 路徑 (force=True 略過 hover 隱藏狀態)
        menu_link_xpath = '//*[@id="app"]/div/div[1]/div/nav/div/div[2]/div[1]/div[1]/div[3]/ul/li[6]/ul/li[3]/ul/li[1]/a'
        
        try:
            # 處理可能遺留的系統公告
            await self._clear_popups(base_loc)
            await page.wait_for_timeout(1000)

            await base_loc.locator(f"xpath={menu_hover_xpath}").hover()
            await page.wait_for_timeout(500)
            await base_loc.locator(f"xpath={menu_link_xpath}").click(force=True)
            
            # 等待畫面切換並載入 result 區塊
            title_xpath = '//*[@id="result"]/div[1]/div[1]/div[1]/h3'
            
            # Wait inside an explicit loop for frame DOM transition
            for _ in range(20):
                if await page.locator(f"xpath={title_xpath}").count() > 0:
                    base_loc = page
                    break
                try:
                    if await page.frame_locator("iframe").first.locator(f"xpath={title_xpath}").count() > 0:
                        base_loc = page.frame_locator("iframe").first
                        break
                except Exception:
                    pass
                await page.wait_for_timeout(1000)

            await base_loc.locator(f"xpath={title_xpath}").wait_for(state="attached", timeout=20000)
            await page.wait_for_timeout(2000)  # 給予一點 Ajax 渲染時間
            
            # Title: " 2026/03 信用卡明細 "
            title_text = (await base_loc.locator(f"xpath={title_xpath}").inner_text()).strip()
            
            # 結帳日: 2026/03/17
            closing_date_str = (await base_loc.locator('xpath=//*[@id="result"]/div[3]/div[1]/div/div/div/div[3]/div/p').inner_text()).strip()
            
            # 繳款截止日: 2026/03/17
            due_date_str = (await base_loc.locator('xpath=//*[@id="result"]/div[3]/div[1]/div/div/div/div[4]/div/p').inner_text()).strip()
            
            # 帳單金額: 70,075
            amount_str = (await base_loc.locator('xpath=//*[@id="result"]/div[3]/div[1]/div/div/div/div[5]/div/p/span').inner_text()).strip()
            amount_str = amount_str.replace(",", "")
            
        except Exception as e:
            logger.warning("台新: 解析信用卡帳單發生錯誤或找不到該節點 - %s", e)
            return []
            
        # Parse Dates
        try:
            closing_date = datetime.strptime(closing_date_str, "%Y/%m/%d").date()
        except ValueError:
            logger.warning("台新: 無法解析結帳日格式 %s", closing_date_str)
            closing_date = datetime.now().date()
            
        try:
            due_date = datetime.strptime(due_date_str, "%Y/%m/%d").date()
        except ValueError:
            logger.warning("台新: 無法解析繳款截止日格式 %s", due_date_str)
            due_date = datetime.now().date()

        if not amount_str:
            return []

        # Parse year/month from title or use "總帳單"
        # e.g. "2026/03 信用卡明細" -> split()[0] -> "2026/03"
        billing_period = title_text.split(" ")[0] if title_text else "總帳單"

        bill = CreditCardBill(
            bank="台新",
            billing_period=billing_period,
            closing_date=closing_date,
            due_date=due_date,
            amount=Decimal(amount_str),
        )
        logger.info("台新: 成功抓取信用卡帳單 (結帳日: %s, 應繳金額: %s)", closing_date, amount_str)
        return [bill]

    async def logout(self, page: Page) -> None:
        logger.info("台新: 嘗試登出...")
        base_loc = page
        if await page.locator("iframe").count() > 0:
            base_loc = page.frame_locator("iframe").first
            
        logout_btn_xpath = '//*[@id="app"]/div/div[1]/div/nav/div/div[2]/div[2]/button'
        try:
            await base_loc.locator(f"xpath={logout_btn_xpath}").click(timeout=5000)
            await page.wait_for_timeout(2000)
            logger.info("台新: 成功送出登出請求。")
        except Exception as e:
            logger.debug("台新: 登出失敗或已登出 - %s", e)
