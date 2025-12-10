# init_db_run.py
from app.db import init_db

if __name__ == "__main__":
    init_db()
    print("資料庫初始化完成！（data/app.db 已建立）")
