# Foodie Hunter — AI 餐廳推薦系統

Foodie Hunter 是一套基於 FastAPI、WebSocket、LangChain 以及 Google Maps Review 的互動式 AI 餐廳推薦系統。使用者可透過自然語言輸入美食需求，例如「台北信義區不辣的火鍋」，系統會自動解析需求、搜尋餐廳、爬取評論、分析評論語意與情緒，並由 Gemini 生成個人化推薦理由。

---

## 主要功能特性

### 1. 自然語言需求解析

支援輸入地點、餐廳種類與個人偏好，並自動判斷地點範圍是否過大。

### 2. 餐廳搜尋與詳細資訊補全

透過 Google Places API 取得餐廳資訊、地址、電話、營業時間與 Maps 連結。

### 3. 中文 Google Maps 評論爬取

使用 Playwright 反自動化偵測技術，從 Google Maps 抓取繁體中文評論內容與星級。

### 4. 評論語意與情緒分析

使用 MiniLM Embedding 計算評論與偏好語意匹配度，並使用 RoBERTa 進行情緒分析，統計正向比例與評論摘要。

### 5. Gemini 生成推薦理由

根據評論摘要、偏好與匹配分數，生成簡潔、符合實際評論的推薦理由，不添加未觀察到的資訊。

### 6. 前後端 WebSocket 即時互動

後端推送進度與推薦結果，前端以推薦卡片呈現分析結果。

---

## 系統架構圖

```
使用者輸入(自然語言)
        │
        ▼
需求解析與地點驗證
        │
        ▼
Google Places 餐廳搜尋
        │
        ▼
Google Maps 中文評論爬取(Playwright)
        │
        ▼
語意與情緒分析 (Embedding + Sentiment)
        │
        ▼
Gemini 生成推薦理由
        │
        ▼
WebSocket 推播推薦內容至前端
```

---

## 專案結構

```
backend/
├── server.py                     # FastAPI + WebSocket 主服務
├── recommend_agent.py            # 推薦流程與 LangGraph
├── tools/
│   ├── embedding_tool.py         # 語意匹配與情緒分析
│   ├── gemini_tool.py            # 推薦理由生成
│   ├── place_info_tool.py        # 餐廳搜尋與地點檢查
│   └── review_scraper_tool.py    # Google Maps 評論爬取

frontend/
├── index.html                    # 使用者 UI
├── script.js                     # WebSocket 通訊與動態渲染
└── style.css                     # UI 風格與動畫

.env                              # API Key 設定 (不推上 GitHub)
```

---

## 安裝步驟

### 1. 安裝後端環境

```bash
cd backend
pip install -r requirements.txt
```

### 2. 安裝 Playwright

```bash
playwright install
playwright install-deps
```

### 3. 設定 .env

```
GOOGLE_API_KEY=你的GoogleAPI金鑰
GEMINI_API_KEY=你的Gemini金鑰
```

### 4. 啟動服務

```bash
uvicorn server:app --reload
```

### 5. 在瀏覽器開啟

```
http://localhost:8000
```

---

## 使用流程範例

輸入：

```
我想在信義區吃不辣的火鍋
```

系統將依序執行：

* 解析地點與偏好
* 搜尋火鍋餐廳
* 爬取最多 80 則評論
* 計算偏好匹配與評論正向比率
* 產生推薦理由
* 推送卡片至前端
---
## License

本專案採用 MIT License，可自由使用、修改與分享。
---
