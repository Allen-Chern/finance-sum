# FinanceSum

私用財務自動化 CLI 工具 — 自動擷取台灣各大銀行餘額與信用卡帳單，同步至 Notion 並透過 Telegram 通知。

## 支援銀行與功能實作狀況

目前系統支援（含計畫支援）以下各大銀行，針對核心功能 `login`、`fetch_balance`、`fetch_credit_card_bills` 的實作進度如下：

| 銀行名稱 | 登入 | 抓取餘額 | 抓取信用卡帳單 | 實作細節說明 |
|:---|:---:|:---:|:---:|:---|
| **台新** | ✅ | ✅ | ✅ | |
| **土銀** | ✅ | ✅ | ❌ | |
| **王道** | ✅ | ✅ | ❌ | |
| **國泰** | ✅ | ✅ | ✅ | |
| **星展** | ✅ | ❌ | ✅ | Card+ 信用卡網銀專屬登入、無帳戶餘額。 |
| **永豐** | ✅ | ❌ | ✅ | |
| **富邦** | ✅ | ❌ | ✅ | |
| **聯邦** | ✅ | ❌ | ✅ | |

## 快速開始

```bash
# 建立虛擬環境並安裝依賴
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 設定銀行帳密
financesum login

# 同步資料
financesum sync

# 僅同步特定銀行
financesum sync --bank 台新
```

---

## Notion 設定

### 第一步：建立 Notion Integration 並取得 API Key

1. 前往 [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations)
2. 點擊 **「+ New integration」**，填入名稱（例：FinanceSum）
3. 選擇對應的 Workspace，按 **Submit**
4. 複製 **「Internal Integration Secret」**（格式：`secret_xxxxxxxx`），填入 `.env` 的 `NOTION_API_KEY`

---

### 第二步：建立「餘額」Database

1. 在 Notion 中建立一個新的 **Full Page Database**，命名為「銀行餘額」
2. 設定以下欄位（Properties）：

| 欄位名稱 | 類型 | 說明 |
|----------|------|------|
| `銀行` | **Title** | 預設欄位，改名為「銀行」即可 |
| `帳號` | Text | 帳號末四碼 |
| `餘額` | Number | 格式選「台幣 NT$」 |
| `更新時間` | Date | 包含時間（Include time） |

3. 點擊右上角 **「⋯」→「Add connections」**，搜尋並加入剛才建立的 Integration
4. 複製 Database ID：
   - 在瀏覽器開啟此 Database 的頁面
   - 網址格式：`https://www.notion.so/{workspace}/{DATABASE_ID}?v=...`
   - 取出 `DATABASE_ID`（32 字元的英數字串，中間有 `-`）
   - 填入 `.env` 的 `NOTION_BALANCE_DB_ID`

---

### 第三步：建立「信用卡帳單」Database

1. 在 Notion 中建立另一個新的 **Full Page Database**，命名為「信用卡帳單」
2. 設定以下欄位（Properties）：

| 欄位名稱 | 類型 | 說明 |
|----------|------|------|
| `帳單ID` | **Title** | 預設欄位，改名為「帳單ID」即可（系統自動填入 hash） |
| `銀行` | Select | 各銀行名稱 |
| `期別` | Text | 帳單期別，例：`2026/03` 或 `總帳單` |
| `帳單結帳日` | Date | 帳單結帳日期 |
| `繳費截止日` | Date | 最後繳款日期 |
| `應繳金額` | Number | 格式選「台幣 NT$」 |
| `繳費日期` | Date | 實際繳費日期（繳費後手動更新） |
| `自動扣繳帳戶` | Text | 自動扣繳帳戶資訊（無則留空） |

3. 同樣加入 Integration 連接（同第二步驟第 3 點）
4. 用同樣方式複製 Database ID，填入 `.env` 的 `NOTION_CREDIT_CARD_DB_ID`

---

## 環境變數

複製 `.env.example` 為 `.env` 並填入：

```bash
cp .env.example .env
```

| 變數 | 說明 |
|------|------|
| `STORAGE_BACKEND` | 儲存方式（`notion` 或 `json`），預設為 `notion`。設為 `json` 將資料存至本機以免上傳。 |
| `NOTION_API_KEY` | Notion Integration Token（`secret_` 開頭） |
| `NOTION_BALANCE_DB_ID` | 餘額 Database ID（32 字元） |
| `NOTION_CREDIT_CARD_DB_ID` | 信用卡帳單 Database ID（32 字元） |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token（向 [@BotFather](https://t.me/botfather) 申請） |
| `TELEGRAM_CHAT_ID` | 目標 Chat ID（可用 [@userinfobot](https://t.me/userinfobot) 查詢） |
| `HEADLESS` | Playwright 是否背景執行（預設 `true`，除錯時設 `false`） |

---

## 安全說明

- 銀行帳密以 **Fernet 對稱加密**儲存，金鑰位於 `.key`
- `.key`、`.env`、`credentials.json` 均已加入 `.gitignore`
- 憑證儲存路徑：`~/.config/finance-sum/credentials.json`
