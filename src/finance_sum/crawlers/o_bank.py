"""王道銀行爬蟲 — Playwright + ddddocr (CAPTCHA) + Tesseract fallback."""

from __future__ import annotations

import base64
import io
import logging
import re
from decimal import Decimal
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from finance_sum.crawlers.base import BalanceResult, BankCrawler, CreditCardBill
from finance_sum.store import BankCredential

logger = logging.getLogger(__name__)

_LOGIN_URL = (
    "https://www.o-bank.com/ebank/apps/services/www/ibmb/"
    "desktopbrowser/default/index.html"
)
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
    config = "--psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    text = pytesseract.image_to_string(img, config=config).strip().upper()
    text = re.sub(r"[^A-Z]", "", text)
    logger.debug("Tesseract OCR 辨識結果: %r", text)
    return text


async def _get_captcha_image_bytes(page: Page) -> bytes:
    """Return raw bytes of the CAPTCHA image (from Base64 data URI)."""
    src = await page.get_attribute("img[src*='data:image']", "src")
    if not src or "base64," not in src:
        raise RuntimeError("找不到驗證碼圖片")
    img_bytes = base64.b64decode(src.split("base64,", 1)[1])

    # Save debug copy
    debug_path = Path("/tmp/obank_captcha.png")
    try:
        from PIL import Image as PilImage
        PilImage.open(io.BytesIO(img_bytes)).save(debug_path)
        logger.debug("驗證碼已儲存至 %s", debug_path)
    except Exception:
        pass

    return img_bytes


async def _close_popup(page: Page) -> None:
    """Close announcement popup or cookie banner if present."""
    for sel in [
        "a.btn_close", "button.btn_close",
        ".modal .close", "a[class*='close']",
        "[class*='popup'] [class*='close']",
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await el.click()
                logger.debug("關閉彈窗: %s", sel)
                await page.wait_for_timeout(500)
                break
        except PlaywrightTimeout:
            continue


async def _detect_login_error(page: Page) -> str | None:
    """Return error message text if a login error is visible, else None."""
    error_sels = [
        ".error_msg", ".alert-danger", "[class*='error']",
        "[class*='alert']", ".msg", ".tip",
    ]
    for sel in error_sels:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1500):
                text = (await el.inner_text()).strip()
                if text:
                    # 過濾掉非錯誤訊息 (如行銷文字)
                    if "首選王道" in text or "證券交割" in text or "🤩" in text:
                        continue
                    return text
        except PlaywrightTimeout:
            continue
    return None


async def _wait_for_login_success(page: Page, timeout: int = 10000) -> bool:
    """Return True if we detect a successful login indicator."""
    # Any of these indicate we've passed the login page
    success_indicators = [
        # Login form fields should disappear
        lambda: page.wait_for_function(
            "!document.querySelector('#no') && !document.querySelector('#uno')",
            timeout=timeout,
        ),
        # Or a post-login nav/menu element appears
        lambda: page.wait_for_selector(
            "[class*='main-menu'], [class*='mainMenu'], nav.menu, "
            "[class*='account'], [class*='dashboard'], [class*='home']",
            timeout=timeout,
        ),
    ]
    for check in success_indicators:
        try:
            await check()
            return True
        except (PlaywrightTimeout, Exception):
            continue
    return False


class OBankCrawler(BankCrawler):
    """Crawler for 王道銀行 — Playwright + ddddocr CAPTCHA solving."""

    bank_name = "王道"

    async def login(self, page: Page, credential: BankCredential) -> None:
        logger.info("王道: 前往登入頁面")
        await page.goto(_LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        await _close_popup(page)
        await page.wait_for_selector("#no", timeout=15000)

        for attempt in range(1, _MAX_CAPTCHA_RETRIES + 1):
            logger.info("王道: 登入嘗試 %d/%d", attempt, _MAX_CAPTCHA_RETRIES)

            # Fill credentials
            await page.fill("#no", credential.id_number)
            await page.fill("#uno", credential.username)
            await page.fill("#sec", credential.password)

            # Solve CAPTCHA
            img_bytes = await _get_captcha_image_bytes(page)
            captcha_text = _solve_captcha(img_bytes)

            if len(captcha_text) not in (4, 5):  # O-Bank uses 4-char CAPTCHAs
                logger.warning("驗證碼辨識字元數異常 (%r)，重新整理", captcha_text)
                await _refresh_captcha(page)
                continue

            # Trim to 4 chars if OCR added an extra
            captcha_text = captcha_text[:4] if len(captcha_text) > 4 else captcha_text
            await page.fill("#captcha", captcha_text)
            logger.debug("已填入驗證碼: %s", captcha_text)

            # Submit
            await page.click("a.submit_btn")
            await page.wait_for_timeout(5000)

            # --- Check for concurrent login modal ---
            # "這個網路銀行帳戶可能是上次未正常登出。您要繼續使用並從其他裝置登出嗎?"
            confirm_modal = page.locator("#confirmModal")
            if await confirm_modal.is_visible():
                logger.warning("王道: 偵測到重複登入確認框，點擊繼續")
                try:
                    # Click confirm button inside the modal
                    await confirm_modal.locator("#confirmModalBtn1").click()
                    await page.wait_for_timeout(2000)
                except PlaywrightTimeout:
                    logger.debug("找不到確認對話框的確定按鈕")

            # Check error first
            error = await _detect_login_error(page)
            if error:
                logger.warning("王道登入錯誤: %s", error)
                if "驗證碼" in error or "captcha" in error.lower():
                    await _refresh_captcha(page)
                    continue
                raise RuntimeError(f"王道登入失敗: {error}")

            # Check success
            if await _wait_for_login_success(page):
                logger.info("王道: 登入成功 (URL=%s)", page.url)
                return

            # Neither error nor success — possibly still on login page after wrong captcha
            # Check if the form is still there
            if await page.is_visible("#no"):
                logger.warning("王道: 登入表單仍在，可能驗證碼錯誤，重試")
                await _refresh_captcha(page)
                # Clear fields for retry
                await page.fill("#no", "")
                await page.fill("#uno", "")
                await page.fill("#sec", "")
                continue
            else:
                # Form gone but can't confirm success — assume logged in
                logger.info("王道: 登入表單消失，假設登入成功 (URL=%s)", page.url)
                return

        raise RuntimeError(
            f"王道: 驗證碼辨識連續失敗 {_MAX_CAPTCHA_RETRIES} 次，請檢查 /tmp/obank_captcha.png"
        )

    async def fetch_balance(self, page: Page) -> list[BalanceResult]:
        logger.info("王道: 準備點擊總存款金額以展開帳戶列表")

        try:
            # 1. 點擊總存款，展開底下的帳戶清單
            total_xpath = "xpath=//*[@id='totalDepositAmount']"
            await page.wait_for_selector(total_xpath, timeout=15000)
            await page.click(total_xpath)
            
            # 2. 等待展開的帳戶項目（li.cdDetail）出現
            item_selector = "li.cdDetail"
            await page.wait_for_selector(item_selector, timeout=10000)
            
            items = await page.locator(item_selector).all()
            results: list[BalanceResult] = []
            
            for item in items:
                # 抓取帳號
                account_el = item.locator(".txt_account")
                if not await account_el.is_visible():
                    continue
                account_full = (await account_el.inner_text()).strip()
                account_num = account_full if account_full else "MAIN"
                
                # 抓取餘額
                money_el = item.locator(".card_list_money")
                if not await money_el.is_visible():
                    continue
                money_text = (await money_el.inner_text()).strip()
                
                # 從文字中提取數字
                amount_match = re.search(r"([\d,]+(?:\.\d+)?)", money_text)
                if not amount_match:
                    continue
                    
                amount_str = amount_match.group(1).replace(",", "")
                balance = Decimal(amount_str)
                
                logger.info("王道: 找到帳戶 ...%s 餘額: %s", account_num, balance)
                results.append(BalanceResult(
                    bank=self.bank_name,
                    account_number=account_num,
                    balance=balance,
                ))

            if results:
                logger.info("王道: 共成功抓取 %d 筆帳戶餘額", len(results))
                return results
            else:
                logger.warning("王道: 無法從列表中解析出任何帳戶餘額")

        except PlaywrightTimeout:
            logger.error("王道: 等待總存款元素或帳戶列表逾時")
        except Exception as e:
            logger.error("王道: 抓取餘額列表失敗: %s", e)

        return []

    async def fetch_credit_card_bills(self, page: Page) -> list[CreditCardBill]:
        raise NotImplementedError("王道 fetch_credit_card_bills 尚未實作")

    async def logout(self, page: Page) -> None:
        logger.info("王道: 嘗試登出...")
        logout_btn_xpath = "xpath=//*[@id='logout_btn']"
        try:
            if await page.locator(logout_btn_xpath).is_visible(timeout=5000):
                await page.click(logout_btn_xpath)
                # 等待跳轉穩定
                await page.wait_for_load_state("networkidle", timeout=10000)
                logger.info("王道: 登出完成。")
            else:
                logger.debug("王道: 找不到登出按鈕，可能已登出。")
        except Exception as e:
            logger.debug("王道: 登出過程中發生錯誤 (可能已自動登出) - %s", e)


async def _refresh_captcha(page: Page) -> None:
    """Click the CAPTCHA refresh button and wait for a new image."""
    try:
        await page.click("a.btn_refresh")
        await page.wait_for_timeout(1000)
    except Exception as e:
        logger.debug("刷新驗證碼失敗: %s", e)
