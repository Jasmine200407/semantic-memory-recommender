from langchain.tools import BaseTool
from typing import Optional, Type
from pydantic import BaseModel, Field
import os
import torch
import numpy as np
from sentence_transformers import SentenceTransformer, util
from transformers import pipeline

# ────────────────────────────────
# 模型選擇
# ────────────────────────────────
EMBED_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
SENTIMENT_MODEL_NAME = "uer/roberta-base-finetuned-dianping-chinese"

# 模型快取資料夾
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models")

def load_embedder():
    local_path = os.path.join(MODEL_DIR, "MiniLM-L12-v2")
    try:
        # 優先讀取本地快取
        if os.path.exists(local_path):
            return SentenceTransformer(local_path)
        # 沒有就下載一次
        return SentenceTransformer(EMBED_MODEL_NAME, cache_folder=MODEL_DIR)
    except Exception as e:
        print("[WARN] Embedding 模型下載失敗，改用 fallback (詞向量失效)")
        return None  # 之後分析時會用 fallback 排序

def load_sentiment_analyzer():
    local_path = os.path.join(MODEL_DIR, "dianping-sentiment")
    device = 0 if torch.cuda.is_available() else -1
    try:
        if os.path.exists(local_path):
            return pipeline("sentiment-analysis", model=local_path, device=device)
        
        # transformers 4.x 版本需要透過 model_kwargs 或直接在 from_pretrained 時設定
        return pipeline(
            "sentiment-analysis", 
            model=SENTIMENT_MODEL_NAME,
            device=device,
            model_kwargs={"cache_dir": MODEL_DIR}
        )
    except Exception as e:
        print("[WARN] Sentiment 模型下載失敗，改用 fallback (不做情緒分析)")
        print(f"[WARN] 錯誤詳情：{e}")
        return None

embedder = load_embedder()
sentiment_analyzer = load_sentiment_analyzer()

# ────────────────────────────────
# 產生評論向量並儲存
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
# 分析評論內容與偏好語意
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
    
    # 如果沒有 embedder，使用 fallback
    if not embedder:
        print("[WARN] Embedder 未初始化，使用 fallback 分析")
        return {
            "summary": " / ".join(review_texts[:3]),
            "match_score": 0.5,  # 給予中等分數
            "positive_rate": 0.5,
        }
    
    review_emb = embedder.encode(review_texts, convert_to_tensor=True, show_progress_bar=False)

    # 對偏好進行 embedding
    pref_text = "，".join(preferences) if preferences else "一般用餐體驗"
    pref_emb = embedder.encode([pref_text], convert_to_tensor=True)

    # 語意相似度
    sim_scores = util.cos_sim(pref_emb, review_emb).cpu().numpy().flatten()
    match_score = float(np.mean(sim_scores)) if len(sim_scores) > 0 else 0.0

    # 計算正向評論比例
    if sentiment_analyzer:
        try:
            sentiments = sentiment_analyzer(review_texts[:50])  # 限制最多 50 則加速
            positive_count = sum(1 for s in sentiments if s["label"].lower().startswith("pos"))
            positive_rate = positive_count / len(sentiments) if sentiments else 0.0
        except Exception as e:
            print(f"[WARN] Sentiment 分析失敗：{e}")
            positive_rate = 0.5  # fallback
    else:
        positive_rate = 0.5  # 無 sentiment analyzer 時給予中等分數

    # 摘要：取最相關三句評論
    top_idx = np.argsort(sim_scores)[-10:][::-1]
    top_reviews = [review_texts[i] for i in top_idx]
    summary = " / ".join(top_reviews)

    return {
        "summary": summary,
        "match_score": round(match_score, 3),
        "positive_rate": round(positive_rate, 3),
    }


# ────────────────────────────────
# LangChain Tool 包裝
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