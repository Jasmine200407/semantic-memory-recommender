# -*- coding: utf-8 -*-
"""
RecommendAgent - 改進版流程
清晰的逐步收集：地點 + 類型 → 確認 → 搜尋
"""

import re
import json
import datetime
import concurrent.futures
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END

# 工具模組
from recommender.tools.place_info_tool import search_restaurants, location_is_too_large
from recommender.tools.review_scraper_tool import get_all_reviews
from recommender.tools.embedding_tool import analyze_reviews
from recommender.tools.gemini_tool import call_gemini, generate_reason

# 資料庫模型
from app.db import SessionLocal, Restaurant, Review, Recommendation


# ============================================================
# 資料庫輔助函式（保持不變）
# ============================================================

def upsert_restaurant_from_dict(info: Dict[str, Any]) -> Optional[Restaurant]:
    place_id = info.get("place_id")
    if not place_id:
        print("[upsert_restaurant_from_dict] 缺少 place_id，略過寫入餐廳資料")
        return None

    db = SessionLocal()
    try:
        restaurant = (
            db.query(Restaurant)
            .filter(Restaurant.place_id == place_id)
            .first()
        )
        if restaurant is None:
            restaurant = Restaurant(place_id=place_id)
            db.add(restaurant)

        restaurant.name = info.get("name")
        restaurant.address = info.get("address")
        restaurant.rating = info.get("rating")
        restaurant.user_ratings_total = info.get("user_ratings_total")
        restaurant.phone = info.get("phone")
        restaurant.website = info.get("website")
        restaurant.map_url = info.get("map_url")
        restaurant.last_update = datetime.datetime.utcnow()

        db.commit()
        db.refresh(restaurant)
        print(f"[upsert_restaurant_from_dict] 已寫入餐廳：{restaurant.name} ({place_id})")
        return restaurant
    except Exception as e:
        db.rollback()
        print("[upsert_restaurant_from_dict] 資料庫錯誤：", e)
        return None
    finally:
        db.close()


def get_cached_reviews_if_fresh(place_id: str, cache_days: int) -> Optional[List[Dict[str, Any]]]:
    db = SessionLocal()
    try:
        restaurant = (
            db.query(Restaurant)
            .filter(Restaurant.place_id == place_id)
            .first()
        )
        if restaurant is None or restaurant.last_update is None:
            print("[get_cached_reviews_if_fresh] 找不到餐廳或沒有 last_update，略過快取")
            return None

        diff_days = (datetime.datetime.utcnow() - restaurant.last_update).days
        print(
            f"[get_cached_reviews_if_fresh] {restaurant.name} 上次更新日：{restaurant.last_update.date()}，距今 {diff_days} 天"
        )
        if diff_days > cache_days:
            print(f"[get_cached_reviews_if_fresh] 已超過 {cache_days} 天，不使用快取")
            return None

        review_rows = db.query(Review).filter(Review.restaurant_id == restaurant.id).all()
        if not review_rows:
            print("[get_cached_reviews_if_fresh] 沒有評論紀錄，不使用快取")
            return None

        reviews = [
            {"text": row.text, "stars": row.stars}
            for row in review_rows
        ]
        print(f"[get_cached_reviews_if_fresh] 使用資料庫快取評論數量：{len(reviews)}")
        return reviews
    except Exception as e:
        print("[get_cached_reviews_if_fresh] 資料庫錯誤：", e)
        return None
    finally:
        db.close()


def replace_reviews_in_db(place_id: str, reviews: List[Dict[str, Any]]) -> None:
    if not place_id:
        print("[replace_reviews_in_db] 缺少 place_id，無法寫入評論")
        return

    db = SessionLocal()
    try:
        restaurant = (
            db.query(Restaurant)
            .filter(Restaurant.place_id == place_id)
            .first()
        )
        if restaurant is None:
            print("[replace_reviews_in_db] 找不到對應餐廳，略過評論寫入")
            return

        deleted = (
            db.query(Review)
            .filter(Review.restaurant_id == restaurant.id)
            .delete()
        )
        print(f"[replace_reviews_in_db] 已刪除舊評論數量：{deleted}")

        for rv in reviews:
            text = rv.get("text") or ""
            stars = rv.get("stars")
            db.add(Review(
                restaurant_id=restaurant.id,
                text=text,
                stars=stars,
            ))

        db.commit()
        print(f"[replace_reviews_in_db] 已寫入新評論數量：{len(reviews)}")
    except Exception as e:
        db.rollback()
        print("[replace_reviews_in_db] 資料庫錯誤：", e)
    finally:
        db.close()


def insert_recommendation_record(
    user_input: Optional[str],
    location: Optional[str],
    category: Optional[str],
    ranked: List[Dict[str, Any]],
) -> None:
    db = SessionLocal()
    try:
        top_ids = []
        for r in ranked[:3]:
            pid = r.get("place_id")
            if pid:
                top_ids.append(pid)

        record = Recommendation(
            user_input=user_input,
            location=location,
            category=category,
            top_place_ids=",".join(top_ids),
            recommendation_json=ranked,
        )

        db.add(record)
        db.commit()
        print("[insert_recommendation_record] 已寫入 Recommendation 紀錄")
    except Exception as e:
        db.rollback()
        print("[insert_recommendation_record] 資料庫錯誤：", e)
    finally:
        db.close()
# ============================================================
# NLP 解析函式
# ============================================================

def detect_non_food_intent(text: str) -> bool:
    """判斷是否與餐廳推薦完全無關"""
    prompt = f"""
    判斷以下訊息是否與尋找餐廳、吃飯、食物、地點、餐廳種類相關？
    僅回答 yes 或 no。

    使用者訊息: 「{text}」

    若屬於下列類型則回答 yes（表示無關）：
    - 打招呼 (嗨、哈囉、你好)
    - 聊天或生活狀況 (我好累、今天天氣好)
    - 心情分享 (好無聊、肚子痛)
    - 問候 (你在嗎、你是誰)
    - 與食物無關的問題 (你叫什麼名字、幾點了)
    - 只有情緒表達 (哈哈哈、哭哭、QQ)

    若訊息提到：吃東西、找餐廳、想吃什麼、地點、餐廳類型等 → 回答 no。
    僅輸出 yes 或 no。
    """
    try:
        result = call_gemini(prompt).strip().lower()
        return result.startswith("yes")
    except Exception:
        return False


def parse_user_input(user_input: str) -> Optional[Dict[str, Any]]:
    """
    解析使用者輸入，提取：
    - location: 地點
    - category: 餐廳類型
    - preferences: 偏好列表
    """
    prompt = f"""
    將以下使用者需求整理成 JSON：
    「{user_input}」
    
    回傳格式：
    {{
      "location": "地點（如果沒提到則為 null）",
      "category": "餐廳類型（如：火鍋、壽司、燒肉，沒提到則為 null）",
      "preferences": ["偏好1", "偏好2"]
    }}
    
    注意：
    - 如果使用者沒有明確提到地點，location 必須是 null
    - 如果使用者沒有明確提到餐廳類型，category 必須是 null
    - 如果沒有特別偏好，preferences 可以是空陣列 []
    
    僅輸出 JSON，不要其他文字。
    """
    try:
        raw = call_gemini(prompt).strip()
        print("[parse_user_input] Gemini 原始回傳：", raw)

        # 移除 markdown 標記
        raw = re.sub(r"```(?:json)?(.*?)```", r"\1", raw, flags=re.DOTALL).strip()

        # 提取 JSON
        start_idx = raw.find("{")
        end_idx = raw.rfind("}")
        if start_idx == -1 or end_idx == -1:
            raise ValueError("找不到有效的 JSON 區塊")

        json_str = raw[start_idx: end_idx + 1]
        data = json.loads(json_str)
        print("[parse_user_input] 解析後 JSON：", data)

        # 確保 preferences 是 list
        if isinstance(data.get("preferences"), str):
            data["preferences"] = [data["preferences"]]
        if not data.get("preferences"):
            data["preferences"] = []

        return data
    except Exception as e:
        print("[parse_user_input] 解析失敗：", e)
        return None


def classify_preferences(prefs: List[str]) -> Dict[str, List[str]]:
    """
    將偏好分類為 strong（強制過濾）和 weak（加分項）
    """
    strong = []
    weak = []

    for p in prefs:
        txt = p.lower()

        if re.search(r"(不吃|不能).*牛", txt):
            strong.append("no_beef")
        elif re.search(r"(不吃|不能).*辣", txt):
            strong.append("no_spicy")
        elif re.search(r"(素食|吃素|vegan|vegetarian)", txt):
            strong.append("vegetarian")
        elif re.search(r"(清真|halal)", txt):
            strong.append("halal")
        elif re.search(r"(不吃|不能).*豬", txt):
            strong.append("no_pork")
        else:
            weak.append(txt)

    return {"strong": strong, "weak": weak}


# ============================================================
# RecommendAgent（保持不變）
# ============================================================

class RecommendAgent:
    def __init__(self):
        self.max_reviews = 80
        self.cache_days = 30
        self.weights = {"match_score": 0.7, "positive_rate": 0.2, "rating": 0.1}

    def fetch_single(self, restaurant: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        name = restaurant.get("name")
        place_id = restaurant.get("place_id")
        print(f"[fetch_single] 應處理餐廳：{name} ({place_id})")

        if not name or not place_id:
            print("[fetch_single] 缺少名稱或 place_id，略過")
            return None

        upsert_restaurant_from_dict(restaurant)

        cache = get_cached_reviews_if_fresh(place_id, self.cache_days)
        if cache:
            print(f"[fetch_single] 使用資料庫快取：{name}，評論數：{len(cache)}")
            return {"restaurant": restaurant, "reviews": cache}

        print(f"[fetch_single] 無可用快取，開始爬取：{name}")
        reviews = get_all_reviews(name, place_id, max_reviews=self.max_reviews) or []
        print(f"[fetch_single] {name} 實際抓到評論數：{len(reviews)}")

        if reviews:
            replace_reviews_in_db(place_id, reviews)
        else:
            print(f"[fetch_single] {name} 沒有成功取得評論，資料庫評論維持不變")

        return {"restaurant": restaurant, "reviews": reviews}

    def fetch_reviews_batch(self, restaurants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        print(f"[fetch_reviews_batch] 準備處理餐廳數量：{len(restaurants)}")
        results: List[Dict[str, Any]] = []

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

    def analyze_results(self, review_batches: List[Dict[str, Any]], prefs: Dict[str, List[str]]) -> List[Dict[str, Any]]:
        print("[analyze_results] 進來的餐廳數量：", len(review_batches))
        print("[analyze_results] 使用者偏好（含 strong/weak）：", prefs)

        weak = prefs.get("weak", [])

        output: List[Dict[str, Any]] = []
        for rb in review_batches:
            r = rb["restaurant"]
            reviews = rb["reviews"]
            print(f"[analyze_results] 處理餐廳：{r.get('name')}，評論數：{len(reviews)}")

            try:
                res = analyze_reviews(reviews, weak)
                print(
                    f"[analyze_results] NLP 結果：match={res.get('match_score')}, "
                    f"positive_rate={res.get('positive_rate')}"
                )
            except Exception as e:
                print("[analyze_results] analyze_reviews 發生錯誤：", e)
                res = {"summary": "", "match_score": 0.0, "positive_rate": 0.0}

            try:
                reason_text = generate_reason(r["name"], res.get("summary", ""), weak)
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


# ============================================================
# State 定義
# ============================================================

class RecommendState(BaseModel):
    user_input: Optional[str] = None
    location: Optional[str] = None
    category: Optional[str] = None
    preferences: Optional[Dict[str, List[str]]] = None

    restaurants: Optional[List[Dict[str, Any]]] = None
    review_batches: Optional[List[Dict[str, Any]]] = None
    analyzed: Optional[List[Dict[str, Any]]] = None
    recommendations: Optional[List[Dict[str, Any]]] = None
    ranked: List[Dict[str, Any]] = Field(default_factory=list)
    
    next: Optional[str] = None
    message: Optional[str] = None
    
    # ★ 新增：追蹤目前處於哪個階段
    waiting_for_confirmation: bool = False
    waiting_for_preference: bool = False  # ★ 等待偏好輸入


# ============================================================
# ★★★ 新流程節點 ★★★
# ============================================================

def parse_input_node(state: RecommendState) -> Dict[str, Any]:
    """
    解析使用者輸入，判斷四種情況：
    1. 完全無關 → 提醒使用者
    2. 只有偏好 → 詢問地點和類型
    3. 缺地點 → 詢問地點
    4. 缺類型 → 詢問類型
    5. 都有 → 驗證地點
    """
    text = (state.user_input or "").strip()
    print(f"[parse_input_node] 輸入：{text}")
    
    # ★ 如果正在等待確認，則轉到 confirm_response_node
    if state.waiting_for_confirmation:
        print("[parse_input_node] 偵測到等待確認狀態，轉到 confirm_response_node")
        return {"next": "confirm_response_node"}
    
    # ★ 如果正在等待偏好，則轉到 preference_response_node
    if state.waiting_for_preference:
        print("[parse_input_node] 偵測到等待偏好狀態，轉到 preference_response_node")
        return {"next": "preference_response_node"}

    # 1️⃣ 判斷是否完全無關
    if detect_non_food_intent(text):
        return {
            "next": "end",
            "message": "我只能幫你推薦餐廳喔！請告訴我想在哪裡吃什麼類型的餐廳～\n例如：「想在信義區吃火鍋」"
        }

    # 2️⃣ 解析輸入
    data = parse_user_input(text)
    if not data:
        return {
            "next": "end",
            "message": "我不太懂你的意思，可以換個方式說嗎？\n例如：「想在信義區吃火鍋」"
        }

    new_location = data.get("location")
    new_category = data.get("category")
    new_preferences = data.get("preferences", [])

    # 分類偏好
    classified_prefs = classify_preferences(new_preferences)

    # 更新 state（只更新有值的部分）
    updates = {"preferences": classified_prefs}
    if new_location:
        updates["location"] = new_location
    if new_category:
        updates["category"] = new_category

    # 3️⃣ 判斷目前狀態
    current_location = updates.get("location") or state.location
    current_category = updates.get("category") or state.category

    print(f"[parse_input_node] 目前狀態 - 地點:{current_location}, 類型:{current_category}")

    # 情況1：只有偏好（沒有地點也沒有類型）
    if not current_location and not current_category:
        return {
            **updates,
            "next": "end",
            "message": "想在哪裡吃什麼類型的餐廳呢？\n例如：「信義區的火鍋」或「西門町日本料理」"
        }

    # 情況2：只有類型，缺地點
    if current_category and not current_location:
        return {
            **updates,
            "next": "end",
            "message": f"想在哪裡吃{current_category}呢？\n例如：信義區、大安區、西門町"
        }

    # 情況3：只有地點，缺類型
    if current_location and not current_category:
        return {
            **updates,
            "next": "end",
            "message": f"想在{current_location}吃什麼類型的餐廳呢？\n例如：火鍋、壽司、燒肉、義式料理"
        }

    # 情況4：都有了，進入驗證地點
    return {
        **updates,
        "next": "validate_location_node"
    }


def validate_location_node(state: RecommendState) -> Dict[str, Any]:
    """
    驗證地點是否過大
    - 過大 → 重新詢問地點
    - 合格 → 進入確認節點
    """
    loc = state.location
    print(f"[validate_location_node] 驗證地點：{loc}")

    if location_is_too_large(loc):
        return {
            "next": "end",
            "location": None,  # 清除過大的地點
            "message": f"「{loc}」範圍太大了，可以說得更具體一點嗎？\n例如：信義區、大安區、西門町附近"
        }

    # 地點合格，進入確認
    return {
        "next": "confirm_node"
    }


def confirm_node(state: RecommendState) -> Dict[str, Any]:
    """
    地點和類型都有了，先詢問偏好
    """
    loc = state.location
    cat = state.category
    
    return {
        "next": "ask_preference_node"
    }


def ask_preference_node(state: RecommendState) -> Dict[str, Any]:
    """
    詢問使用者是否有特別偏好
    """
    loc = state.location
    cat = state.category
    
    return {
        "next": "end",
        "waiting_for_preference": True,  # ★ 進入等待偏好狀態
        "message": f"好的！要搜尋「{loc}」的「{cat}」\n\n有什麼特別偏好嗎？\n例如：不吃辣、吃素、大份量、安靜環境\n\n（沒有的話請回答「沒有」或「開始搜尋」）"
    }


def preference_response_node(state: RecommendState) -> Dict[str, Any]:
    """
    處理使用者的偏好回應
    """
    text = (state.user_input or "").strip().lower()
    print(f"[preference_response_node] 收到偏好回應：{text}")
    
    # 使用者表示沒有偏好 → 直接搜尋
    if text in ["沒有", "没有", "無", "无", "no", "none", "開始搜尋", "开始搜寻", "搜尋", "搜寻", "開始", "开始"]:
        return {
            "next": "final_confirm_node",
            "waiting_for_preference": False
        }
    
    # 使用者說了偏好 → 解析並記錄
    # 重新解析整句話，提取偏好
    data = parse_user_input(state.user_input)
    
    if data and data.get("preferences"):
        prefs = classify_preferences(data.get("preferences", []))
        print(f"[preference_response_node] 解析到偏好：{prefs}")
        
        return {
            "next": "final_confirm_node",
            "preferences": prefs,
            "waiting_for_preference": False
        }
    
    # 解析失敗 → 也當作沒有偏好
    return {
        "next": "final_confirm_node",
        "waiting_for_preference": False
    }


def final_confirm_node(state: RecommendState) -> Dict[str, Any]:
    """
    最終確認：顯示地點、類型、偏好，讓使用者確認
    """
    loc = state.location
    cat = state.category
    prefs = state.preferences or {"weak": [], "strong": []}
    
    # 組合偏好文字
    pref_text = ""
    all_prefs = prefs.get("strong", []) + prefs.get("weak", [])
    if all_prefs:
        pref_text = f"\n偏好：{', '.join(all_prefs)}"
    
    return {
        "next": "end",
        "waiting_for_confirmation": True,
        "message": f"確認要搜尋：\n地點：{loc}\n類型：{cat}{pref_text}\n\n確定嗎？（是/否）"
    }


def confirm_response_node(state: RecommendState) -> Dict[str, Any]:
    """
    處理使用者的確認回應
    """
    text = (state.user_input or "").strip().lower()
    print(f"[confirm_response_node] 收到回應：{text}")
    
    # 確認要搜尋
    if text in ["是", "yes", "ok", "好", "對", "確定", "嗯", "恩"]:
        return {
            "next": "place_search_node",
            "waiting_for_confirmation": False  # ★ 清除等待狀態
        }
    
    # 取消搜尋
    if text in ["否", "不要", "no", "取消", "不是"]:
        return {
            "next": "end",
            "location": None,
            "category": None,
            "waiting_for_confirmation": False,  # ★ 清除等待狀態
            "message": "好的，已取消！請重新告訴我想在哪裡吃什麼類型的餐廳～"
        }
    
    # 其他回應 → 視為要修改，回到 parse
    return {
        "next": "parse_input_node",
        "waiting_for_confirmation": False,  # ★ 清除等待狀態
    }


# ============================================================
# 搜尋與分析節點（保持原有邏輯）
# ============================================================

def place_search_node(state: RecommendState) -> Dict[str, Any]:
    """搜尋餐廳並進行強偏好過濾"""
    print("[place_search_node] location =", state.location, "category =", state.category)
    loc, cat = state.location, state.category
    
    # Step 1: 直接搜尋
    restaurants = search_restaurants(loc, cat)
    print("[place_search_node] 搜尋到餐廳數量：", len(restaurants))

    if not restaurants:
        return {"next": "end", "message": "找不到符合條件的餐廳，要不要換個地點或類型試試？"}

    # ❌ 不做任何地址過濾，保留全部搜尋結果

    # Step 2: 強偏好過濾
    strong = (state.preferences or {}).get("strong", [])
    filtered = []
    for r in restaurants:
        name = (r.get("name") or "").lower()

        if "no_beef" in strong and re.search(r"(牛|和牛|牛排)", name):
            continue
        if "no_spicy" in strong and re.search(r"(辣|麻辣|辣子|辣醬)", name):
            continue
        if "vegetarian" in strong and not re.search(r"(素食|蔬食|vegan|vegetarian)", name):
            continue
        if "halal" in strong and not re.search(r"(清真|halal)", name):
            continue
        if "no_pork" in strong and re.search(r"(豬|豬肉)", name):
            continue

        filtered.append(r)

    # 強偏好後若為空 → 使用全部
    if not filtered:
        filtered = restaurants

    # 資料庫寫入
    for r in filtered:
        upsert_restaurant_from_dict(r)

    return {
        "next": "review_fetch_node", 
        "restaurants": filtered
    }

def review_fetch_node(state: RecommendState) -> Dict[str, Any]:
    """批次抓取評論"""
    restaurants = state.restaurants or []
    print("[review_fetch_node] 餐廳數量：", len(restaurants))

    if not restaurants:
        return {"next": "end", "message": "找不到相關餐廳"}

    results = agent.fetch_reviews_batch(restaurants)
    print("[review_fetch_node] fetch_reviews_batch 結果數量：", len(results))

    if not results:
        analyzed = restaurants
        print("[review_fetch_node] 沒有成功取得評論，改用原始餐廳清單作 ranking")
        return {
            "next": "ranking_node",
            "analyzed": analyzed
        }

    return {
        "next": "analysis_node",
        "review_batches": results
    }


def analysis_node(state: RecommendState) -> Dict[str, Any]:
    """NLP 分析評論"""
    print("[analysis_node] review_batches 數量：", len(state.review_batches or []))
    if not state.review_batches:
        analyzed = state.analyzed or []
        return {"next": "ranking_node", "analyzed": analyzed}

    analyzed = agent.analyze_results(
        state.review_batches, 
        state.preferences or {"weak": [], "strong": []}
    )
    print("[analysis_node] 分析後餐廳數量：", len(analyzed or []))

    return {
        "next": "ranking_node", 
        "analyzed": analyzed
    }


def ranking_node(state: RecommendState) -> Dict[str, Any]:
    """排序餐廳"""
    print("[ranking_node] analyzed 數量：", len(state.analyzed or []))

    if not state.analyzed:
        return {
            "next": "response_node",
            "recommendations": [],
            "ranked": []
        }

    ranked = state.analyzed
    weights = {"match_score": 0.7, "positive_rate": 0.2, "rating": 0.1}
    weak = (state.preferences or {}).get("weak", [])

    def score(r: Dict) -> float:
        try:
            base = (
                weights["match_score"] * float(r.get("match_score") or 0.0)
                + weights["positive_rate"] * float(r.get("positive_rate") or 0.0)
                + weights["rating"] * (float(r.get("rating") or 0.0) / 5.0)
            )
            summary = (r.get("summary") or "").lower()
            if any(w in summary for w in weak):
                base += 0.05
            return base
        except Exception:
            return 0.0

    ranked_sorted = sorted(ranked, key=score, reverse=True)

    insert_recommendation_record(
        user_input=state.user_input,
        location=state.location,
        category=state.category,
        ranked=ranked_sorted,
    )

    print("[ranking_node] 排序完成，TOP3 為：", [r.get("name") for r in ranked_sorted[:3]])
    
    return {
        "next": "response_node",
        "ranked": ranked_sorted,
        "recommendations": ranked_sorted[:3]
    }


def response_node(state: RecommendState) -> Dict[str, Any]:
    """產生最終推薦訊息（只傳推薦 data，不傳聊天訊息）"""
    recs = state.recommendations or []
    if not recs:
        # ⚠ 如果完全沒推薦才顯示錯誤訊息
        return {"next": "end", "message": "找不到符合條件的餐廳"}

    # ✔ 不回任何 message，避免聊天室顯示推薦句
    return {"next": "end", "recommendations": recs}

# ============================================================
# 路由函數
# ============================================================

def route_next(state: RecommendState) -> str:
    """根據 state.next 決定下一步"""
    next_node = state.next
    
    if next_node == "end":
        return END
    
    return next_node or END


# ============================================================
# ★★★ Graph Builder - 新流程 ★★★
# ============================================================

def build_recommend_graph() -> StateGraph:
    """
    新流程：
    START → parse_input_node → validate_location_node → confirm_node
          → ask_preference_node → preference_response_node 
          → final_confirm_node → confirm_response_node → place_search_node → ...
    """
    g = StateGraph(RecommendState)

    # 添加節點
    g.add_node("parse_input_node", parse_input_node)
    g.add_node("validate_location_node", validate_location_node)
    g.add_node("confirm_node", confirm_node)
    g.add_node("ask_preference_node", ask_preference_node)  # ★ 新增
    g.add_node("preference_response_node", preference_response_node)  # ★ 新增
    g.add_node("final_confirm_node", final_confirm_node)  # ★ 新增
    g.add_node("confirm_response_node", confirm_response_node)
    g.add_node("place_search_node", place_search_node)
    g.add_node("review_fetch_node", review_fetch_node)
    g.add_node("analysis_node", analysis_node)
    g.add_node("ranking_node", ranking_node)
    g.add_node("response_node", response_node)

    # 設定起點
    g.add_edge(START, "parse_input_node")

    # 使用條件邊實現動態路由
    g.add_conditional_edges("parse_input_node", route_next)
    g.add_conditional_edges("validate_location_node", route_next)
    g.add_conditional_edges("confirm_node", route_next)
    g.add_conditional_edges("ask_preference_node", route_next)  # ★ 新增
    g.add_conditional_edges("preference_response_node", route_next)  # ★ 新增
    g.add_conditional_edges("final_confirm_node", route_next)  # ★ 新增
    g.add_conditional_edges("confirm_response_node", route_next)
    g.add_conditional_edges("place_search_node", route_next)
    g.add_conditional_edges("review_fetch_node", route_next)
    g.add_conditional_edges("analysis_node", route_next)
    g.add_conditional_edges("ranking_node", route_next)
    g.add_conditional_edges("response_node", route_next)

    return g