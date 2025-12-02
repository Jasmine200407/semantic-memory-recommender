const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("messageInput");
const sendBtn = document.getElementById("sendButton");

function scrollToBottom() {
  const parent = messagesEl.parentNode;
  parent.scrollTop = parent.scrollHeight;
}

function addMessage(text, isPersonal = false) {
  const div = document.createElement("div");
  div.className = "message" + (isPersonal ? " message-personal" : "");
  div.textContent = text;
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function addLoading() {
  const div = document.createElement("div");
  div.className = "message loading";
  div.innerHTML = "<span></span>";
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function addRestaurantBubble(item) {
  const div = document.createElement("div");
  div.className = "restaurant-bubble";
  div.innerHTML = `
    <div class="name">${item.name}</div>
    <div class="tags">${(item.tags || []).join(" / ")}</div>
    <div class="reason">${item.reason}</div>
    <div class="price">預估價位：約 ${item.avg_price || "—"} 元/人</div>
  `;
  messagesEl.appendChild(div);
  scrollToBottom();
}

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;

  // 使用者訊息
  addMessage(text, true);
  inputEl.value = "";

  // loading
  const loading = addLoading();

  // 🔧 這裡先用假資料，確定前端 UI 沒問題
  setTimeout(() => {
    loading.remove();
    addMessage("以下是我為你找到的餐廳（目前是假資料 Demo）：");

    const mock = [
      {
        name: "湯之森火鍋屋",
        tags: ["火鍋", "約會", "下雨天"],
        reason: "湯底溫和、氣氛安靜，很適合雨天慢慢聊天。",
        avg_price: 320
      },
      {
        name: "小川食堂",
        tags: ["午餐", "上班族", "出餐快"],
        reason: "步行約 5 分鐘即可抵達，出餐快速，適合午休時間有限時使用。",
        avg_price: 200
      }
    ];

    mock.forEach(addRestaurantBubble);
  }, 800);

  /* 
  ✅ 之後要接你們的後端時，把上面 setTimeout 拿掉
     改成呼叫 API：

  try {
    const res = await fetch("http://127.0.0.1:8000/search", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ query: text, top_k: 3 })
    });
    const data = await res.json();
    loading.remove();

    if (!data.restaurants || data.restaurants.length === 0) {
      addMessage("目前找不到符合的餐廳 QQ");
      return;
    }

    addMessage("以下是我為你找到的選項：");
    data.restaurants.forEach(addRestaurantBubble);

  } catch (err) {
    loading.remove();
    addMessage("伺服器錯誤，請稍後再試～");
  }
  */
}

sendBtn.addEventListener("click", sendMessage);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    sendMessage();
  }
});

// 一進頁面先給一則歡迎訊息
addMessage("嗨～我是 Foodie Hunter，有什麼想吃的嗎？");
