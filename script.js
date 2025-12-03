const messagesEl = document.getElementById("messages");
const recommendationArea = document.getElementById("recommendationArea");
const inputEl = document.getElementById("messageInput");   // ← 修正完成！
const sendBtn = document.getElementById("sendButton");

let isThinking = false;

// scroll to bottom
function scrollToBottom() {
    const parent = messagesEl.parentNode;
    parent.scrollTop = parent.scrollHeight;
}

// chat bubble
function addMessage(text, isPersonal = false) {
    const div = document.createElement("div");
    div.className = "message" + (isPersonal ? " message-personal" : "");
    div.textContent = text;
    messagesEl.appendChild(div);
    scrollToBottom();
    return div;
}

// loading bubble
function addLoading() {
    const div = document.createElement("div");
    div.className = "message loading";
    div.innerHTML = "<span></span>";
    messagesEl.appendChild(div);
    scrollToBottom();
    return div;
}

// restaurant card (in bottom area)
function addRestaurantCard(item, rank) {
    const card = document.createElement("div");
    card.className = "restaurant-card";

    card.innerHTML = `
        <div class="title">#${rank} ${item.name}</div>
        <div class="rating">
          ⭐ ${item.rating ?? "—"} · 好感度 ${Math.round((item.match_score || 0) * 100)}%
        </div>
        <div class="reason">${item.reason || "很適合你！"}</div>
        <div class="tags">
          ${(item.preferences || []).map(p => `<span>#${p}</span>`).join(" ")}
        </div>
    `;

    recommendationArea.appendChild(card);
}

// clear previous recommendations
function clearRecommendations() {
    recommendationArea.innerHTML = "";
}

// send message
function sendMessage() {
    const text = inputEl.value.trim();
    if (!text) return;

    if (isThinking) {
        addMessage("我還在處理上一個請求，等等喔～");
        return;
    }

    addMessage(text, true);
    inputEl.value = "";

    isThinking = true;
    inputEl.disabled = true;
    sendBtn.disabled = true;

    let loading = addLoading();
    loading.textContent = "正在處理中…";

    clearRecommendations();

    const eventSource = new EventSource(
        `http://127.0.0.1:8000/chat_stream?user_input=${encodeURIComponent(text)}`
    );

    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.done) {
            loading.remove();
            addMessage("已完成推薦！✨");

            (data.recommendation_json || []).forEach((item, index) => {
                addRestaurantCard(item, index + 1);
            });

            isThinking = false;
            inputEl.disabled = false;
            sendBtn.disabled = false;

            eventSource.close();
            return;
        }

        loading.textContent = data.status;
    };

    eventSource.onerror = () => {
        loading.remove();
        addMessage("伺服器連線中斷，請稍後再試！");
        isThinking = false;
        inputEl.disabled = false;
        sendBtn.disabled = false;
        eventSource.close();
    };
}

sendBtn.addEventListener("click", sendMessage);

inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        e.preventDefault();
        sendMessage();
    }
});

// welcome
addMessage("嗨～我是 Foodie Hunter，有什麼想吃的嗎？");
