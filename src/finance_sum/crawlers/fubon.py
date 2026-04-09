"""富邦銀行爬蟲."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import ddddocr
from playwright.async_api import Page

from finance_sum.crawlers.base import BalanceResult, BankCrawler, CreditCardBill
from finance_sum.store import BankCredential

logger = logging.getLogger(__name__)

_URL = "https://ebank.taipeifubon.com.tw/B2C/common/Index.faces"

class FubonCrawler(BankCrawler):
    """Crawler for 富邦 bank."""

    bank_name = "富邦"

    _ocr = None

    @classmethod
    def get_ocr(cls) -> ddddocr.DdddOcr:
        if cls._ocr is None:
            cls._ocr = ddddocr.DdddOcr(show_ad=False)
        return cls._ocr

    def _convert_roc_date(self, roc_date_str: str) -> str:
        """將民國年轉換為西元年。如 '115/03' -> '2026/03', '115/03/18' -> '2026/03/18'"""
        parts = roc_date_str.split("/")
        if len(parts) >= 2:
            parts[0] = str(int(parts[0]) + 1911)
            return "/".join(parts)
        return roc_date_str

    async def login(self, page: Page, credential: BankCredential) -> None:
        logger.info("富邦: 前往首頁準備登入")
        
        # 註冊彈窗監聽器，自動點擊「確定」
        page.on("dialog", lambda dialog: dialog.accept())
        
        await page.goto(_URL, wait_until="domcontentloaded")
        
        for attempt in range(1, 6):
            logger.info("富邦: 登入嘗試 %d/5", attempt)
            
            # 1. 確保彈窗已開啟且找到正確框架
            target_frame = None
            login_entry = None
            
            try:
                # 給 frames 一些載入時間，重複檢查 10 次 (約 10 秒)
                for _ in range(10):
                    # 偵測是否已經在彈窗內 (尋找「信用卡網路會員登入」)
                    for frame in page.frames:
                        try:
                            if await frame.get_by_text("信用卡網路會員登入").is_visible(timeout=500):
                                target_frame = frame
                                break
                        except: continue
                        
                    if target_frame:
                        break
                        
                    # 如果還沒看到彈窗，找看看有沒有「登入」按鈕
                    for frame in page.frames:
                        try:
                            target = frame.locator("a:has-text('登入'), a:text-is('登入')").first
                            if await target.is_visible(timeout=500):
                                login_entry = target
                                break
                        except: continue
                        
                    if login_entry:
                        logger.info("富邦: 找到登入按鈕，準備點擊...")
                        await login_entry.click(force=True)
                        await page.wait_for_timeout(2000)
                        break

                    # 等待一下再試
                    await page.wait_for_timeout(1000)

                if login_entry and not target_frame:
                    # 如果有點擊登入按鈕，再次掃描尋找彈窗
                    for frame in page.frames:
                        try:
                            if await frame.get_by_text("信用卡網路會員登入").is_visible(timeout=2000):
                                target_frame = frame
                                break
                        except: continue

            except Exception as e:
                logger.warning("富邦: 尋找登入視窗過程發生錯誤: %s", e)
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
                continue

            if not target_frame:
                logger.warning("富邦: 無法定位登入框架，重試中...")
                await page.reload()
                continue

            # 2. 確保切換至「信用卡會員」分頁 (解決閃一下跳回一般會員的問題)
            try:
                cc_tab = target_frame.get_by_text("信用卡網路會員登入")
                await cc_tab.click(force=True)
                await page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning("富邦: 切換分頁失敗: %s", e)

            # 3. 在框架內尋找填寫欄位 (使用 visible 濾鏡)
            try:
                id_input = target_frame.locator("tr:has-text('身分證字號')").locator("input").filter(visible=True)
                user_input = target_frame.locator("tr:has-text('使用者代碼')").locator("input").filter(visible=True)
                pwd_input = target_frame.locator("tr:has-text('使用者密碼')").locator("input").filter(visible=True)
                captcha_input = target_frame.locator("tr:has-text('驗證碼')").locator("input").filter(visible=True)
                captcha_img = target_frame.locator("tr:has-text('驗證碼')").locator("img").filter(visible=True)

                await id_input.first.wait_for(state="visible", timeout=10000)
                
                # 填寫資料
                await id_input.first.fill(credential.id_number)
                await user_input.first.fill(credential.username)
                await pwd_input.first.fill(credential.password)

                # 處理驗證碼
                await captcha_img.first.wait_for(state="visible", timeout=10000)
                raw_bytes = await captcha_img.first.screenshot()
                img_bytes = bytes(raw_bytes)
                Path("/tmp/fubon_captcha.png").write_bytes(img_bytes)

                ocr = self.get_ocr()
                captcha_text = ocr.classification(img_bytes)
                logger.info("富邦: 驗證碼辨識結果: %r", captcha_text)
                await captcha_input.first.fill(captcha_text)
                
                await page.wait_for_timeout(500)

                # 點擊正確的「登入」藍色按鈕
                submit_btn = target_frame.locator("a:text-is('登入'), a.btn_blue").filter(visible=True).last
                await submit_btn.click(force=True)
                
            except Exception as e:
                logger.warning("富邦: 表單填寫失敗: %s", e)
                await page.reload()
                continue

            # 4. 等待登入結果
            success = False
            try:
                for _ in range(20):  # 稍微增加等待時間
                    # 檢查所有框架中是否有「登出」按鈕或儀表板特徵
                    for f in page.frames:
                        try:
                            # 檢查「登出」按鈕
                            logout_btn = f.locator("a:has-text('登出')").first
                            if await logout_btn.is_visible(timeout=500):
                                logger.info("富邦: 在框架 '%s' 偵測到登出按鈕，登入成功", f.name or "(無名)")
                                success = True
                                break
                            
                            # 檢查儀表板特定文字 (我的存款, 帳戶總覽, 綜合存款)
                            dashboard_marker = f.locator("text='我的存款', text='帳戶總覽', text='綜合存款'").first
                            if await dashboard_marker.is_visible(timeout=500):
                                logger.info("富邦: 在框架 '%s' 偵測到儀表板特徵，登入成功", f.name or "(無名)")
                                success = True
                                break
                        except:
                            continue
                    
                    if success:
                        break
                    
                    # 檢查原本的登入表單是否消失 (包含處理框架斷開的情況)
                    try:
                        is_form_visible = await id_input.first.is_visible(timeout=500)
                        if not is_form_visible:
                            logger.debug("富邦: 登入表單已不在視覺範圍內")
                    except Exception as e:
                        # 如果丟出 "Frame was detached" 或 "Execution context was destroyed"
                        # 通常代表彈窗已關閉或頁面跳轉
                        marker_keywords = ["detached", "destroyed", "closed", "navigated"]
                        if any(k in str(e).lower() for k in marker_keywords):
                            logger.info("富邦: 登入框架已斷開或銷毀，可能正在跳轉中...")
                        else:
                            logger.debug("富邦: 檢查表單狀態時的預期外錯誤: %s", e)
                    
                    await page.wait_for_timeout(1000)
            except Exception as e:
                logger.debug("富邦: 監測登入狀態時發生錯誤: %s", e)
            
            if success:
                return

            logger.warning("富邦: 登入超時或表單依然存在，準備下一次嘗試")
            await page.reload()
            await page.wait_for_timeout(2000)

        raise RuntimeError("富邦: 登入失敗 (超過重試次數)")

    async def fetch_balance(self, page: Page) -> list[BalanceResult]:
        return []

    async def fetch_credit_card_bills(self, page: Page) -> list[CreditCardBill]:
        logger.info("富邦: 準備前往信用卡帳單頁面")
        
        # 由於是 frameset 架構，我們在主框架 frame1 中進行導航
        main_frame = page.frame(name="frame1") or page
        
        try:
            # 1. 點擊頂部「信用卡/簽帳金融卡」選單
            cc_menu = main_frame.locator("a:has-text('信用卡/簽帳金融卡')")
            await cc_menu.wait_for(state="visible", timeout=15000)
            await cc_menu.click(force=True)
            await page.wait_for_timeout(1500)

            # 2. 由於子選單會載入到 txnFrame 框架中，切換目標框架
            txn_frame = main_frame.frame_locator("iframe[name='txnFrame']")
            
            # Hover 到「帳務/繳款」區塊 (不再依賴特定標籤 span)
            billing_section = txn_frame.locator("text='帳務/繳款'").first
            await billing_section.wait_for(state="visible", timeout=10000)
            await billing_section.hover()
            await page.wait_for_timeout(500)
            # 確保它是展開的
            await billing_section.click(force=True)
            await page.wait_for_timeout(1000)

            # 3. 帳單明細查詢
            bill_query = txn_frame.locator("text='帳單明細查詢'").first
            await bill_query.wait_for(state="visible", timeout=10000)
            await bill_query.click(force=True)
            await page.wait_for_timeout(3000)

            # 4. 取得表格內容進行精準解析
            table_headers = await txn_frame.locator("th, td.head, td[class*='header']").all_inner_texts()
            table_cells = await txn_frame.locator("td").all_inner_texts()
            
            # 使用 evaluate 抓取含有關鍵字的整列資料，比較不容易出錯
            bill_info = await txn_frame.locator("body").evaluate('''() => {
                let info = {};
                
                // 尋找所有的 table rows
                let rows = document.querySelectorAll('tr');
                let foundHeaders = [];
                
                for (let row of rows) {
                    let cells = Array.from(row.querySelectorAll('td, th')).map(c => c.innerText.trim());
                    // 尋找包含標題的 row
                    if (cells.includes('帳單年月') || cells.includes('帳單結帳日')) {
                        foundHeaders = cells;
                        continue;
                    }
                    if (cells.includes('本期應繳總額(註三)') || cells.includes('本期應繳總額')) {
                        foundHeaders = cells;
                        continue;
                    }
                    
                    // 如果這個 row 是標題 row 的下一列 (資料列)
                    if (foundHeaders.length > 0 && cells.length > 0) {
                        for (let i = 0; i < foundHeaders.length; i++) {
                            if (foundHeaders[i] && cells[i]) {
                                let key = foundHeaders[i].replace(/\\s+/g, '');
                                info[key] = cells[i];
                            }
                        }
                        foundHeaders = []; // 讀完資料列就清空，等待下一個標題列
                    }
                }
                return info;
            }''')
            
            logger.info("富邦: 擷取到的帳單欄位: %s", bill_info)

            # 解析帳單年月
            period_roc = bill_info.get("帳單年月", datetime.now().strftime("%Y/%m"))
            period_roc = self._convert_roc_date(period_roc)
            
            # 解析帳單結帳日
            closing_date_str = bill_info.get("帳單結帳日")
            if closing_date_str:
                closing_date_str = self._convert_roc_date(closing_date_str)
                closing_date = datetime.strptime(closing_date_str, "%Y/%m/%d").date()
            else:
                closing_date = datetime.now().date()
                
            # 解析繳款截止日
            due_date_str = bill_info.get("繳款截止日")
            due_date = closing_date
            if due_date_str and due_date_str != "無需繳款":
                try:
                    due_date_str = self._convert_roc_date(due_date_str)
                    due_date = datetime.strptime(due_date_str, "%Y/%m/%d").date()
                except Exception:
                    logger.warning("富邦: 無法解析繳款截止日: %s", due_date_str)
            
            # 解析金額
            amount_str = bill_info.get("本期應繳總額(註三)", bill_info.get("本期應繳總額", "0"))
            amount_str = amount_str.replace(",", "").strip()
            if not amount_str:
                amount_str = "0"

        except Exception as e:
            logger.warning("富邦: 解析信用卡帳單發生錯誤或找不到該節點 - %s", e)
            try:
                await page.screenshot(path="fubon_error.png", full_page=True)
                
                # 嘗試傾印出所有框架中的 HTML 和文字，以便除錯
                dump_text = []
                for idx, f in enumerate(page.frames):
                    try:
                        title = await f.title()
                        content = await f.content()
                        dump_text.append(f"==== Frame {idx} ({f.name}) | Title: {title} ====\n")
                        
                        # 抽出所有的標楷體與連結文字
                        links = await f.locator("a, span, td, div").all_inner_texts()
                        links_clean = [text.strip() for text in links if text.strip()]
                        # 去除重複並維持順序
                        unique_links = []
                        for text in links_clean:
                            if text not in unique_links:
                                unique_links.append(text)
                        
                        dump_text.append("Texts:\n" + "\n".join(unique_links[:200]) + "\n\n")
                    except Exception as fe:
                        dump_text.append(f"==== Frame {idx} ({f.name}) failed: {fe} ====\n\n")
                
                Path("fubon_error_all_frames.txt").write_text("".join(dump_text), encoding="utf-8")
                
                logger.info("==== 已經將錯誤當下的截圖與所有Frame的文字儲存為 fubon_error*.txt/png，請檢查 ====")
            except Exception as dump_e:
                logger.error("富邦: 儲存錯誤狀態失敗 - %s", dump_e)
            return []

        if not amount_str:
            return []

        bill = CreditCardBill(
            bank="富邦",
            billing_period=period_roc,
            closing_date=closing_date,
            due_date=due_date,
            amount=Decimal(amount_str),
        )
        logger.info("富邦: 成功抓取信用卡帳單 (結帳日: %s, 應繳金額: %s)", closing_date, amount_str)
        return [bill]

    async def logout(self, page: Page) -> None:
        logger.info("富邦: 嘗試登出...")
        try:
            logout_btn1 = '/html/body/div[1]/form/div/div/a[4]'
            logout_btn2 = '/html/body/div[5]/div/div[2]/div/div/div/div/a[2]'
            
            await page.locator(f"xpath={logout_btn1}").click(timeout=5000)
            await page.wait_for_timeout(1000)
            
            if await page.locator(f"xpath={logout_btn2}").is_visible(timeout=3000):
                await page.locator(f"xpath={logout_btn2}").click()
                await page.wait_for_timeout(2000)
                
            logger.info("富邦: 成功登出...")
        except Exception as e:
            logger.debug("富邦: 登出錯誤 - %s", e)
