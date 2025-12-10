# db.py
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Text, DateTime, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime
import os
from sqlalchemy import JSON
# 1. 確保有 data/ 資料夾
os.makedirs("data", exist_ok=True)

# 2. 設定資料庫連線字串
# SQLite 會在 data/app.db 這個檔案裡存資料
BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # backend/
DB_PATH = os.path.join(BASE_DIR, "data", "app.db")

DATABASE_URL = f"sqlite:///{DB_PATH}"

# 3. 建立 Engine（負責跟 DB 溝通的主連線物件）
engine = create_engine(DATABASE_URL, echo=False, future=True)

# 4. 建立 Session 類別（之後用他拿到「連線 session」）
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# 5. Base：所有資料表 model 的基底類別
Base = declarative_base()


# 6. 定義「餐廳」資料表
class Restaurant(Base):
    __tablename__ = "restaurants"  # 資料表名稱

    id = Column(Integer, primary_key=True, index=True)  # 自動編號
    place_id = Column(String(128), unique=True, index=True, nullable=False)
    name = Column(String(256), nullable=False)
    address = Column(String(512))
    rating = Column(Float)
    user_ratings_total = Column(Integer)
    phone = Column(String(64))
    website = Column(String(512))
    map_url = Column(String(512))
    last_update = Column(DateTime, default=datetime.utcnow)

    # 一間餐廳有很多評論：一對多關係
    reviews = relationship("Review", back_populates="restaurant")


# 7. 定義「評論」資料表
class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"))  # 關聯到 Restaurant.id
    text = Column(Text)
    stars = Column(Float)

    restaurant = relationship("Restaurant", back_populates="reviews")


# 8. 定義「推薦紀錄」資料表（之後 API 用，現在可以先放著）
class Recommendation(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    user_input = Column(Text)      # 使用者原始輸入句子
    location = Column(String(128)) # 之後你可以塞解析出來的地點
    category = Column(String(64))  # 類別（咖啡、燒肉…）
    top_place_ids = Column(String(512))  # 用逗號串起來的 place_id 列表
    recommendation_json = Column(JSON)  # ⭐ 新增：存完整推薦結果(JSON)

# 9. 建立資料表用的函式
def init_db():
    Base.metadata.create_all(bind=engine)
