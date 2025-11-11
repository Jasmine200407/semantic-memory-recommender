"""
🧭 LangGraph Node-based Restaurant Recommender
───────────────────────────────────────────────
版本：正式穩定版（Gemini 2.5-flash）
特性：
- 自動引導式輸入檢查（地點 / 主題不足時 Retry）
- 多執行緒抓取評論（同時 3 間）
- 多權重加權排序（match_score, positive_rate, rating）
- 雙層輸出：完整 + latest_recommendation.json（精簡版）
───────────────────────────────────────────────
"""

import os
import json
import time
import datetime
import concurrent.futures
from langgraph.graph import StateGraph, START, END
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

# === 工具匯入（無 backend prefix） ===
from tools.place_info_tool import search_restaurants, location_is_too_large
from tools.review_scraper_tool import get_all_reviews
from tools.embedding_tool import analyze_reviews
from tools.gemini_tool import generate_reason
from tools.save_json import save_json


# ────────────────────────────────
# 🌟 RecommendAgent 主類別
# ────────────────────────────────
class RecommendAgent:
    def __init__(self):
        self.review_dir = "data/reviews"
        self.vector_dir = "data/vectors"
        self.output_dir = "data/recommendations"
        os.makedirs(self.review_dir, exist_ok=True)
        os.makedirs(self.vector_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)

        # 權重設計（可依需求微調）
        self.weights = {"match_score": 0.7, "positive_rate": 0.2, "rating": 0.1}

    # 檢查評論快取（30 天內）
    def check_cache(self, place_id):
        path = os.path.join(self.review_dir, f"{place_id}.json")
        if os.path.exists(path):
            days = (datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(path))).days
            if days <= 30:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        return None

    # 抓取單一餐廳評論（含快取）
    def fetch_single(self, restaurant):
        pid, name = restaurant["place_id"], restaurant["name"]
        cache = self.check_cache(pid)
        if cache:
            return cache
        reviews = get_all_reviews(name, pid)
        if reviews:
            save_json(reviews, os.path.join(self.review_dir, f"{pid}.json"))
        return reviews

    # 批次抓取評論（同時 3 間）
    def fetch_reviews_batch(self, batch):
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(self.fetch_single, r): r for r in batch}
            for f in concurrent.futures.as_completed(futures):
                r = futures[f]
                try:
                    reviews = f.result()
                    if reviews:
                        results.append({"restaurant": r, "reviews": reviews})
                except Exception as e:
                    print(f"❌ {r['name']} 發生錯誤：{e}")
        return results

    # 分析評論、生成推薦理由並儲存
    def analyze_and_save(self, restaurant, reviews, preferences):
        name, pid = restaurant["name"], restaurant["place_id"]
        analysis = analyze_reviews(reviews, preferences)
        reason = generate_reason(name, analysis.get("summary", ""), preferences)
        record = {
            "name": name,
            "map_url": restaurant["map_url"],
            "rating": restaurant.get("rating", 0),
            "user_ratings_total": restaurant.get("user_ratings_total", 0),
            "summary": analysis.get("summary", ""),
            "reason": reason,
            "match_score": analysis.get("match_score", 0),
            "positive_rate": analysis.get("positive_rate", 0)
        }
        save_json(record, os.path.join(self.vector_dir, f"{pid}.json"))
        return record
# ────────────────────────────────
# 🔹 Node 定義區
# ────────────────────────────────
agent = RecommendAgent()
# 🟩 1️⃣ Start Node — 驗證輸入
def start_node(state):
    """
    驗證使用者輸入的地點與餐廳類別。
    若資訊不足或範圍過大，返回 retry_node。
    """
    user_input = state.user_input or {}
    location = user_input.get("location")
    category = user_input.get("category")

    if not location:
        return {"next": "retry_node", "message": "請輸入明確地點（例如：信義區、市府站）。"}
    if not category:
        return {"next": "retry_node", "message": "請告訴我想吃什麼（例如：火鍋、壽司、咖啡廳）。"}
    if location_is_too_large(location):
        return {"next": "retry_node", "message": "地點範圍過大，請縮小搜尋範圍（例如：台北信義區，而非整個台北市）。"}

    print(f"✅ 已確認輸入：地點={location}，主題={category}")
    return {"next": "place_search_node", "location": location, "category": category}


# 🟦 2️⃣ Place Search Node — 搜尋餐廳
def place_search_node(state):
    """
    透過 Google Place API 搜尋指定地點與類別的餐廳。
    若無結果則重試。
    """
    location, category = state.location, state.category
    print(f"🔍 搜尋 {location} 的 {category} 餐廳中...")

    restaurants = search_restaurants(location, category, radius=3000, max_results=10)
    if not restaurants:
        return {"next": "retry_node", "message": "找不到相關餐廳，請嘗試其他區域或主題。"}

    print(f"🍽️ 共找到 {len(restaurants)} 間餐廳。")
    return {"next": "review_fetch_node", "restaurants": restaurants}


# 🟨 3️⃣ Review Fetch Node — 抓取評論
def review_fetch_node(state):
    """
    並行抓取多家餐廳評論，每次最多三家。
    若無評論則重新嘗試。
    """
    restaurants = state.restaurants
    print(f"📥 開始抓取餐廳評論，共 {len(restaurants)} 間...")

    all_reviews = []
    for i in range(0, len(restaurants), 3):
        batch = restaurants[i:i + 3]
        fetched = agent.fetch_reviews_batch(batch)
        all_reviews.extend(fetched)
        time.sleep(0.8)

    if not all_reviews:
        return {"next": "retry_node", "message": "評論擷取失敗，請稍後再試。"}

    print(f"✅ 已成功擷取 {len(all_reviews)} 間餐廳評論。")
    return {"next": "vector_analysis_node", "review_batches": all_reviews}


# 🟧 4️⃣ Vector Analysis Node — 向量化與摘要分析
def vector_analysis_node(state):
    """
    將評論向量化並分析使用者偏好相關度。
    每家餐廳生成摘要與推薦理由。
    """
    prefs = state.preferences or []
    reviews_batch = state.review_batches
    print("🧠 開始語意分析與摘要...")

    analyzed = []
    for item in reviews_batch:
        r = item["restaurant"]
        rev = item["reviews"]
        record = agent.analyze_and_save(r, rev, prefs)
        analyzed.append(record)

    print(f"✅ 已分析完成 {len(analyzed)} 間餐廳。")
    return {"next": "ranking_node", "analyzed": analyzed}


# 🟥 5️⃣ Ranking Node — 加權排序與結果儲存
def ranking_node(state):
    """
    根據 match_score / positive_rate / rating 權重排序，
    並輸出 top-3 結果。
    """
    w = agent.weights
    analyzed = state.analyzed

    sorted_res = sorted(
        analyzed,
        key=lambda x: (
            x["match_score"] * w["match_score"]
            + x["positive_rate"] * w["positive_rate"]
            + (x["rating"] / 5.0) * w["rating"]
        ),
        reverse=True
    )

    # 儲存完整推薦結果
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    full_path = os.path.join(agent.output_dir, f"recommendation_{timestamp}.json")
    save_json(sorted_res, full_path)

    # 儲存簡短版本（給前端快速讀取）
    latest = [
        {
            "name": r["name"],
            "map_url": r["map_url"],
            "rating": r["rating"],
            "reason": r["reason"]
        }
        for r in sorted_res[:3]
    ]
    save_json(latest, os.path.join(agent.output_dir, "latest_recommendation.json"))

    print("🏆 完成加權排序並輸出結果。")
    return {"next": "response_node", "recommendations": sorted_res[:3]}


# 🟪 6️⃣ Response Node — 輸出文字給前端
def response_node(state):
    """
    根據分析結果組合回覆訊息，
    用於回傳給前端或 LINE Bot。
    """
    prefs = state.preferences or []
    recs = state.recommendations
    print("📝 組合輸出文字中...")

    msg = "🎯 根據你的偏好（" + "、".join(prefs) + "），推薦如下：\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(recs):
        msg += f"{medals[i]} {r['name']} - ⭐{r['rating']}（{r['user_ratings_total']} 則評論）\n"
        msg += f"📍 {r['map_url']}\n💬 推薦理由：{r['reason']}\n\n"

    return {"next": END, "message": msg}


# 🔁 Retry Node — 引導使用者重新輸入
def retry_node(state):
    msg = state.message or "請重新輸入地點與餐廳主題。"
    print("🔁 請使用者補充輸入。")
    return {"next": END, "message": msg}

class RecommendState(BaseModel):
    user_input: Optional[Dict[str, Any]] = None
    location: Optional[str] = None
    category: Optional[str] = None
    preferences: Optional[List[str]] = None
    restaurants: Optional[List[Dict[str, Any]]] = None
    review_batches: Optional[List[Dict[str, Any]]] = None
    analyzed: Optional[List[Dict[str, Any]]] = None
    recommendations: Optional[List[Dict[str, Any]]] = None
    message: Optional[str] = None
    next: Optional[str] = None
# ────────────────────────────────
# 🧩 Graph 組裝區
# ────────────────────────────────
def build_recommend_graph():
    """
    建立完整的餐廳推薦流程圖：
    start → place_search → review_fetch → vector_analysis → ranking → response
    若任一步失敗或資訊不足 → retry_node。
    """
    g = StateGraph(RecommendState)   # ← 🔥 必須傳入 state schema

    # === 節點定義 ===
    g.add_node("start_node", start_node)
    g.add_node("place_search_node", place_search_node)
    g.add_node("review_fetch_node", review_fetch_node)
    g.add_node("vector_analysis_node", vector_analysis_node)
    g.add_node("ranking_node", ranking_node)
    g.add_node("response_node", response_node)
    g.add_node("retry_node", retry_node)

    # === 節點連接 ===
    g.add_edge(START, "start_node")

    # ✅ 改成使用屬性取法
    g.add_conditional_edges("start_node", lambda state: state.next)
    g.add_conditional_edges("place_search_node", lambda state: state.next)
    g.add_conditional_edges("review_fetch_node", lambda state: state.next)
    g.add_conditional_edges("vector_analysis_node", lambda state: state.next)
    g.add_conditional_edges("ranking_node", lambda state: state.next)

    g.add_edge("response_node", END)
    g.add_edge("retry_node", END)

    print("🧭 Recommend Graph 已建立完成。")
    return g
# ────────────────────────────────
# 🚀 主程式執行（測試與整合）
# ────────────────────────────────

if __name__ == "__main__":
    """
    測試範例：
    使用者輸入「台北市信義區」與「火鍋」，
    偏好為「約會」與「安靜」。
    可直接執行此檔案驗證整個流程。
    """

    graph = build_recommend_graph()
    app = graph.compile()  # ✅ 新版 LangGraph 需先 compile

    # 模擬使用者輸入
    input_state = {
        "user_input": {
            "location": "台北市信義區",
            "category": "火鍋"
        },
        "preferences": ["約會", "安靜"]
    }

    print("\n🚦 開始執行 Recommend Graph...\n")

    # ✅ 改用 app.invoke() 或 app.stream()
    result = app.invoke(input_state)

    print("\n🧾 === 最終輸出結果 ===\n")
    print(result["message"])

    # 若需要，可額外讀取最新推薦結果
    latest_path = "data/recommendations/latest_recommendation.json"
    if os.path.exists(latest_path):
        print("\n📂 最新推薦摘要已儲存於：", latest_path)
    else:
        print("\n⚠️ 未生成最新推薦摘要（流程可能中斷）。")

