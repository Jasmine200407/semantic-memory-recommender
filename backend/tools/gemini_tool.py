from langchain.tools import BaseTool
from typing import Optional, Type
from pydantic import BaseModel, Field
import google.generativeai as genai
import os
import time
from dotenv import load_dotenv

# ────────────────────────────────
# ⚙️ 初始化 Gemini
# ────────────────────────────────
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY 未設定，請在 .env 或系統環境變數中設置。")

genai.configure(api_key=GEMINI_API_KEY)


# ────────────────────────────────
# 🧠 核心函式：生成推薦理由
# ────────────────────────────────
def generate_reason(name, summary, preferences=None, match_score=None):
    preferences = preferences or []
    pref_text = "、".join(preferences) if preferences else "一般用餐需求"

    prompt = f"""
你是一位貼心的美食顧問，請根據以下資訊為使用者生成推薦理由。

- 餐廳名稱：{name}
- 使用者偏好：{pref_text}
- 匹配分數（0~1）：{match_score if match_score is not None else '未知'}
- 評論摘要：{summary}

請生成 2~3 句自然流暢的繁體中文理由，語氣親切、自然，
要明確說出這家餐廳為何符合使用者的偏好（如氣氛、口味、CP值等）。
回覆格式請只輸出純文字，不要包含 JSON、標題或代碼。
    """

    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(prompt)
        if hasattr(response, "text") and response.text:
            return response.text.strip()
        elif hasattr(response, "candidates"):
            return response.candidates[0].content.parts[0].text.strip()
        else:
            return f"{name} 很符合你喜歡的『{pref_text}』氛圍，值得一試！"
    except Exception as e:
        print(f"⚠️ Gemini 生成失敗：{e}")
        time.sleep(1)
        return f"{name} 的風格很符合你想要的『{pref_text}』氛圍，值得一試！"
def call_gemini(prompt: str, model: str = "gemini-2.5-flash", temperature: float = 0.3) -> str:
    """
    呼叫 Gemini 模型，回傳純文字內容。
    Args:
        prompt (str): 要輸入的提示字串。
        model (str): 模型名稱，預設 "gemini-1.5-flash"。
        temperature (float): 生成溫度，控制創造性。
    Returns:
        str: 模型回傳的文字結果。
    """
    try:
        gemini_model = genai.GenerativeModel(model)
        response = gemini_model.generate_content(prompt, generation_config={"temperature": temperature})
        return response.text.strip()
    except Exception as e:
        print(f"⚠️ Gemini 呼叫失敗: {e}")
        return ""

# ────────────────────────────────
# 🧰 LangChain Tool 包裝
# ────────────────────────────────
class GeminiReasonInput(BaseModel):
    name: str = Field(..., description="餐廳名稱")
    summary: str = Field(..., description="餐廳評論摘要或主要特點")
    preferences: Optional[list[str]] = Field(default=[], description="使用者偏好，如 ['氣氛好','適合聚餐']")
    match_score: Optional[float] = Field(default=None, description="匹配分數（0~1）")


class GeminiReasonTool(BaseTool):
    name: str = "gemini_reason_tool"
    description: str = (
        "使用 Gemini 模型生成個人化的推薦理由，根據餐廳資訊與使用者偏好"
    )
    args_schema: Type[BaseModel] = GeminiReasonInput


    def _run(self, name: str, summary: str, preferences: Optional[list[str]] = None,
             match_score: Optional[float] = None):
        text = generate_reason(name, summary, preferences, match_score)
        return {"restaurant": name, "reason": text}

    async def _arun(self, **kwargs):
        raise NotImplementedError("此工具不支援 async 模式")
