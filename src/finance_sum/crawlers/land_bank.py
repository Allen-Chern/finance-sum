"""土地銀行爬蟲 — Playwright + ddddocr (CAPTCHA)."""

from __future__ import annotations

import io
import logging
import re
from decimal import Decimal
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from finance_sum.crawlers.base import BalanceResult, BankCrawler, CreditCardBill
from finance_sum.store import BankCredential

logger = logging.getLogger(__name__)

_LOGIN_URL = "https://mybank.landbank.com.tw/Account/Login"
_BALANCE_URL = "https://mybank.landbank.com.tw/Twd/TWD_acing_01/TwdBalance"
_MAX_CAPTCHA_RETRIES = 5


def _solve_captcha(img_bytes: bytes) -> str:
    """Try ddddocr first, fallback to Tesseract."""
    # --- ddddocr (preferred) ---
    try:
        import ddddocr  # type: ignore

        ocr = ddddocr.DdddOcr(show_ad=False)
        text = ocr.classification(img_bytes).strip().upper()
        text = re.sub(r"[^A-Z0-9]", "", text)
        logger.debug("ddddocr 辨識結果: %r", text)
        return text
    except Exception as e:
        logger.warning("ddddocr 失敗，改用 Tesseract: %s", e)

    # --- Tesseract fallback ---
    import pytesseract
    from PIL import Image as PilImage, ImageFilter, ImageOps

    img = PilImage.open(io.BytesIO(img_bytes)).convert("L")
    img = img.resize((img.width * 3, img.height * 3), PilImage.LANCZOS)
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.SHARPEN)
    config = "--psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    text = pytesseract.image_to_string(img, config=config).strip().upper()
    text = re.sub(r"[^A-Z0-9]", "", text)
    logger.debug("Tesseract OCR 辨識結果: %r", text)
    return text


class LandBankCrawler(BankCrawler):
    """Crawler for 土地銀行."""

    bank_name = "土銀"

    async def login(self, page: Page, credential: BankCredential) -> None:
        logger.info("土銀: 前往登入頁面")

        for attempt in range(1, _MAX_CAPTCHA_RETRIES + 1):
            logger.info("土銀: 登入嘗試 %d/%d", attempt, _MAX_CAPTCHA_RETRIES)
            
            # 每次登入失敗都重新載入頁面，避免驗證碼過期或狀態卡死
            await page.goto(_LOGIN_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            
            # 土銀會強制作 frameset 轉址 (DesktopDefault.htm)，實際內容在 iframe 裡面
            frame = page.frame_locator("iframe").first

            # Fill credentials
            await frame.locator("#hidNationalID").fill(credential.id_number)
            await frame.locator("#UserName").fill(credential.username)
            await frame.locator("#Password").fill(credential.password)

            # Solve CAPTCHA via screenshot of the element
            img_el = frame.locator("#_verification_img")
            await img_el.wait_for(state="visible", timeout=10000)
            await page.wait_for_timeout(500) # Give image time to load
            raw_bytes = await img_el.screenshot()
            if not raw_bytes:
                img_bytes = b""
            else:
                img_bytes = bytes(raw_bytes)
            
            # Save debug copy
            debug_path = Path("/tmp/landbank_captcha.png")
            try:
                debug_path.write_bytes(img_bytes)
                logger.debug("驗證碼已儲存至 %s", debug_path)
            except Exception:
                pass

            captcha_text = _solve_captcha(img_bytes)

            if len(captcha_text) != 4:  # Land Bank uses 4-char CAPTCHAs
                logger.warning("驗證碼辨識字元數異常 (%r)，重新整理", captcha_text)
                continue # 重新進入下一回合，會自動 goto()

            await frame.locator("#VerificationCode").fill(captcha_text)
            logger.debug("已填入驗證碼: %s", captcha_text)

            # 點擊登入
            await frame.locator("#btnLogin").click()

            # Wait for success or error. Since the main page URL doesn't change, we wait for iframe elements
            try:
                # 登入成功後，通常首頁會出現歡迎訊息或選單，或者我們檢查錯誤訊息
                error_el = frame.locator("#emptyNIDmessage, #UserNameError, #PasswordError, #VCodeError, .field-validation-error, .bootstrap-dialog-message")
                relogin_el = frame.locator("#ReLogin_div")
                
                # 等待直到出現錯誤訊息，或者 #btnLogin 消失 (代表畫面轉走了)
                # 使用較短的迴圈跟 timeout
                for _ in range(15):
                    if await relogin_el.is_visible():
                        logger.warning("土銀: 偵測到重複登入確認框，點擊「是」")
                        await frame.locator("#btnPopAdd").click()
                        await page.wait_for_timeout(2000)
                        # 點擊後看是不是就進去了
                        continue

                    if await error_el.first.is_visible():
                        err_text = await error_el.first.inner_text()
                        logger.warning("土銀: 登入錯誤訊息: %s", err_text.strip())
                        # 有錯誤訊息就跳出內層迴圈，進入下一回合 attempt
                        break
                    
                    if not await frame.locator("#btnLogin").is_visible():
                        logger.info("土銀: 登入成功 (表單已消失)")
                        return
                        
                    await page.wait_for_timeout(1000)
                else:
                    # 如果迴圈正常結束代表 15 秒了還卡在同個畫面卻沒錯誤訊息
                    logger.warning("土銀: 等待登入結果逾時，下一回合尝试重整畫面")

            except Exception as e:
                logger.debug("登入檢查發生例外: %s", e)

        raise RuntimeError(f"土銀: 登入失敗，已重試 {_MAX_CAPTCHA_RETRIES} 次驗證碼")

    async def fetch_balance(self, page: Page) -> list[BalanceResult]:
        logger.info("土銀: 準備前往餘額查詢頁面")
        await page.goto(_BALANCE_URL, wait_until="domcontentloaded")
        
        try:
            # 尋找下拉選單：有時候系統不會帶 iframe，有時候會
            select_selector = "select#AcctID"
            base_loc = page
            
            for _ in range(15):
                # 先找主畫面
                if await page.locator(select_selector).count() > 0:
                    base_loc = page
                    logger.debug("土銀: 在主畫面找到下拉選單")
                    break
                
                # 再找 iframe
                frame_loc = page.frame_locator("iframe").first
                try:
                    if await frame_loc.locator(select_selector).count() > 0:
                        base_loc = frame_loc
                        logger.debug("土銀: 在 iframe 中找到下拉選單")
                        break
                except Exception:
                    pass
                
                await page.wait_for_timeout(1000)
            else:
                logger.warning("土銀: 15秒內未在主畫面或 iframe 中找到下拉選單，預設使用 iframe")
                base_loc = page.frame_locator("iframe").first
            
            # Wait for the account dropdown (it has style="display: none;")
            await base_loc.locator(select_selector).wait_for(state="attached", timeout=15000)
            
            # Extract all option values
            options = await base_loc.locator(f"{select_selector} option").all()
            acct_values = []
            for opt in options:
                val = await opt.get_attribute("value")
                if val:  # Skip empty or '請選擇'
                    acct_values.append(val)
            
            logger.info("土銀: 找到 %d 個帳戶可查詢", len(acct_values))
            
            results: list[BalanceResult] = []
            
            for acct_val in acct_values:
                logger.debug("土銀: 查詢帳戶 %s", acct_val)
                # Select the account (force=True because it is display: none)
                await base_loc.locator(select_selector).select_option(acct_val, force=True)
                
                # Click Query
                await base_loc.locator("#btnQuery").click()
                
                # Wait for the result table to update (tr[1] typically holds basic info)
                result_table_sel = "#queryResult"
                await base_loc.locator(result_table_sel).wait_for(state="attached", timeout=10000)
                await page.wait_for_timeout(2000) # Give it a moment to render after ajax
                
                # Extract Account Number
                account_el = base_loc.locator('//*[@id="queryResult"]/tr[1]/td[2]/span[1]')
                account_str = (await account_el.inner_text()).strip() if await account_el.count() > 0 and await account_el.is_visible() else acct_val
                
                # Extract Balance
                balance_el = base_loc.locator('//*[@id="queryResult"]/tr[5]/td[2]')
                if await balance_el.count() == 0 or not await balance_el.is_visible():
                    logger.warning("土銀: 找不到帳戶 %s 的餘額元素", account_str)
                    continue
                    
                balance_text = (await balance_el.inner_text()).strip()
                amount_match = re.search(r"([\d,]+(?:\.\d+)?)", balance_text)
                if not amount_match:
                    logger.warning("土銀: 無法從 %r 解析餘額 (%s)", balance_text, account_str)
                    continue
                    
                amount_str = amount_match.group(1).replace(",", "")
                balance = Decimal(amount_str)
                
                logger.info("土銀: 找到帳戶 %s 餘額: %s", account_str, balance)
                results.append(BalanceResult(
                    bank=self.bank_name,
                    account_number=account_str,
                    balance=balance,
                ))

            if results:
                logger.info("土銀: 共成功抓取 %d 筆帳戶餘額", len(results))
                return results
            else:
                logger.warning("土銀: 無法從列表中解析出任何帳戶餘額")

        except PlaywrightTimeout:
            logger.error("土銀: 等待帳戶下拉選單逾時")
        except Exception as e:
            logger.error("土銀: 抓取餘額失敗: %s", e)

        return []

    async def fetch_credit_card_bills(self, page: Page) -> list[CreditCardBill]:
        raise NotImplementedError("土銀 fetch_credit_card_bills 尚未實作")

    async def logout(self, page: Page) -> None:
        logger.info("土銀: 嘗試登出...")
        logout_btn_xpath = "xpath=//*[@id='btnLogOff']"
        try:
            # 土銀內容常在 iframe 中
            base_loc = page
            if await page.locator("iframe").count() > 0:
                base_loc = page.frame_locator("iframe").first
                
            btn = base_loc.locator(logout_btn_xpath)
            if await btn.is_visible(timeout=5000):
                await btn.click()
                await page.wait_for_load_state("networkidle", timeout=10000)
                logger.info("土銀: 登出完成。")
            else:
                logger.debug("土銀: 找不到登出按鈕。")
        except Exception as e:
            logger.debug("土銀: 登出錯誤 - %s", e)
