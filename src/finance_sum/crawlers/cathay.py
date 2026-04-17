"""國泰銀行爬蟲."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from playwright.async_api import Page

from finance_sum.crawlers.base import BalanceResult, BankCrawler, CreditCardBill, SyncItem
from finance_sum.store import BankCredential

logger = logging.getLogger(__name__)

_LOGIN_URL = "https://www.cathaybk.com.tw/mybank"
_BALANCE_URL = "https://www.cathaybk.com.tw/OnlineBanking/AcctInq/B0101_DepInq"
_BILL_URL = "https://www.cathaybk.com.tw/OnlineBanking/CQuery/C0102_BillInq"
_BALANCE_TABLE_XPATH = '//*[@id="root"]/div/div[3]/div/div/div[3]/div[3]/div/div[2]/div/div[2]/div/div/table'


class CathayCrawler(BankCrawler):
    """Crawler for 國泰世華銀行."""

    bank_name = "國泰"
    sync_items = [SyncItem.BALANCE, SyncItem.CREDIT_CARD]

    async def login(self, page: Page, credential: BankCredential) -> None:
        logger.info("國泰: 前往登入頁面")
        await page.goto(_LOGIN_URL, wait_until="networkidle")

        await page.wait_for_timeout(2000)

        # 系統通知彈窗
        _msg_xpath = '//*[@id="divSystemLoginMsgList"]/div/div/div[2]/div[2]/button[2]'
        msg_btn = page.locator(f"xpath={_msg_xpath}")
        if await msg_btn.is_visible():
            logger.info("國泰: 發現登入提示通知，點擊關閉")
            await msg_btn.click()
            await page.wait_for_timeout(1000)

        logger.info("國泰: 填寫登入資訊")
        id_input = page.locator('xpath=//*[@id="CustID"]')
        await id_input.wait_for(state="visible", timeout=10000)
        await id_input.fill(credential.id_number)

        await page.locator('xpath=//*[@id="UserIdKeyin"]').fill(credential.username)
        await page.locator('xpath=//*[@id="PasswordKeyin"]').fill(credential.password)

        logger.info("國泰: 點擊登入按鈕")
        _login_xpath = '/html/body/div[2]/main/form/div/div/div[2]/div/div[1]/div[2]/button'
        login_btn = page.locator(f"xpath={_login_xpath}")
        await login_btn.click()

        # 等待跳轉離開登入頁（URL 不再包含 /mybank 首頁或登入表單消失）
        try:
            await page.wait_for_function(
                "() => !document.querySelector('#CustID')",
                timeout=20000,
            )
            logger.info("國泰: 登入成功 (登入表單已消失，URL=%s)", page.url)
        except Exception as e:
            logger.warning("國泰: 等待登入跳轉逾時 - %s", e)

        await page.wait_for_load_state("networkidle", timeout=15000)
        logger.info("國泰: 登入後 URL=%s, title=%s", page.url, await page.title())

    async def handle_2fa(self, page: Page) -> None:
        """若出現兩步驟驗證頁面，等使用者手動完成（最多 2 分鐘）。"""
        # 偵測是否停在 2FA / 驗證頁面（含「兩步驟」或「驗證」關鍵字）
        try:
            is_2fa = await page.evaluate(
                "() => document.body.innerText.includes('兩步驟') "
                "|| document.body.innerText.includes('身分驗證') "
                "|| document.body.innerText.includes('OTP')"
            )
        except Exception:
            is_2fa = False

        if not is_2fa:
            return

        print("\n⚠️  國泰: 偵測到兩步驟驗證，請在瀏覽器視窗中完成驗證（最多等待 2 分鐘）...")
        logger.info("國泰: 等待使用者完成兩步驟驗證，最多 120 秒")

        # 每 3 秒檢查一次，直到 2FA 頁面消失（nav 選單出現代表已到主頁）
        for elapsed in range(0, 120, 3):
            await page.wait_for_timeout(3000)
            try:
                done = await page.evaluate(
                    "() => !document.body.innerText.includes('兩步驟') "
                    "&& !document.body.innerText.includes('身分驗證') "
                    "&& !document.body.innerText.includes('OTP')"
                )
                if done:
                    logger.info("國泰: 兩步驟驗證完成（約 %d 秒後）", elapsed + 3)
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    return
            except Exception:
                pass

        logger.warning("國泰: 兩步驟驗證等待逾時（120 秒）")

    async def fetch_balance(self, page: Page) -> list[BalanceResult]:
        import re

        logger.info("國泰: 前往存款查詢頁面")
        await page.goto(_BALANCE_URL, wait_until="networkidle")
        await page.wait_for_timeout(2000)

        try:
            table = page.locator(f"xpath={_BALANCE_TABLE_XPATH}")
            await table.wait_for(state="visible", timeout=15000)

            rows = table.locator("tbody tr")
            count = await rows.count()
            logger.info("國泰: 找到 %d 筆帳戶資料", count)

            results: list[BalanceResult] = []
            for i in range(count):
                row = rows.nth(i)
                cells = row.locator("td")

                account_number_raw = await cells.nth(0).locator("button").inner_text(timeout=5000)
                account_number = account_number_raw.strip()

                balance_raw = await cells.nth(1).locator("p").inner_text(timeout=5000)
                balance_str = re.sub(r"[^\d]", "", balance_raw)
                if not balance_str:
                    logger.warning("國泰: 第 %d 列無法解析帳戶餘額: %r", i, balance_raw)
                    continue

                logger.info("國泰: 帳戶 %s 餘額 = %s", account_number, balance_str)
                results.append(BalanceResult(
                    bank="國泰",
                    account_number=account_number,
                    balance=Decimal(balance_str),
                ))
        except Exception as e:
            logger.warning("國泰: 解析餘額失敗 - %s", e)
            return []

        return results

    async def fetch_credit_card_bills(self, page: Page) -> list[CreditCardBill]:
        import re

        logger.info("國泰: 前往信用卡帳單總覽頁")
        await page.goto(_BILL_URL, wait_until="networkidle")
        await page.wait_for_timeout(2000)

        try:
            # 等待「最近一期帳單」區塊載入
            await page.get_by_text("帳單查詢與繳款").first.wait_for(state="visible", timeout=15000)
            await page.wait_for_timeout(500)

            # --- 1. 期別：從「2026年4月新臺幣應繳總金額」解析出年月 ---
            _period_xpath = '//*[@id="root"]/div/div[3]/div/div/div[3]/div[1]/div[2]/div[2]/div[1]/div[1]/div[1]/p'
            period_raw = await page.locator(f"xpath={_period_xpath}").inner_text(timeout=5000)
            logger.info("國泰: 期別原始文字 = %r", period_raw)
            m_period = re.search(r"(\d{4})年(\d{1,2})月", period_raw)
            if m_period:
                billing_period = f"{m_period.group(1)}/{int(m_period.group(2)):02d}"
            else:
                billing_period = period_raw.strip()
                logger.warning("國泰: 無法從期別文字解析年月: %r", period_raw)
            logger.info("國泰: 帳單期別 = %s", billing_period)

            # --- 2. 帳單結帳日 ---
            _closing_xpath = '//*[@id="root"]/div/div[3]/div/div/div[3]/div[1]/div[2]/div[2]/div[1]/div[2]/div[4]/div[2]'
            closing_date_raw = await page.locator(f"xpath={_closing_xpath}").inner_text(timeout=5000)
            closing_date_raw = closing_date_raw.strip()
            logger.info("國泰: 帳單結帳日原始 = %r", closing_date_raw)

            # --- 3. 繳費截止日：從「繳款截止日 2026 / 05 / 01」解析 ---
            _due_xpath = '//*[@id="root"]/div/div[3]/div/div/div[3]/div[1]/div[2]/div[1]/div/p'
            due_date_raw = await page.locator(f"xpath={_due_xpath}").inner_text(timeout=5000)
            logger.info("國泰: 繳費截止日原始 = %r", due_date_raw)
            m_due = re.search(r"(\d{4})\s*/\s*(\d{1,2})\s*/\s*(\d{1,2})", due_date_raw)
            if m_due:
                due_date_str = f"{m_due.group(1)}/{int(m_due.group(2)):02d}/{int(m_due.group(3)):02d}"
            else:
                due_date_str = due_date_raw.strip()
                logger.warning("國泰: 無法從繳費截止日文字解析日期: %r", due_date_raw)
            logger.info("國泰: 繳費截止日 = %s", due_date_str)

            # --- 4. 應繳金額：從「TWD 11,750」解析數字 ---
            _amount_xpath = '//*[@id="root"]/div/div[3]/div/div/div[3]/div[1]/div[2]/div[2]/div[1]/div[1]/div[2]/p'
            amount_raw = await page.locator(f"xpath={_amount_xpath}").inner_text(timeout=5000)
            logger.info("國泰: 應繳金額原始 = %r", amount_raw)
            amount_str = re.sub(r"[^\d]", "", amount_raw)
            logger.info("國泰: 應繳金額 = %s", amount_str)

        except Exception as e:
            logger.warning("國泰: 解析帳單失敗 - %s", e)
            return []

        # --- 日期解析 ---
        def _parse_date(raw: str):
            for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y年%m月%d日"):
                try:
                    return datetime.strptime(raw.strip(), fmt).date()
                except ValueError:
                    continue
            raise ValueError(f"無法解析日期: {raw!r}")

        try:
            due_date = _parse_date(due_date_str)
        except Exception as e:
            logger.warning("國泰: 無法解析繳費截止日 %r - %s", due_date_str, e)
            due_date = datetime.now().date()

        try:
            closing_date = _parse_date(closing_date_raw)
        except Exception as e:
            logger.warning("國泰: 無法解析帳單結帳日 %r - %s", closing_date_raw, e)
            closing_date = due_date

        if not amount_str:
            logger.warning("國泰: 無法取得應繳金額")
            return []

        bill = CreditCardBill(
            bank="國泰",
            billing_period=billing_period,
            closing_date=closing_date,
            due_date=due_date,
            amount=Decimal(amount_str),
        )
        logger.info("國泰: 成功抓取帳單 (期別: %s, 結帳日: %s, 截止: %s, 金額: %s)",
                     billing_period, closing_date, due_date, amount_str)
        return [bill]

    async def logout(self, page: Page) -> None:
        logger.info("國泰: 嘗試登出...")
        try:
            _logout_xpath = '//*[@id="root"]/div/div[1]/nav/div/div/div[2]/div/button[2]'
            logout_btn = page.locator(f"xpath={_logout_xpath}")
            await logout_btn.click(timeout=5000)
            await page.wait_for_timeout(2000)
            logger.info("國泰: 成功登出")
        except Exception as e:
            logger.debug("國泰: 登出錯誤 - %s", e)


# ---------------------------------------------------------------------------
# 輔助函式：根據標籤文字抓取旁邊的資料值
# ---------------------------------------------------------------------------

async def _extract_date_near_label(page: Page, label: str) -> str:
    """找到含有 *label* 文字的元素，回傳同層或下一個兄弟/子節點的文字。"""
    try:
        # 策略 1：找含標籤的 <dt>/<th>/<label>，取對應 <dd>/<td>/<span> 的文字
        sel = f"dt:has-text('{label}'), th:has-text('{label}'), label:has-text('{label}')"
        el = page.locator(sel).first
        if await el.count() > 0:
            # 嘗試取 <dd> 或 <td> 或下一個兄弟
            value = await page.evaluate(
                """(el) => {
                    const next = el.nextElementSibling;
                    if (next) return next.innerText.trim();
                    const parent = el.parentElement;
                    if (parent) {
                        const dd = parent.querySelector('dd, td');
                        if (dd) return dd.innerText.trim();
                    }
                    return '';
                }""",
                await el.element_handle(),
            )
            if value:
                return value

        # 策略 2：找含標籤文字的任意元素，取其 nextSibling 文字或父容器內的日期 pattern
        import re
        all_els = page.get_by_text(label)
        count = await all_els.count()
        for i in range(min(count, 3)):
            handle = await all_els.nth(i).element_handle()
            value = await page.evaluate(
                """(el) => {
                    const sibling = el.nextElementSibling;
                    if (sibling) return sibling.innerText.trim();
                    const leaf = el.parentElement?.querySelector('span,p');
                    return leaf ? leaf.innerText.trim() : '';
                }""",
                handle,
            )
            m = re.search(r"\d{4}[/\-年]\d{1,2}[/\-月]\d{1,2}", value)
            if m:
                return m.group(0)
    except Exception as e:
        logger.debug("_extract_date_near_label(%r) 失敗: %s", label, e)
    return ""


async def _extract_amount_near_label(page: Page, label: str) -> str:
    """找到含有 *label* 文字的元素，回傳旁邊的數字文字。"""
    import re
    try:
        all_els = page.get_by_text(label)
        count = await all_els.count()
        for i in range(min(count, 3)):
            handle = await all_els.nth(i).element_handle()
            value = await page.evaluate(
                """(el) => {
                    const candidates = [
                        el.nextElementSibling,
                        el.parentElement?.nextElementSibling,
                        el.closest('tr')?.querySelector('td:last-child'),
                        el.closest('li')?.querySelector('span,p'),
                    ];
                    for (const c of candidates) {
                        if (c && c.innerText.trim()) return c.innerText.trim();
                    }
                    return '';
                }""",
                handle,
            )
            m = re.search(r"[\d,]+", value)
            if m:
                return m.group(0)
    except Exception as e:
        logger.debug("_extract_amount_near_label(%r) 失敗: %s", label, e)
    return ""
