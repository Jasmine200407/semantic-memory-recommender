# db_writer.py
import datetime
from db import SessionLocal, Restaurant, Review, Recommendation
import json 

def upsert_restaurant(info: dict):
    """
    info: 一間餐廳的基本資料
    例如：
      {
        "place_id": "...",
        "name": "...",
        "address": "...",
        "rating": 4.9,
        "user_ratings_total": 120,
        "phone": "...",
        "website": "...",
        "map_url": "..."
      }
    """
    db = SessionLocal()
    place_id = info["place_id"]

    restaurant = db.query(Restaurant).filter_by(place_id=place_id).first()
    if not restaurant:
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
    db.close()


def upsert_reviews(place_id: str, reviews: list):
    """
    reviews:
      [
        {"text": "...", "stars": 5.0},
        {"text": "...", "stars": 4.0},
      ]
    """
    db = SessionLocal()
    restaurant = db.query(Restaurant).filter_by(place_id=place_id).first()
    if not restaurant:
        db.close()
        return

    # 清空舊評論
    db.query(Review).filter_by(restaurant_id=restaurant.id).delete()

    # 新增評論
    for rv in reviews:
        db.add(Review(
            restaurant_id=restaurant.id,
            text=rv.get("text"),
            stars=rv.get("stars")
        ))

    db.commit()
    db.close()

def insert_recommendation(user_input, location, category, ranked):
    """
    ranked = 排序後完整的推薦清單（含 match_score, positive_rate, summary, reason ...）
    這裡會把：
      1. 前三名的 place_id 存在 top_place_ids
      2. 排序後後資料 用 JSON 存在 recommendation_json (含全部評估分數)
    """
    db = SessionLocal()
    try:
        top3 = [r.get("place_id") for r in ranked[:3] if r.get("place_id")]

        rec = Recommendation(
            user_input=user_input,
            location=location,
            category=category,
            top_place_ids=",".join(top3),
            recommendation_json=json.dumps(ranked, ensure_ascii=False), 
        )
        db.add(rec)
        db.commit()
        print("[insert_recommendation] 已寫入 Recommendation（含完整推薦 JSON）")
    except Exception as e:
        db.rollback()
        print("[insert_recommendation] 發生錯誤：", e)
    finally:
        db.close()


