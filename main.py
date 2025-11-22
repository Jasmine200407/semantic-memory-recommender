from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from backend.recommend_agent import RecommendAgent


app = Flask(__name__, static_folder="frontend", template_folder="frontend")
CORS(app)

agent = RecommendAgent()
state = ConversationState()

# ────────────────────────────────
# 💬 Chat Route：多輪互動邏輯
# ────────────────────────────────
@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_input = data.get("message", "").strip()

    if not user_input:
        return jsonify({"reply": "請輸入訊息喔～"})

    # 交由 RecommendAgent 處理多輪對話
    reply = agent.handle_message(user_input)
    return jsonify({"reply": reply})


# ────────────────────────────────
# 🍽️ Recommend Route：直接指定地點、類型、偏好查詢
# ────────────────────────────────
@app.route("/recommend", methods=["POST"])
def recommend():
    data = request.get_json()
    location = data.get("location")
    category = data.get("category")
    preferences = data.get("preferences", [])
    budget = data.get("budget", None)

    result = agent.run_recommendation(location, category, preferences, budget)
    return jsonify(result)


# ────────────────────────────────
# 🏠 前端首頁
# ────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
