# 🍽️ AI 餐廳推薦系統（LangGraph + Gemini + Embedding）

這是一個基於 LangGraph 節點架構的智能餐廳推薦系統，結合 Google Maps 評論擷取、SentenceTransformer 語意分析，以及 Gemini 模型生成個人化推薦理由，用於打造理解使用者口味與偏好的推薦體驗。

---

## 🧩 系統模組架構

```
recommend_agent.py        # 主流程（LangGraph 節點式推薦代理）
├── embedding_tool.py      # 向量分析與評論偏好相似度
├── gemini_tool.py         # 使用 Gemini 生成推薦理由
├── place_info_tool.py     # Google Places 餐廳搜尋與地點範圍檢查
├── review_scraper_tool.py # Playwright 爬取 Google Maps 評論
└── save_json.py           # 通用 JSON 儲存工具
```

---

## ⚙️ 安裝與設定

```bash
pip install -r requirements.txt
```

建立 `.env` 檔案：

```env
GOOGLE_PLACE_API_KEY=你的GoogleAPI金鑰
GEMINI_API_KEY=你的GeminiAPI金鑰
```

初始化 Playwright（首次執行）：

```bash
playwright install chromium
```

---

## 🚀 執行方式

```bash
python recommend_agent.py
```

系統會自動執行：
1. 驗證輸入地點與餐廳主題；
2. 使用 Google Places API 搜尋餐廳；
3. 並行擷取多家餐廳評論；
4. 使用 SentenceTransformer 進行語意與情感分析；
5. 由 Gemini 生成自然語言推薦理由；
6. 最終輸出 Top 3 餐廳與推薦摘要。

---

## 📁 輸出資料結構

```
data/
├── reviews/                ← 各餐廳評論 JSON
├── vectors/                ← 各餐廳向量分析結果
└── recommendations/
    ├── recommendation_YYYYMMDD_HHMMSS.json
    └── latest_recommendation.json
```

---

## 🧠 LangGraph 節點流程(施工中)

| 節點名稱 | 功能說明 |
|-----------|-----------|
| start_node | 驗證使用者輸入 |
| place_search_node | 搜尋餐廳 |
| review_fetch_node | 擷取多家評論（多執行緒） |
| vector_analysis_node | 向量化與語意分析 |
| ranking_node | 加權排序與結果輸出 |
| response_node | 組合回覆訊息 |
| retry_node | 補充輸入或處理錯誤時重試 |

---

## 📊 推薦加權公式

```python
final_score = (
    match_score * 0.7 +
    positive_rate * 0.2 +
    (rating / 5.0) * 0.1
)
```

---

## 🧾 範例輸出

```
🎯 根據你的偏好（約會、安靜氣氛），推薦如下：

🥇 手工殿麻辣鍋物 - ⭐4.6（385 則評論）
📍 https://www.google.com/maps/place/?q=place_id:XXXX
💬 推薦理由：這間火鍋店氣氛溫馨、座位寬敞，很適合情侶約會放鬆聊天。

🥈 八海食堂 - ⭐4.5（212 則評論）
📍 https://www.google.com/maps/place/?q=place_id:YYYY
💬 推薦理由：食材新鮮、餐點精緻，是聚餐或家庭用餐的熱門選擇。
```

---

## 🧠 模組功能說明

| 模組名稱 | 功能摘要 |
|-----------|-----------|
| embedding_tool.py | 轉換評論文字為語意向量，分析與使用者偏好的相似度。 |
| gemini_tool.py | 使用 Google Gemini 生成自然語言推薦理由。 |
| place_info_tool.py | 根據地點與關鍵字搜尋餐廳資訊並取得 Place ID。 |
| review_scraper_tool.py | 使用 Playwright 自動滾動並爬取 Google Maps 評論。 |
| save_json.py | 儲存 JSON 資料（支援 LangGraph 節點整合）。 |
| recommend_agent.py | 主控制模組，整合所有工具形成完整推薦流程。 |

---

## 🧰 相依套件（requirements.txt）

```txt
langchain>=0.3.0
langgraph>=0.2.0
pydantic>=2.8.0
python-dotenv>=1.0.0
torch>=2.0.0
sentence-transformers>=2.2.2
transformers>=4.40.0
numpy>=1.25.0
requests>=2.31.0
playwright>=1.43.0
google-generativeai>=0.5.4
concurrent-log-handler>=0.9.24
tqdm>=4.66.0
ipython
rich
```
