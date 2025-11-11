from langchain.tools import BaseTool
from typing import Optional, Type
from pydantic import BaseModel, Field
import os
import torch
import numpy as np
from sentence_transformers import SentenceTransformer, util
from transformers import pipeline

# ────────────────────────────────
# ⚙️ 初始化模型
# ────────────────────────────────
embedder = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
sentiment_analyzer = pipeline("sentiment-analysis")


# ────────────────────────────────
# 🧩 產生評論向量並儲存
# ────────────────────────────────
def encode_reviews_to_vector(reviews, save_path=None):
    """將評論文字轉成 embedding 並快取"""
    texts = [r.get("text", "") for r in reviews if r.get("text")]
    if not texts:
        return None

    embeddings = embedder.encode(texts, convert_to_tensor=True, show_progress_bar=False)

    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        torch.save(embeddings, save_path)
    return embeddings


# ────────────────────────────────
# 🧮 分析評論內容與偏好語意
# ────────────────────────────────
def analyze_reviews(reviews, preferences):
    """根據偏好語意分析餐廳評論匹配程度與正面率"""
    if not reviews:
        return {
            "summary": "無評論資料",
            "match_score": 0.0,
            "positive_rate": 0.0,
        }

    review_texts = [r.get("text", "") for r in reviews if r.get("text")]
    review_emb = embedder.encode(review_texts, convert_to_tensor=True, show_progress_bar=False)

    # 對偏好進行 embedding
    pref_text = "，".join(preferences) if preferences else "一般用餐體驗"
    pref_emb = embedder.encode([pref_text], convert_to_tensor=True)

    # 語意相似度
    sim_scores = util.cos_sim(pref_emb, review_emb).cpu().numpy().flatten()
    match_score = float(np.mean(sim_scores)) if len(sim_scores) > 0 else 0.0

    # 計算正向評論比例
    sentiments = sentiment_analyzer(review_texts[:50])  # 限制最多 50 則加速
    positive_count = sum(1 for s in sentiments if s["label"].lower().startswith("pos"))
    positive_rate = positive_count / len(sentiments) if sentiments else 0.0

    # 摘要：取最相關三句評論
    top_idx = np.argsort(sim_scores)[-3:][::-1]
    top_reviews = [review_texts[i] for i in top_idx]
    summary = " / ".join(top_reviews)

    return {
        "summary": summary,
        "match_score": round(match_score, 3),
        "positive_rate": round(positive_rate, 3),
    }


# ────────────────────────────────
# 🧠 LangChain Tool 包裝
# ────────────────────────────────
class EmbeddingAnalysisInput(BaseModel):
    reviews: list = Field(..., description="評論列表，每項包含 'text'")
    preferences: Optional[list[str]] = Field(default=[], description="使用者偏好，如 ['安靜', '氣氛好']")


class EmbeddingAnalysisTool(BaseTool):
    name: str = "embedding_analysis_tool"
    description: str = "分析評論與使用者偏好的語意相似度與情感傾向"
    args_schema: Type[BaseModel] = EmbeddingAnalysisInput

    def _run(self, reviews: list, preferences: Optional[list[str]] = None):
        result = analyze_reviews(reviews, preferences or [])
        return result

    async def _arun(self, **kwargs):
        raise NotImplementedError("此工具不支援 async 模式")
