# Foodie Hunter Backend

Foodie Hunter Backend 提供餐廳智慧推薦 API，結合 Google Maps 餐廳資料、評論爬取、中文語意分析與大型語言模型推薦理由生成。本系統基於 LangGraph 建立推薦流程，使用者僅需輸入一句自然語言敘述，即可獲得完整美食推薦結果。

---

## Features

* **自然語言需求解析**：使用 Gemini 模型解析文字需求，抽取地點、類型與偏好條件。
* **Google Maps 餐廳資料查詢**：取得餐廳基本資訊與評價。
* **Google Maps 中文評論爬取**：自動滾動載入、支援容錯 selector。
* **中文情緒與語意分析**：模型分析評論情感傾向與偏好相關性。
* **個人化推薦理由生成**：Gemini 自動產生推薦原因。
* **資料快取與推薦結果儲存**：自動管理 JSON 快取避免重複爬取。
* **完整推薦決策流程**：採用 LangGraph 建立具狀態的推薦 Agent。

---

## System Architecture

```
+──────────────┐
| User Query   |
+─────┬────────┘
      ▼
[1] Parse User Input (Gemini)
      ▼
[2] Search Restaurants (Google Places API)
      ▼
[3] Fetch Reviews (Playwright爬蟲)
      ▼
[4] NLP Semantic Analysis (BERT)
      ▼
[5] Score & Ranking
      ▼
[6] LLM-generated Recommendation Message
      ▼
+──────────────┐
| Recommendation Output |
+──────────────────────┘
```

---

## Project Structure

```
backend/
│
├─ tools/
│  ├─ embedding_tool.py      # 語意分析與情感分析
│  ├─ gemini_tool.py         # LLM 理由生成
│  ├─ place_info_tool.py     # Google 餐廳資訊
│  ├─ review_scraper_tool.py # Playwright 評論爬蟲
│  ├─ save_json.py           # JSON 輸出管理
│
├─ recommend_agent.py        # LangGraph 主流程建構
├─ test_nlp.py               # 單次呼叫與輸出測試腳本
└─ data/                     # 自動生成的快取與推薦結果
```

---

## Environment Setup

### 建立虛擬環境

Windows PowerShell:

```sh
python -m venv venv
venv\Scripts\Activate.ps1
```

macOS / Linux:

```sh
python3 -m venv venv
source venv/bin/activate
```

### 安裝套件

```sh
pip install -r requirements.txt
```

### 設定環境變數

建立 `.env`：

```
GOOGLE_API_KEY=your_google_api_key
GEMINI_API_KEY=your_gemini_api_key
```

Playwright 初次安裝：

```sh
playwright install
```

---

## Run Example

執行測試流程：

```sh
python test_nlp.py
```

範例輸入：

```
我想在信義區找適合約會的火鍋
```

---

## Data Output

| 類別     | 路徑                    |
| -------- | ----------------------- |
| 餐廳清單 | `data/restaurant_list/` |
| 評論快取 | `data/reviews/`         |
| 推薦結果 | `data/recommendations/` |

---

## License

本專案採用 MIT License 授權。
