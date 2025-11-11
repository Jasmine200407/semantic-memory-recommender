"""
🗺️ place_info_tool.py
---------------------------------
功能：
- 搜尋餐廳基本資料（使用 Google Places API）
- 檢查地點是否過大
- 用於 recommend_agent 流程的地點資料來源
---------------------------------
"""

import os
import requests
from dotenv import load_dotenv

# ────────────────────────────────
# ⚙️ 初始化環境變數
# ────────────────────────────────
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_PLACE_API_KEY")

if not GOOGLE_API_KEY:
    raise ValueError("❌ GOOGLE_API_KEY 未設定，請在 .env 或系統環境變數中設置。")

# ────────────────────────────────
# 📍 檢查地點是否過大
# ────────────────────────────────
import requests
import os

def location_is_too_large(location: str) -> bool:
    """
    根據地點的經緯度範圍判斷是否過大。
    若查到的地理邊界差距（lat/lng）任一超過 0.2 度，視為範圍過廣。
    若 API 請求逾時或失敗，則回傳 False（避免中斷流程）。
    """
    if not location:
        return True

    api_key = os.getenv("GOOGLE_PLACE_API_KEY")
    if not api_key:
        print("⚠️ 未設定 GOOGLE_API_KEY，跳過範圍檢查。")
        return False

    try:
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={location}&key={api_key}"
        resp = requests.get(url, timeout=10)  # ✅ 設定 10 秒 timeout
        data = resp.json()

        if data.get("status") != "OK" or not data.get("results"):
            print(f"⚠️ 無法解析地點：{location}")
            return True  # 若地點模糊或無效則視為太廣

        geometry = data["results"][0].get("geometry", {})
        viewport = geometry.get("viewport")

        if viewport:
            lat_diff = abs(viewport["northeast"]["lat"] - viewport["southwest"]["lat"])
            lng_diff = abs(viewport["northeast"]["lng"] - viewport["southwest"]["lng"])
            print(f"📏 範圍差距 lat={lat_diff:.3f}, lng={lng_diff:.3f}")

            return lat_diff > 0.2 or lng_diff > 0.2

        return False

    except requests.exceptions.ReadTimeout:
        print("⏰ Google API 連線逾時，略過範圍檢查。")
        return False

    except Exception as e:
        print(f"❌ 檢查地點範圍時發生錯誤：{e}")
        return False

# ────────────────────────────────
# 🍽️ 搜尋餐廳
# ────────────────────────────────
def search_restaurants(location: str, category: str, radius: int = 2000, max_results: int = 10):
    """
    使用 Google Places Text Search API 搜尋餐廳資訊。
    
    Args:
        location (str): 使用者指定的地點（例如「信義區」）
        category (str): 餐廳主題（例如「火鍋」、「早午餐」）
        radius (int): 搜尋範圍（公尺）
        max_results (int): 取回的最大餐廳數量

    Returns:
        list[dict]: 餐廳資訊列表，每筆包含名稱、ID、評分、地址與地圖連結。
    """
    query = f"{location} {category} 餐廳"
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "type": "restaurant",
        "language": "zh-TW",
        "key": GOOGLE_API_KEY,
    }

    try:
        response = requests.get(url, params=params)
        data = response.json()
        status = data.get("status")

        if status != "OK":
            print(f"⚠️ Google Places API 錯誤：{status}")
            return []

        restaurants = []
        for item in data.get("results", [])[:max_results]:
            restaurants.append({
                "name": item.get("name"),
                "place_id": item.get("place_id"),
                "rating": item.get("rating", 0),
                "user_ratings_total": item.get("user_ratings_total", 0),
                "address": item.get("formatted_address", ""),
                "map_url": f"https://www.google.com/maps/place/?q=place_id:{item.get('place_id')}",
            })

        return restaurants

    except Exception as e:
        print(f"❌ 餐廳搜尋失敗：{e}")
        return []


# ────────────────────────────────
# 🧪 測試執行（開發時用）
# ────────────────────────────────
if __name__ == "__main__":
    location = "信義區"
    category = "火鍋"
    print(f"🔍 測試搜尋：{location} 的 {category} 餐廳...")
    results = search_restaurants(location, category)
    print(f"共找到 {len(results)} 間：")
    for r in results:
        print(f"- {r['name']}（⭐ {r['rating']}）→ {r['map_url']}")
