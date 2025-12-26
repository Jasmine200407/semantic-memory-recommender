"""
RecommendAgent 
清晰的逐步收集：地點 + 類型 → 確認 → 搜尋

模組說明：
本模組實現了一個基於 LangGraph 的餐廳推薦代理系統，主要功能包括：
1. 解析使用者輸入的餐廳需求（地點、類型、偏好）
2. 透過 Google Places API 搜尋餐廳
3. 爬取並分析餐廳評論
4. 使用 NLP 進行語意分析和匹配度計算
5. 根據多維度評分進行餐廳排序
6. 提供個人化的餐廳推薦結果

系統架構採用狀態圖（StateGraph）設計，將整個推薦流程拆分為多個節點，
每個節點負責特定的處理邏輯，透過條件路由實現靈活的流程控制。
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
    """
    新增或更新餐廳資料至資料庫
    
    功能說明：
    - 根據 place_id 檢查餐廳是否已存在
    - 若不存在則新增，若存在則更新資訊
    - 更新 last_update 時間戳記以追蹤資料新鮮度
    
    參數：
        info: 包含餐廳資訊的字典，必須包含 place_id
        
    返回：
        Restaurant: 更新後的餐廳物件，若操作失敗則返回 None
        
    資料庫欄位：
        - place_id: Google Places API 的唯一識別碼
        - name: 餐廳名稱
        - address: 地址
        - rating: 平均評分（1-5）
        - user_ratings_total: 總評論數
        - phone: 聯絡電話
        - website: 官方網站
        - map_url: Google Maps 連結
        - last_update: 最後更新時間（UTC）
    """
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
    """
    檢查並取得快取的餐廳評論（若資料夠新鮮）
    
    快取策略說明：
    - 透過 last_update 時間判斷資料新鮮度
    - 若距離上次更新未超過 cache_days 天數，則使用快取資料
    - 若超過時限或無快取，則返回 None 以觸發重新爬取
    
    參數：
        place_id: Google Places API 的餐廳唯一識別碼
        cache_days: 快取有效天數，超過此天數將不使用快取
        
    返回：
        List[Dict]: 評論列表，每個評論包含 text 和 stars 欄位
        None: 無可用快取或資料已過期
        
    效能優化：
    - 減少重複爬取，降低 API 呼叫次數
    - 提升系統回應速度
    - 節省網路頻寬和運算資源
    """
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
    """
    替換資料庫中特定餐廳的所有評論
    
    操作流程：
    1. 根據 place_id 找到對應的餐廳
    2. 刪除該餐廳所有舊評論
    3. 批次寫入新的評論資料
    4. 執行資料庫 commit 確保資料一致性
    
    參數：
        place_id: 餐廳的唯一識別碼
        reviews: 新的評論列表，每個評論需包含 text 和 stars
        
    設計考量：
    - 採用完整替換而非增量更新，確保資料一致性
    - 使用事務機制，失敗時自動回滾
    - 避免重複評論累積
    """
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
    """
    記錄推薦歷史至資料庫
    
    功能說明：
    - 儲存使用者的查詢條件和推薦結果
    - 記錄前三名推薦餐廳的 place_id
    - 保存完整的推薦資料（JSON 格式）
    
    參數：
        user_input: 使用者的原始輸入文字
        location: 搜尋地點
        category: 餐廳類型
        ranked: 排序後的完整餐廳列表
        
    用途：
    - 分析使用者偏好和行為模式
    - 追蹤推薦品質和準確度
    - 提供歷史查詢記錄
    - 支援 A/B 測試和模型優化
    """
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
    """
    判斷使用者輸入是否與餐廳推薦完全無關
    
    使用 Gemini LLM 進行意圖識別，區分以下情況：
    - 相關：提到吃飯、餐廳、食物、地點、料理類型等
    - 無關：打招呼、聊天、情緒表達、無關問題
    
    參數：
        text: 使用者的輸入文字
        
    返回：
        bool: True 表示無關，False 表示相關
        
    設計目的：
    - 過濾無效請求，提升系統效率
    - 引導使用者提供正確的輸入格式
    - 改善使用者體驗
    
    錯誤處理：
    - API 呼叫失敗時預設為 False（假設相關），避免誤判
    """
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
    解析使用者自然語言輸入，提取結構化資訊
    
    使用 Gemini LLM 進行自然語言理解（NLU），提取：
    1. location: 地點資訊（如：信義區、西門町）
    2. category: 餐廳類型（如：火鍋、壽司、義式料理）
    3. preferences: 使用者偏好列表（如：不吃辣、安靜環境）
    
    參數：
        user_input: 使用者的原始輸入文字
        
    返回：
        Dict: 包含 location, category, preferences 的字典
        None: 解析失敗時返回
        
    輸出格式：
    {
        "location": str | None,
        "category": str | None,
        "preferences": List[str]
    }
    
    處理流程：
    1. 使用 LLM 將自然語言轉換為結構化 JSON
    2. 移除 Markdown 格式標記（```json```）
    3. 提取有效的 JSON 字串
    4. 確保 preferences 為列表格式
    5. 處理空值和預設值
    
    錯誤處理：
    - JSON 解析失敗時返回 None
    - 容錯處理不完整的回應
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
    將使用者偏好分類為強制條件和加分項
    
    分類邏輯：
    - strong（強偏好）：必須滿足的硬性條件，用於過濾餐廳
      例如：不吃牛、不吃辣、素食、清真、不吃豬
    - weak（弱偏好）：軟性需求，用於排序加分
      例如：大份量、安靜環境、有停車位
    
    參數：
        prefs: 偏好文字列表
        
    返回：
        Dict: {"strong": [...], "weak": [...]}
        
    強偏好標準化代碼：
    - no_beef: 不吃牛肉
    - no_spicy: 不吃辣
    - vegetarian: 素食需求
    - halal: 清真認證
    - no_pork: 不吃豬肉
    
    設計考量：
    - 使用正則表達式進行彈性匹配
    - 支援多種表達方式（不吃/不能）
    - 標準化偏好代碼便於後續處理
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
    """
    餐廳推薦代理主類別
    
    負責協調整個推薦流程的核心邏輯：
    1. 批次爬取餐廳評論
    2. 管理快取機制
    3. 執行 NLP 分析
    4. 計算匹配度分數
    
    屬性：
        max_reviews: 每間餐廳最多爬取的評論數量（預設 80）
        cache_days: 評論快取有效天數（預設 30 天）
        weights: 排序權重配置
            - match_score: 偏好匹配度權重（0.7）
            - positive_rate: 正面評價比例權重（0.2）
            - rating: Google 評分權重（0.1）
    
    設計模式：
    - 單例模式：全域共用一個實例
    - 批次處理：使用執行緒池並行爬取評論
    - 快取優先：優先使用資料庫快取，降低 API 呼叫
    """
    def __init__(self):
        self.max_reviews = 80
        self.cache_days = 30
        self.weights = {"match_score": 0.7, "positive_rate": 0.2, "rating": 0.1}

    def fetch_single(self, restaurant: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        爬取單一餐廳的評論資料
        
        處理流程：
        1. 驗證必要欄位（name, place_id）
        2. 寫入或更新餐廳基本資訊
        3. 檢查快取是否可用
        4. 若無快取則執行網路爬取
        5. 將新評論寫入資料庫
        
        參數：
            restaurant: 餐廳資訊字典
            
        返回：
            Dict: {"restaurant": {...}, "reviews": [...]}
            None: 處理失敗時返回
            
        快取策略：
        - 快取命中：直接返回資料庫評論
        - 快取過期：重新爬取並更新快取
        - 爬取失敗：保留舊評論不變
        """
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
        """
        批次並行爬取多間餐廳的評論
        
        使用執行緒池實現並行處理，提升整體效能：
        - 最多 3 個並行執行緒（避免過度佔用資源）
        - 使用 Future 機制處理非同步結果
        - 個別餐廳失敗不影響其他餐廳
        
        參數：
            restaurants: 餐廳資訊列表
            
        返回：
            List[Dict]: 成功取得評論的餐廳資料列表
            
        效能優化：
        - 並行處理縮短總耗時
        - 執行緒池重用減少建立開銷
        - 容錯機制確保部分失敗不影響整體
        """
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
        """
        分析所有餐廳的評論並計算匹配度
        
        核心功能：
        1. 使用 NLP 模型分析評論內容
        2. 計算與使用者偏好的匹配度
        3. 統計正面評價比例
        4. 使用 LLM 生成推薦理由
        
        參數：
            review_batches: 包含餐廳和評論的批次資料
            prefs: 使用者偏好（strong 和 weak）
            
        返回：
            List[Dict]: 包含分析結果的餐廳列表，每間餐廳新增：
                - summary: 評論摘要
                - match_score: 偏好匹配分數（0-1）
                - positive_rate: 正面評價比例（0-1）
                - reason: AI 生成的推薦理由
        
        NLP 分析項目：
        - 語意理解：提取評論中的關鍵資訊
        - 情感分析：判斷正負面評價
        - 關鍵字匹配：與使用者偏好比對
        - 主題聚類：歸納餐廳特色
        
        錯誤處理：
        - NLP 失敗：使用預設分數（0.0）
        - 理由生成失敗：提供通用訊息
        """
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


# 全域單例實例
agent = RecommendAgent()


# ============================================================
# State 定義
# ============================================================

class RecommendState(BaseModel):
    """
    LangGraph 狀態管理類別
    
    定義推薦流程中的所有狀態變數，用於在不同節點間傳遞資訊：
    
    使用者輸入相關：
        user_input: 使用者的原始輸入文字
        location: 解析出的地點
        category: 解析出的餐廳類型
        preferences: 分類後的偏好 {"strong": [...], "weak": [...]}
    
    處理資料相關：
        restaurants: 搜尋到的餐廳列表
        review_batches: 爬取的評論批次資料
        analyzed: NLP 分析後的餐廳資料
        recommendations: 最終推薦結果（前 N 名）
        ranked: 完整的排序結果
    
    流程控制相關：
        next: 下一個要執行的節點名稱
        message: 要回傳給使用者的訊息
        waiting_for_confirmation: 是否等待使用者確認
        waiting_for_preference: 是否等待使用者輸入偏好
    
    設計模式：
    - 不可變性：使用 Pydantic BaseModel 確保型別安全
    - 狀態隔離：每個請求獨立的狀態實例
    - 可追蹤性：保留完整的處理歷程
    """
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
    
    # 流程追蹤標誌
    waiting_for_confirmation: bool = False
    waiting_for_preference: bool = False


# ============================================================
# 流程節點定義
# ============================================================

def parse_input_node(state: RecommendState) -> Dict[str, Any]:
    """
    解析使用者輸入節點
    
    職責：
    1. 檢查目前流程狀態（是否在等待確認或偏好輸入）
    2. 判斷輸入是否與餐廳推薦相關
    3. 解析地點、類型、偏好資訊
    4. 根據完整度決定下一步動作
    
    流程分支：
    - 等待確認中 → 轉到確認回應節點
    - 等待偏好中 → 轉到偏好回應節點
    - 完全無關 → 結束並提示
    - 只有偏好 → 詢問地點和類型
    - 缺地點 → 詢問地點
    - 缺類型 → 詢問類型
    - 資訊完整 → 驗證地點
    
    狀態更新：
    - 更新 location, category, preferences
    - 設定 next 指向下一個節點
    - 產生適當的 message
    
    設計考量：
    - 漸進式資訊收集
    - 友善的使用者引導
    - 彈性的輸入格式
    """
    text = (state.user_input or "").strip()
    print(f"[parse_input_node] 輸入：{text}")
    
    # 檢查是否在等待確認狀態
    if state.waiting_for_confirmation:
        print("[parse_input_node] 偵測到等待確認狀態，轉到 confirm_response_node")
        return {"next": "confirm_response_node"}
    
    # 檢查是否在等待偏好輸入狀態
    if state.waiting_for_preference:
        print("[parse_input_node] 偵測到等待偏好狀態，轉到 preference_response_node")
        return {"next": "preference_response_node"}

    # 判斷是否完全無關
    if detect_non_food_intent(text):
        return {
            "next": "end",
            "message": "我只能幫你推薦餐廳喔！請告訴我想在哪裡吃什麼類型的餐廳～\n例如：「想在信義區吃火鍋」"
        }

    # 解析輸入
    data = parse_user_input(text)
    if not data:
        return {
            "next": "end",
            "message": "我不太懂你的意思，可以換個方式說嗎？\n例如：「想在信義區吃火鍋」"
        }

    new_location = data.get("location")
    new_category = data.get("category")
    new_preferences = data.get("preferences", [])

    # 分類偏好為強制和加分項
    classified_prefs = classify_preferences(new_preferences)

    # 更新狀態（只更新有值的部分）
    updates = {"preferences": classified_prefs}
    if new_location:
        updates["location"] = new_location
    if new_category:
        updates["category"] = new_category

    # 判斷目前資訊完整度
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

    # 情況4：資訊完整，進入驗證地點
    return {
        **updates,
        "next": "validate_location_node"
    }


def validate_location_node(state: RecommendState) -> Dict[str, Any]:
    """
    驗證地點範圍節點
    
    職責：
    - 檢查使用者提供的地點是否過於廣泛
    - 過大的範圍會導致搜尋結果不精準
    
    驗證邏輯：
    - 使用 location_is_too_large 函式檢查
    - 常見過大範圍：台北市、新北市、整個縣市
    - 合適範圍：行政區、商圈、街道
    
    處理分支：
    - 範圍過大 → 清除地點並要求重新輸入
    - 範圍合適 → 進入確認節點
    
    使用者體驗：
    - 明確告知問題（範圍太大）
    - 提供具體範例（信義區、西門町）
    - 保留類型和偏好資訊
    """
    loc = state.location
    print(f"[validate_location_node] 驗證地點：{loc}")

    if location_is_too_large(loc):
        return {
            "next": "end",
            "location": None,
            "message": f"「{loc}」範圍太大了，可以說得更具體一點嗎？\n例如：信義區、大安區、西門町附近"
        }

    # 地點合格，進入確認
    return {
        "next": "confirm_node"
    }


def confirm_node(state: RecommendState) -> Dict[str, Any]:
    """
    確認節點（轉接）
    
    職責：
    - 地點和類型都已確認
    - 轉接到詢問偏好節點
    
    設計說明：
    - 此節點為流程中繼點
    - 確保流程邏輯清晰
    - 便於未來擴充額外驗證
    """
    loc = state.location
    cat = state.category
    
    return {
        "next": "ask_preference_node"
    }


def ask_preference_node(state: RecommendState) -> Dict[str, Any]:
    """
    詢問使用者偏好節點
    
    職責：
    - 主動詢問使用者是否有特別偏好
    - 提供偏好範例引導輸入
    - 設定等待偏好輸入狀態
    
    偏好類型範例：
    - 飲食限制：不吃辣、不吃牛、吃素
    - 環境需求：安靜、有包廂、有停車位
    - 份量偏好：大份量、小份量
    - 價格考量：平價、高級
    
    狀態設定：
    - waiting_for_preference = True
    - 下次輸入將由 preference_response_node 處理
    
    使用者友善設計：
    - 提供多種偏好範例
    - 說明如何跳過（回答「沒有」或「開始搜尋」）
    """
    loc = state.location
    cat = state.category
    
    return {
        "next": "end",
        "waiting_for_preference": True,
        "message": f"好的！要搜尋「{loc}」的「{cat}」\n\n有什麼特別偏好嗎？\n例如：不吃辣、吃素、大份量、安靜環境\n\n（沒有的話請回答「沒有」或「開始搜尋」）"
    }


def preference_response_node(state: RecommendState) -> Dict[str, Any]:
    """
    處理偏好回應節點
    
    職責：
    - 解析使用者的偏好輸入
    - 更新狀態中的偏好資訊
    - 轉接到最終確認節點
    
    處理邏輯：
    1. 檢查使用者是否表示無偏好
    2. 若有偏好，重新解析並分類
    3. 更新 state.preferences
    4. 清除等待標誌
    
    無偏好關鍵字：
    - 中文：沒有、無、開始搜尋
    - 英文：no, none
    
    有偏好處理：
    - 使用 parse_user_input 重新解析
    - 使用 classify_preferences 分類
    - 更新為結構化格式
    
    容錯設計：
    - 解析失敗視為無偏好
    - 不阻斷流程繼續執行
    """
    text = (state.user_input or "").strip().lower()
    print(f"[preference_response_node] 收到偏好回應：{text}")
    
    # 使用者表示沒有偏好
    if text in ["沒有", "没有", "無", "无", "no", "none", "開始搜尋", "开始搜寻", "搜尋", "搜寻", "開始", "开始"]:
        return {
            "next": "final_confirm_node",
            "waiting_for_preference": False
        }
    
    # 使用者提供了偏好
    data = parse_user_input(state.user_input)
    
    if data and data.get("preferences"):
        prefs = classify_preferences(data.get("preferences", []))
        print(f"[preference_response_node] 解析到偏好：{prefs}")
        
        return {
            "next": "final_confirm_node",
            "preferences": prefs,
            "waiting_for_preference": False
        }
    
    # 解析失敗當作沒有偏好
    return {
        "next": "final_confirm_node",
        "waiting_for_preference": False
    }


def final_confirm_node(state: RecommendState) -> Dict[str, Any]:
    """
    最終確認節點
    
    職責：
    - 顯示完整的搜尋條件摘要
    - 包含地點、類型、偏好
    - 等待使用者最終確認
    
    顯示內容：
    - 地點：從 state.location 讀取
    - 類型：從 state.category 讀取
    - 偏好：合併 strong 和 weak 偏好
    
    狀態設定：
    - waiting_for_confirmation = True
    - 下次輸入由 confirm_response_node 處理
    
    訊息格式：
    確認要搜尋：
    地點：信義區
    類型：火鍋
    偏好：不吃辣, 大份量
    
    確定嗎？（是/否）
    
    使用者體驗：
    - 清晰呈現所有條件
    - 給予修改機會
    - 明確的確認指示
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
    處理確認回應節點
    
    職責：
    - 解析使用者的確認回應
    - 根據回應決定下一步動作
    
    回應分類：
    1. 確認搜尋：
       - 關鍵字：是、yes、ok、好、對、確定、嗯、恩
       - 動作：開始搜尋流程
       - 轉到：place_search_node
    
    2. 取消搜尋：
       - 關鍵字：否、不要、no、取消、不是
       - 動作：清除所有條件，重新開始
       - 狀態重置：location, category 設為 None
    
    3. 其他回應：
       - 視為要修改條件
       - 轉到：parse_input_node 重新解析
    
    狀態管理：
    - 所有分支都清除 waiting_for_confirmation
    - 確保不會卡在等待狀態
    
    設計考量：
    - 支援多語言確認關鍵字
    - 彈性處理使用者意圖
    - 提供友善的退出機制
    """
    text = (state.user_input or "").strip().lower()
    print(f"[confirm_response_node] 收到回應：{text}")
    
    # 確認要搜尋
    if text in ["是", "yes", "ok", "好", "對", "確定", "嗯", "恩"]:
        return {
            "next": "place_search_node",
            "waiting_for_confirmation": False
        }
    
    # 取消搜尋
    if text in ["否", "不要", "no", "取消", "不是"]:
        return {
            "next": "end",
            "location": None,
            "category": None,
            "waiting_for_confirmation": False,
            "message": "好的，已取消！請重新告訴我想在哪裡吃什麼類型的餐廳～"
        }
    
    # 其他回應視為要修改條件
    return {
        "next": "parse_input_node",
        "waiting_for_confirmation": False,
    }


# ============================================================
# 搜尋與分析節點（保持原有邏輯）
# ============================================================

def place_search_node(state: RecommendState) -> Dict[str, Any]:
    """
    餐廳搜尋節點
    
    職責：
    1. 呼叫 Google Places API 搜尋餐廳
    2. 根據強偏好進行過濾
    3. 將餐廳資訊寫入資料庫
    
    搜尋流程：
    Step 1: 執行地點和類型搜尋
    - 使用 search_restaurants 工具
    - 取得初步餐廳列表
    
    Step 2: 強偏好過濾
    - 不吃牛：排除名稱含「牛」的餐廳
    - 不吃辣：排除名稱含「辣」的餐廳
    - 素食：僅保留名稱含「素食」的餐廳
    - 清真：僅保留名稱含「清真」的餐廳
    - 不吃豬：排除名稱含「豬」的餐廳
    
    Step 3: 資料庫寫入
    - 批次寫入所有餐廳基本資訊
    - 更新 last_update 時間戳記
    
    過濾策略：
    - 強偏好為硬性條件，必須滿足
    - 使用正則表達式比對餐廳名稱
    - 過濾後若無結果，保留全部餐廳
    
    錯誤處理：
    - 搜尋無結果：提示換條件重試
    - 資料庫寫入失敗：不影響流程繼續
    
    設計考量：
    - 不進行地址過濾（已移除）
    - 信任 Google Places API 的結果
    - 強偏好優先於評分和評論
    """
    print("[place_search_node] location =", state.location, "category =", state.category)
    loc, cat = state.location, state.category
    
    # Step 1: 直接搜尋
    restaurants = search_restaurants(loc, cat)
    print("[place_search_node] 搜尋到餐廳數量：", len(restaurants))

    if not restaurants:
        return {"next": "end", "message": "找不到符合條件的餐廳，要不要換個地點或類型試試？"}

    # 不做任何地址過濾，保留全部搜尋結果

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

    # 強偏好過濾後若為空，使用全部結果
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
    """
    評論爬取節點
    
    職責：
    - 批次爬取所有餐廳的評論
    - 使用快取機制提升效能
    - 處理爬取失敗的情況
    
    處理流程：
    1. 檢查餐廳列表是否為空
    2. 呼叫 agent.fetch_reviews_batch 並行爬取
    3. 根據結果決定下一步
    
    結果分支：
    - 成功爬取評論 → 轉到 analysis_node
    - 完全失敗 → 使用原始餐廳列表直接排序
    
    快取策略：
    - 由 RecommendAgent 自動處理
    - 優先使用 30 天內的快取
    - 無快取或過期則重新爬取
    
    容錯設計：
    - 部分餐廳失敗不影響整體
    - 爬取失敗時仍可根據基本資訊排序
    - 確保使用者總能得到結果
    """
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
    """
    NLP 分析節點
    
    職責：
    - 使用自然語言處理分析評論內容
    - 計算偏好匹配度
    - 統計正面評價比例
    - 生成推薦理由
    
    分析項目：
    1. 語意理解
       - 提取評論關鍵資訊
       - 識別餐廳特色和優缺點
    
    2. 情感分析
       - 判斷評論正負面傾向
       - 計算整體滿意度
    
    3. 偏好匹配
       - 與使用者弱偏好比對
       - 計算匹配分數（0-1）
    
    4. 理由生成
       - 使用 LLM 生成個人化推薦理由
       - 結合餐廳特色和使用者偏好
    
    輸出資料：
    - summary: 評論摘要（餐廳特色總結）
    - match_score: 偏好匹配分數
    - positive_rate: 正面評價比例
    - reason: AI 生成的推薦理由
    
    錯誤處理：
    - 無評論資料：跳過分析直接排序
    - 分析失敗：使用預設值繼續流程
    """
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
    """
    餐廳排序節點
    
    職責：
    - 根據多維度指標計算綜合分數
    - 對餐廳進行排序
    - 記錄推薦結果至資料庫
    
    排序演算法：
    綜合分數 = 0.7 × 匹配度 + 0.2 × 正面率 + 0.1 × 評分
    
    權重說明：
    - match_score (0.7): 偏好匹配度，最重要的指標
    - positive_rate (0.2): 正面評價比例，次要指標
    - rating (0.1): Google 評分（標準化為 0-1），輔助指標
    
    加分機制：
    - 若摘要中包含弱偏好關鍵字，額外加 0.05 分
    - 弱偏好不強制過濾，僅用於加分
    
    輸出結果：
    - ranked: 完整排序列表（所有餐廳）
    - recommendations: 前 3 名推薦（展示給使用者）
    
    資料記錄：
    - 寫入 Recommendation 表格
    - 記錄查詢條件和結果
    - 供後續分析使用
    
    容錯設計：
    - 空列表：返回空推薦
    - 分數計算失敗：使用 0.0
    - 資料庫寫入失敗：不影響結果返回
    """
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
        """
        計算單一餐廳的綜合分數
        
        計算公式：
        base_score = w1 × match + w2 × positive + w3 × (rating / 5)
        final_score = base_score + bonus
        
        bonus 條件：
        - 評論摘要包含弱偏好關鍵字：+0.05
        
        錯誤處理：
        - 缺失欄位使用 0.0
        - 計算異常返回 0.0
        """
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
    """
    回應產生節點
    
    職責：
    - 產生最終推薦結果
    - 僅返回推薦資料，不產生聊天訊息
    
    輸出策略：
    - 有推薦：返回 recommendations 列表
    - 無推薦：返回錯誤訊息
    
    設計考量：
    - 避免在聊天室顯示制式推薦句
    - 推薦資料由前端自行格式化展示
    - 保持回應簡潔
    
    前端展示責任：
    - 餐廳卡片呈現
    - 評分和評論顯示
    - 地圖位置標記
    - 推薦理由說明
    """
    recs = state.recommendations or []
    if not recs:
        # 完全沒推薦才顯示錯誤訊息
        return {"next": "end", "message": "找不到符合條件的餐廳"}

    # 不回任何 message，避免聊天室顯示推薦句
    return {"next": "end", "recommendations": recs}

# ============================================================
# 路由函數
# ============================================================

def route_next(state: RecommendState) -> str:
    """
    動態路由函數
    
    職責：
    - 根據 state.next 決定流程走向
    - 實現 LangGraph 的條件分支
    
    路由邏輯：
    - state.next == "end" → 結束流程（END）
    - state.next == "node_name" → 跳轉到指定節點
    - state.next == None → 結束流程（預設行為）
    
    使用方式：
    - 所有節點都使用此函數作為條件邊
    - 節點通過返回 {"next": "..."} 控制流程
    
    設計優勢：
    - 統一的路由邏輯
    - 易於維護和除錯
    - 支援動態流程控制
    """
    next_node = state.next
    
    if next_node == "end":
        return END
    
    return next_node or END


# ============================================================
# Graph 構建函數
# ============================================================

def build_recommend_graph() -> StateGraph:
    """
    構建推薦流程狀態圖
    
    流程架構：
    START 
      ↓
    parse_input_node (解析輸入)
      ↓
    validate_location_node (驗證地點)
      ↓
    confirm_node (確認轉接)
      ↓
    ask_preference_node (詢問偏好)
      ↓
    preference_response_node (處理偏好)
      ↓
    final_confirm_node (最終確認)
      ↓
    confirm_response_node (處理確認)
      ↓
    place_search_node (搜尋餐廳)
      ↓
    review_fetch_node (爬取評論)
      ↓
    analysis_node (NLP 分析)
      ↓
    ranking_node (排序餐廳)
      ↓
    response_node (產生回應)
      ↓
    END
    
    節點說明：
    - parse_input_node: 解析使用者輸入，提取地點、類型、偏好
    - validate_location_node: 驗證地點範圍是否合適
    - confirm_node: 確認資訊完整性
    - ask_preference_node: 主動詢問使用者偏好
    - preference_response_node: 處理偏好輸入
    - final_confirm_node: 顯示搜尋條件摘要
    - confirm_response_node: 處理使用者確認
    - place_search_node: Google Places API 搜尋
    - review_fetch_node: 批次爬取評論
    - analysis_node: NLP 分析評論
    - ranking_node: 計算分數並排序
    - response_node: 產生最終推薦
    
    路由機制：
    - 所有節點使用 route_next 函數進行條件路由
    - 節點可以動態決定下一步
    - 支援循環和跳躍
    
    狀態管理：
    - 使用 RecommendState 在節點間傳遞資料
    - 每個節點更新部分狀態
    - 保持狀態不可變性
    
    返回：
        StateGraph: 完整的推薦流程狀態圖
    """
    g = StateGraph(RecommendState)

    # 添加所有節點
    g.add_node("parse_input_node", parse_input_node)
    g.add_node("validate_location_node", validate_location_node)
    g.add_node("confirm_node", confirm_node)
    g.add_node("ask_preference_node", ask_preference_node)
    g.add_node("preference_response_node", preference_response_node)
    g.add_node("final_confirm_node", final_confirm_node)
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
    g.add_conditional_edges("ask_preference_node", route_next)
    g.add_conditional_edges("preference_response_node", route_next)
    g.add_conditional_edges("final_confirm_node", route_next)
    g.add_conditional_edges("confirm_response_node", route_next)
    g.add_conditional_edges("place_search_node", route_next)
    g.add_conditional_edges("review_fetch_node", route_next)
    g.add_conditional_edges("analysis_node", route_next)
    g.add_conditional_edges("ranking_node", route_next)
    g.add_conditional_edges("response_node", route_next)

    return g