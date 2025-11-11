"""
💾 JSON Save Tool (for LangGraph)
安全儲存資料為 UTF-8 JSON 檔案。
"""

import json
import os
from langchain.tools import tool  # ✅ 支援 LangGraph 節點


@tool("save_json")
def save_json(data: dict, path: str) -> dict:
    """
    儲存資料為 JSON 檔案。
    Args:
        data (dict): 欲儲存的資料
        path (str): 檔案完整路徑，例如 "backend/data/reviews/xxx.json"
    Returns:
        dict: 儲存結果
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"💾 已儲存 {path}")
        return {"success": True, "path": path}
    except Exception as e:
        print(f"❌ 儲存失敗: {e}")
        return {"success": False, "error": str(e), "path": path}
