/* ========= 發光效果 - 滑鼠追蹤 ========= */
const $app = document.querySelector(".app");

if ($app) {
    // 計算元素中心點
    const centerOfElement = ($el) => {
        const { left, top, width, height } = $el.getBoundingClientRect();
        return [width / 2, height / 2];
    };

    // 計算指針相對於元素的位置
    const pointerPositionRelativeToElement = ($el, e) => {
        const pos = [e.clientX, e.clientY];
        const { left, top, width, height } = $el.getBoundingClientRect();
        const x = pos[0] - left;
        const y = pos[1] - top;
        const px = clamp((100 / width) * x);
        const py = clamp((100 / height) * y);
        return { pixels: [x, y], percent: [px, py] };
    };

    // 計算角度
    const angleFromPointerEvent = ($el, dx, dy) => {
        let angleRadians = 0;
        let angleDegrees = 0;
        if (dx !== 0 || dy !== 0) {
            angleRadians = Math.atan2(dy, dx);
            angleDegrees = angleRadians * (180 / Math.PI) + 90;
            if (angleDegrees < 0) {
                angleDegrees += 360;
            }
        }
        return angleDegrees;
    };

    // 計算距離中心的距離
    const distanceFromCenter = ($card, x, y) => {
        const [cx, cy] = centerOfElement($card);
        return [x - cx, y - cy];
    };

    // 計算接近邊緣的程度
    const closenessToEdge = ($card, x, y) => {
        const [cx, cy] = centerOfElement($card);
        const [dx, dy] = distanceFromCenter($card, x, y);
        let k_x = Infinity;
        let k_y = Infinity;
        if (dx !== 0) {
            k_x = cx / Math.abs(dx);
        }
        if (dy !== 0) {
            k_y = cy / Math.abs(dy);
        }
        return clamp(1 / Math.min(k_x, k_y), 0, 1);
    };

    // 四捨五入
    const round = (value, precision = 3) => parseFloat(value.toFixed(precision));

    // 限制範圍
    const clamp = (value, min = 0, max = 100) => Math.min(Math.max(value, min), max);

    // 更新發光效果
    const cardUpdate = (e) => {
        const position = pointerPositionRelativeToElement($app, e);
        const [px, py] = position.pixels;
        const [perx, pery] = position.percent;
        const [dx, dy] = distanceFromCenter($app, px, py);
        const edge = closenessToEdge($app, px, py);
        const angle = angleFromPointerEvent($app, dx, dy);

        $app.style.setProperty("--pointer-x", `${round(perx)}%`);
        $app.style.setProperty("--pointer-y", `${round(pery)}%`);
        $app.style.setProperty("--pointer-deg", `${round(angle)}deg`);
        $app.style.setProperty("--pointer-d", `${round(edge * 100)}`);
    };

    // 綁定事件
    $app.addEventListener("pointermove", cardUpdate);
}

/* ========= WebSocket ========= */
const ws = new WebSocket(`ws://${location.host}/ws`);

/* ========= DOM ========= */
const messagesContainer = document.querySelector(".messages");
const messagesBox = document.getElementById("messages");
const input = document.getElementById("messageInput");
const sendBtn = document.getElementById("sendButton");
const cardArea = document.getElementById("recommendationArea");

/* ====== 自動捲動到底部 ====== */
function scrollToBottom() {
    if (messagesContainer) {
        // 正常佈局：捲到最底部
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
}

/* ====== UI 文字顯示 ====== */
function addMessage(text, cls = "bot") {
    const div = document.createElement("div");
    div.className = `message ${cls === "user" ? "message-personal" : ""}`;
    div.textContent = text;
    messagesBox.appendChild(div);

    // 延遲捲動，確保 DOM 已更新
    setTimeout(scrollToBottom, 50);
}

/* ====== 餐廳卡渲染 ====== */
function renderCards(recs) {
    cardArea.innerHTML = "";

    if (!recs || recs.length === 0) {
        console.log("[Card] 沒有推薦結果");
        return;
    }

    console.log(`[Card] 渲染 ${recs.length} 張餐廳卡片`);

    recs.forEach((r, index) => {
        const card = document.createElement("div");
        card.className = "restaurant-card";

        const name = r.name || "未命名餐廳";
        const rating = r.rating || "N/A";
        const address = r.address || "地址未提供";
        const reason = r.reason || "綜合推薦";
        const mapUrl = r.map_url || "";

        card.innerHTML = `
            <div class="title">${index + 1}. ${name}</div>
            <div class="rating">⭐ ${rating}　📍 ${address}</div>
            <div class="reason">💡 ${reason}</div>
            ${mapUrl ? `<a href="${mapUrl}" target="_blank" class="map-link">Google 地圖連結</a>` : ""}
        `;

        cardArea.appendChild(card);
    });

    // 渲染卡片後也捲動聊天區
    setTimeout(scrollToBottom, 100);
}

/* ====== 發訊息 ====== */
function send() {
    const text = input.value.trim();
    if (!text) return;

    console.log(`[Send] 發送訊息：${text}`);
    addMessage(text, "user");

    try {
        ws.send(text);
        input.value = "";
    } catch (error) {
        console.error("[Send] 發送失敗：", error);
        addMessage("❌ 發送失敗，請重試");
    }
}

sendBtn.onclick = send;

// Enter 發送（Shift+Enter 換行）
input.addEventListener("keypress", e => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send();
    }
});

/* ====== WebSocket 收訊 ====== */
ws.onmessage = (e) => {
    try {
        const msg = JSON.parse(e.data);
        console.log("[WS] 收到訊息：", msg);

        if (msg.type === "progress") {
            // 進度訊息
            addMessage(msg.text);
        }
        else if (msg.type === "message") {
            // 對話訊息（跳過推薦開頭）
            if (!msg.text.includes("為你推薦")) {
                addMessage(msg.text);
            }
        }
        else if (msg.type === "recommendations") {
            // 推薦結果
            console.log("[WS] 收到推薦：", msg.data);
            renderCards(msg.data);
        }
        else if (msg.type === "error") {
            // 錯誤訊息
            addMessage(`❌ 錯誤：${msg.text}`);
        }
    } catch (error) {
        console.error("[WS] 解析訊息失敗：", error);
        addMessage("⚠️ 收到無效的訊息格式");
    }
};

/* ====== WebSocket 狀態 ====== */
ws.onopen = () => {
    console.log("[WS] WebSocket 連線成功");
    addMessage("你好！告訴我你在哪裡、想吃什麼，我來幫你推薦吧！");
};

ws.onerror = (error) => {
    console.error("[WS] WebSocket 錯誤：", error);
    addMessage("⚠️ 連線發生錯誤");
};

ws.onclose = () => {
    console.log("[WS] WebSocket 連線關閉");
    addMessage("⚠️ 連線中斷，請重新整理頁面");
};

// 防止意外離開時關閉連線
window.addEventListener("beforeunload", () => {
    if (ws.readyState === WebSocket.OPEN) {
        ws.close();
    }
});
/* ====== 自動調整 textarea 高度 ====== */
if (input) {
    input.addEventListener("input", function () {
        this.style.height = "auto";
        this.style.height = Math.min(this.scrollHeight, 120) + "px";
    });
}