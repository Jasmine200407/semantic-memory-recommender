# -*- coding: utf-8 -*-
"""
RecommendAgent with debug logging (no emojis)
"""

import os
import re
import json
import datetime
import concurrent.futures
from typing import Optional, List, Dict, Any

from pydantic import BaseModel
from langgraph.graph import StateGraph, START, END

from tools.place_info_tool import search_restaurants
from tools.review_scraper_tool import get_all_reviews
from tools.embedding_tool import analyze_reviews
from tools.gemini_tool import call_gemini, generate_reason
from tools.save_json import save_json


# ============ 自然語言分析 ============

def parse_user_input(user_input: str) -> Optional[Dict[str, Any]]:
    prompt = f"""
    將以下使用者需求整理成 JSON：
    「{user_input}」
    回傳格式：
    {{
      "location": "地點",
      "category": "種類（火鍋/壽司/燒肉...）",
      "preferences": ["偏好"]
    }}
    若無偏好→["一般用餐需求"]
    僅輸出 JSON。
    """
    try:
        raw = call_gemini(prompt).strip()
        print("[parse_user_input] Gemini 原始回傳：", raw)

        # 移除可能的 ```json``` 區塊標記
        raw = re.sub(r"```(?:json)?(.*?)```", r"\1", raw, flags=re.DOTALL).strip()

        # 嘗試只截取最外層的 JSON 區塊
        start_idx = raw.find("{")
        end_idx = raw.rfind("}")
        if start_idx == -1 or end_idx == -1:
            raise ValueError("找不到有效的 JSON 區塊")

        json_str = raw[start_idx: end_idx + 1]
        data = json.loads(json_str)
        print("[parse_user_input] 解析後 JSON：", data)

        if isinstance(data.get("preferences"), str):
            data["preferences"] = [data["preferences"]]

        if not data.get("preferences"):
            data["preferences"] = ["一般用餐需求"]

        return data
    except Exception as e:
        print("[parse_user_input] 解析失敗：", e)
        return None


# ============ RecommendAgent 核心 ============

class RecommendAgent:
    def __init__(self):
        self.review_dir = "data/reviews"
        self.restaurant_list_dir = "data/restaurant_list"
        self.recommendations_dir = "data/recommendations"
        os.makedirs(self.review_dir, exist_ok=True)
        os.makedirs(self.restaurant_list_dir, exist_ok=True)
        os.makedirs(self.recommendations_dir, exist_ok=True)

        self.max_reviews = 80
        self.cache_days = 30
        self.weights = {"match_score": 0.7, "positive_rate": 0.2, "rating": 0.1}

    def _safe_name(self, name: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fa5]+", "_", name).strip("_")

    def _review_cache_path(self, name: str) -> str:
        return os.path.join(self.review_dir, f"{self._safe_name(name)}.json")

    # -- 使用一個月內 cache --
    def check_cache(self, name: str) -> Optional[List[Dict[str, Any]]]:
        path = self._review_cache_path(name)
        if not os.path.exists(path):
            print(f"[check_cache] {name} 尚無快取檔")
            return None

        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        diff_days = (datetime.datetime.now() - mtime).days
        print(f"[check_cache] {name} 快取檔更新日：{mtime.date()}，距今 {diff_days} 天")

        if diff_days > self.cache_days:
            print(f"[check_cache] {name} 快取超過 {self.cache_days} 天，不使用")
            return None

        try:
            data = json.load(open(path, encoding="utf-8"))
            reviews = data.get("reviews", [])
            print(f"[check_cache] 讀取到 reviews 數量：{len(reviews)}")
            return reviews
        except Exception as e:
            print(f"[check_cache] 讀取快取失敗：", e)
            return None

    # -- 單店評論爬取 --
    def fetch_single(self, restaurant):
        name = restaurant.get("name")
        place_id = restaurant.get("place_id")
        print(f"[fetch_single] 處理餐廳：{name} ({place_id})")

        if not name or not place_id:
            print("[fetch_single] 缺少名稱或 place_id，略過")
            return None

        # 先看 cache
        cache = self.check_cache(name)
        if cache:
            print(f"[fetch_single] 使用快取：{name}，評論數：{len(cache)}")
            return {"restaurant": restaurant, "reviews": cache}

        print(f"[fetch_single] 沒有快取，開始爬取：{name}")
        reviews = get_all_reviews(name, place_id, max_reviews=self.max_reviews)
        print(f"[fetch_single] {name} 實際抓到評論數：{len(reviews) if reviews else 0}")

        if reviews:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            try:
                save_json.invoke({
                    "data": {
                        "place_id": place_id,
                        "name": name,
                        "address": restaurant.get("address"),
                        "rating": restaurant.get("rating"),
                        "user_ratings_total": restaurant.get("user_ratings_total"),
                        "last_update": today,
                        "reviews": reviews,
                    },
                    "path": self._review_cache_path(name),
                })
                print(f"[fetch_single] 已寫入快取：{self._review_cache_path(name)}")
            except Exception as e:
                print(f"[fetch_single] 寫入快取失敗：", e)

            return {"restaurant": restaurant, "reviews": reviews}

        print(f"[fetch_single] {name} 沒有成功取得評論")
        return None

    # -- 批次爬取 --
    def fetch_reviews_batch(self, restaurants):
        print(f"[fetch_reviews_batch] 準備處理餐廳數量：{len(restaurants)}")
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as exe:
            futures = [exe.submit(self.fetch_single, r) for r in restaurants]
            for f in concurrent.futures.as_completed(futures):
                try:
                    res = f.result()
                    if res:
                        results.append(res)
                except Exception as e:
                    print("[fetch_reviews_batch] future 發生錯誤：", e)

        print(f"[fetch_reviews_batch] 成功取得評論的餐廳數量：{len(results)}")
        return results

    # -- NLP 分析 --
    def analyze_results(self, review_batches, prefs):
        print("[analyze_results] 進來的餐廳數量：", len(review_batches))
        print("[analyze_results] 使用者偏好：", prefs)

        output = []
        for rb in review_batches:
            r, reviews = rb["restaurant"], rb["reviews"]
            print(f"[analyze_results] 處理餐廳：{r.get('name')}，評論數：{len(reviews)}")

            try:
                res = analyze_reviews(reviews, prefs)
                print(
                    f"[analyze_results] NLP 結果：match={res.get('match_score')}, "
                    f"positive_rate={res.get('positive_rate')}"
                )
                print(
                    "[analyze_results] 摘要片段：",
                    (res.get("summary") or "")[:50]
                )
            except Exception as e:
                print("[analyze_results] analyze_reviews 發生錯誤：", e)
                res = {"summary": "", "match_score": 0.0, "positive_rate": 0.0}

            try:
                reason_text = generate_reason(r["name"], res.get("summary", ""), prefs)
            except Exception as e:
                print("[analyze_results] generate_reason 發生錯誤：", e)
                reason_text = "系統暫時無法提供詳細理由，建議可先參考整體評價與評論內容。"

            output.append({
                **r,
                "summary": res.get("summary", ""),
                "match_score": float(res.get("match_score", 0) or 0.0),
                "positive_rate": float(res.get("positive_rate", 0) or 0.0),
                "reason": reason_text,
            })

        print("[analyze_results] 最終輸出餐廳數量：", len(output))
        return output


agent = RecommendAgent()


# ============ Graph State ============

class RecommendState(BaseModel):
    user_input: Optional[str] = None
    location: Optional[str] = None
    category: Optional[str] = None
    preferences: Optional[List[str]] = None
    restaurants: Optional[List[Dict[str, Any]]] = None
    review_batches: Optional[List[Dict[str, Any]]] = None
    analyzed: Optional[List[Dict[str, Any]]] = None
    recommendations: Optional[List[Dict[str, Any]]] = None
    next: Optional[str] = None
    message: Optional[str] = None


# ============ Nodes ============

def parse_user_input_node(state):
    print("[parse_user_input_node] 原始輸入：", state.user_input)
    data = parse_user_input(state.user_input)
    print("[parse_user_input_node] 解析結果：", data)

    if not data:
        return {"next": END, "message": "我不太懂，可以換句話嗎？"}

    return {
        "next": "place_search_node",
        "location": data.get("location"),
        "category": data.get("category"),
        "preferences": data.get("preferences"),
    }


def place_search_node(state):
    print("[place_search_node] location =", state.location, "category =", state.category)
    restaurants = search_restaurants(state.location, state.category)
    print("[place_search_node] 搜尋到餐廳數量：", len(restaurants))

    if not restaurants:
        return {"next": END, "message": "這裡似乎沒有你想吃的餐廳"}

    for r in restaurants[:3]:
        print(
            "  [place_search_node] 範例餐廳：",
            r.get("name"),
            "評分：", r.get("rating"),
            "評論數：", r.get("user_ratings_total"),
        )

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        save_json.invoke({
            "data": {
                "location": state.location,
                "category": state.category,
                "timestamp": timestamp,
                "restaurants": restaurants
            },
            "path": os.path.join(
                agent.restaurant_list_dir,
                f"{state.location}_{state.category}_{timestamp}.json"
            ),
        })
        print("[place_search_node] 已儲存餐廳清單紀錄")
    except Exception as e:
        print("[place_search_node] 儲存餐廳清單失敗：", e)

    return {"next": "review_fetch_node", "restaurants": restaurants}


def review_fetch_node(state):
    restaurants = state.restaurants or []
    print("[review_fetch_node] 餐廳數量：", len(restaurants))

    if not restaurants:
        return {"next": END, "message": "找不到相關餐廳"}

    results = agent.fetch_reviews_batch(restaurants)
    print("[review_fetch_node] fetch_reviews_batch 結果數量：", len(results))

    if not results:
        analyzed = restaurants
        print("[review_fetch_node] 沒有成功取得評論，改用原始餐廳清單作 ranking")
        return {
            "next": "ranking_node",
            "analyzed": analyzed,
            "message": "評論取得失敗，改用星等與人氣推薦"
        }

    return {
        "next": "analysis_node",
        "review_batches": results
    }


def analysis_node(state):
    print("[analysis_node] review_batches 數量：", len(state.review_batches or []))
    if not state.review_batches:
        analyzed = state.analyzed or []
        return {"next": "ranking_node", "analyzed": analyzed}

    analyzed = agent.analyze_results(state.review_batches, state.preferences)
    print("[analysis_node] 分析後餐廳數量：", len(analyzed or []))

    if analyzed:
        first = analyzed[0]
        print(
            "[analysis_node] 範例：",
            first.get("name"),
            "match_score =", first.get("match_score"),
            "positive_rate =", first.get("positive_rate"),
        )

    return {"next": "ranking_node", "analyzed": analyzed}


def ranking_node(state):
    print("[ranking_node] 收到 analyzed 數量：", len(state.analyzed or []))

    if not state.analyzed:
        print("[ranking_node] analyzed 為空，無法排序")
        return {"next": END, "message": "推薦餐廳不足，請換個方式提問"}

    for r in state.analyzed:
        r.setdefault("match_score", 0)
        r.setdefault("positive_rate", 0)
        r.setdefault("reason", "評論較少，以評分與人氣為主推薦")

    w = agent.weights
    print("[ranking_node] 權重設定：", w)

    def score(x):
        try:
            return (
                w["match_score"] * float(x.get("match_score", 0.0) or 0.0) +
                w["positive_rate"] * float(x.get("positive_rate", 0.0) or 0.0) +
                (float(x.get("rating", 0.0) or 0.0) / 5.0) * w["rating"]
            )
        except Exception as e:
            print("[ranking_node] 計算分數時錯誤：", e)
            return 0.0

    ranked = sorted(
        state.analyzed,
        key=lambda x: score(x),
        reverse=True
    )

    print("[ranking_node] 排名後前 3 間：")
    for r in ranked[:3]:
        print("   -", r.get("name"), "總分 =", score(r))

    recommendations = ranked[:3]  # 先存起來

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        save_json.invoke({
            "data": {"timestamp": timestamp, "recommendations": ranked},
            "path": os.path.join(agent.recommendations_dir, f"all_{timestamp}.json")
        })
        print("[ranking_node] 已儲存完整排名結果")
    except Exception as e:
        print("[ranking_node] 儲存排名結果失敗：", e)

    # ★★★ 關鍵是把結果回傳出去，不要只改 state ★★★
    return {"next": "response_node", "recommendations": recommendations}



def response_node(state):
    msg = "美食推薦結果：\n\n"
    medals = ["第一名", "第二名", "第三名"]

    recs = state.recommendations or []
    if not recs:
        print("[response_node] 沒有收到 recommendations")
        return {"next": END, "message": "目前沒有可用的推薦結果，請換個條件再試一次。"}

    msg = "美食推薦結果：\n\n"
    medals = ["第一名", "第二名", "第三名"]

    for i, r in enumerate(recs):
        msg += f"{medals[i]}：{r['name']}  評分 {r['rating']}\n"
        msg += f"地址：{r['address']}\n"
        msg += f"地圖連結：{r['map_url']}\n"
        msg += f"推薦理由：{r['reason']}\n\n"

    return {"next": END, "message": msg}


# ============ Graph Builder ============

def build_recommend_graph():
    g = StateGraph(RecommendState)
    g.add_node("parse_user_input_node", parse_user_input_node)
    g.add_node("place_search_node", place_search_node)
    g.add_node("review_fetch_node", review_fetch_node)
    g.add_node("analysis_node", analysis_node)
    g.add_node("ranking_node", ranking_node)
    g.add_node("response_node", response_node)

    g.add_edge(START, "parse_user_input_node")
    g.add_conditional_edges("parse_user_input_node", lambda s: s.next)
    g.add_conditional_edges("place_search_node", lambda s: s.next)
    g.add_conditional_edges("review_fetch_node", lambda s: s.next)
    g.add_conditional_edges("analysis_node", lambda s: s.next)
    g.add_conditional_edges("ranking_node", lambda s: s.next)
    g.add_edge("response_node", END)
    return g
