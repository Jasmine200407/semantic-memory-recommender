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
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type, Optional

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
def location_is_too_large(location: str) -> bool:
    """
    根據地點的經緯度範圍判斷是否過大。
    若查到的地理邊界差距（lat/lng）任一超過 0.2 度，視為範圍過廣。
    若 API 請求逾時或失敗，則回傳 False（避免中斷流程）。
    """
    if not location:
        return True

    api_key = GOOGLE_API_KEY
    if not api_key:
        print("⚠️ 未設定 GOOGLE_API_KEY，跳過範圍檢查。")
        return False

    try:
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={location}&key={api_key}"
        resp = requests.get(url, timeout=10)  # 🕒 加入 timeout
        data = resp.json()

        if data.get("status") != "OK" or not data.get("results"):
            print(f"⚠️ 無法解析地點：{location}（status={data.get('status')}）")  # 📝 加上 log
            return True

        geometry = data["results"][0].get("geometry", {})
        viewport = geometry.get("viewport")

        if viewport:
            lat_diff = abs(viewport["northeast"]["lat"] - viewport["southwest"]["lat"])
            lng_diff = abs(viewport["northeast"]["lng"] - viewport["southwest"]["lng"])
            print(f"📏 範圍差距 lat={lat_diff:.3f}, lng={lng_diff:.3f}")

            return lat_diff > 0.2 or lng_diff > 0.2

        return False

    except requests.exceptions.ReadTimeout:
        print("⏰ Google API 連線逾時，略過範圍檢查。")  # 📝 timeout log
        return False

    except Exception as e:
        print(f"❌ 檢查地點範圍時發生錯誤：{e}")
        return False


# ────────────────────────────────
# 🍽️ 搜尋餐廳
# ────────────────────────────────
import requests
def search_restaurants(location: str, category: str, radius: int = 2000, max_results: int = 10):
    geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
    geo_params = {
        "address": location,
        "key": GOOGLE_API_KEY,
        "language": "zh-TW"
    }
    try:
        geo_res = requests.get(geocode_url, params=geo_params, timeout=10).json()  # 🕒 timeout
    except requests.exceptions.ReadTimeout:
        print(f"⏰ 地理編碼逾時：{location}")  # 📝 timeout log
        return []
    except Exception as e:
        print(f"❌ 地理編碼失敗：{e}")
        return []

    if geo_res.get("status") != "OK":
        print(f"⚠️ 地理編碼失敗：{geo_res.get('status')}")  # 📝 status log
        return []

    lat = geo_res["results"][0]["geometry"]["location"]["lat"]
    lng = geo_res["results"][0]["geometry"]["location"]["lng"]

    nearby_url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    nearby_params = {
        "location": f"{lat},{lng}",
        "radius": radius,
        "keyword": category,
        "type": "restaurant",
        "key": GOOGLE_API_KEY,
        "language": "zh-TW"
    }

    try:
        res = requests.get(nearby_url, params=nearby_params, timeout=10).json()  # 🕒 timeout
    except requests.exceptions.ReadTimeout:
        print(f"⏰ 餐廳搜尋逾時：{location} {category}")
        return []
    except Exception as e:
        print(f"❌ 餐廳搜尋錯誤：{e}")
        return []

    status = res.get("status")
    if status == "OVER_QUERY_LIMIT":
        print("🚫 API 超出額度，請檢查計費或配額！")  # 📝 log
        return []
    if status != "OK":
        print(f"⚠️ 餐廳搜尋失敗：{status}")  # 📝 log
        return []

    restaurants = []
    for item in res.get("results", [])[:max_results]:
        place_id = item.get("place_id")
        if not place_id:
            continue

        # 🔍 補全 Place Details 拿完整資料
        details_url = "https://maps.googleapis.com/maps/api/place/details/json"
        details_params = {
            "place_id": place_id,
            "fields": "formatted_address,formatted_phone_number,website,opening_hours,price_level,url",
            "language": "zh-TW",
            "key": GOOGLE_API_KEY
        }
        try:
            details_res = requests.get(details_url, params=details_params, timeout=10).json()  # 🕒 timeout
        except requests.exceptions.ReadTimeout:
            print(f"⏰ Details 逾時：{place_id}")
            d = {}
        except Exception as e:
            print(f"❌ Details 查詢錯誤：{e}")
            d = {}

        if details_res.get("status") != "OK":
            print(f"⚠️ Details 回傳非 OK：{details_res.get('status')}")  # 📝 log
            d = details_res.get("result", {})
        else:
            d = details_res.get("result", {})

        restaurants.append({
            "name": item.get("name"),
            "place_id": place_id,
            "rating": item.get("rating", 0),
            "user_ratings_total": item.get("user_ratings_total", 0),
            "address": d.get("formatted_address", item.get("vicinity", "")),
            "map_url": f"https://www.google.com/maps/place/?q=place_id:{place_id}",
            "phone": d.get("formatted_phone_number"),
            "website": d.get("website"),
            "price_level": d.get("price_level"),
            "opening_hours": d.get("opening_hours", {}).get("weekday_text") if d.get("opening_hours") else None
        })

    return restaurants

# ────────────────────────────────
# 🧩 LangChain Tool 包裝
# ────────────────────────────────
class PlaceSearchInput(BaseModel):
    location: str = Field(..., description="搜尋地點，例如：台北信義區")
    category: str = Field(..., description="餐廳類別，例如：火鍋、壽司、早午餐")
    radius: Optional[int] = Field(default=2000, description="搜尋半徑（公尺）")
    max_results: Optional[int] = Field(default=10, description="最多回傳筆數")


class PlaceSearchTool(BaseTool):
    name: str = Field(default="place_search_tool")
    description: str = Field(default="搜尋指定地點與餐廳類別的 Google Maps 餐廳資料")
    args_schema: Type[BaseModel] = PlaceSearchInput

    def _run(self, location: str, category: str, radius: int = 2000, max_results: int = 10):
        return search_restaurants(location, category, radius, max_results)

    async def _arun(self, **kwargs):
        raise NotImplementedError("不支援 async 模式")


# ────────────────────────────────
# 🧪 測試執行（開發用）
# ────────────────────────────────
if __name__ == "__main__":
    location = "中央大學"
    category = "火鍋"
    print(f"🔍 測試搜尋：{location} 的 {category} 餐廳...")
    results = search_restaurants(location, category,2000,3)
    print(f"共找到 {len(results)} 間：")
    for r in results:
        print(f"- {r['name']}（⭐ {r['rating']}）→ {r['map_url']}")
