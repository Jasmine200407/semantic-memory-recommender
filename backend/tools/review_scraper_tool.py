import os
import re
import json
import time
from typing import Optional, Type
from pydantic import BaseModel, Field
from langchain.tools import BaseTool
from playwright.sync_api import sync_playwright

# ========== 🧩 基礎工具 ==========
def sanitize_filename(name: str) -> str:
    """移除不合法字元"""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def save_reviews(place_name, place_id, data, base_dir="output"):
    """儲存評論 JSON"""
    os.makedirs(base_dir, exist_ok=True)
    safe_name = sanitize_filename(place_name)
    path = os.path.join(base_dir, f"reviews_{safe_name[:40]}_{place_id[:6]}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path

# ========== 🧠 核心爬蟲（融合可動版） ==========
def scrape_reviews_tw(place_id: str, max_reviews: int = 100, duration_limit: int = 20, headless: bool = True):
    """
    Google Maps 評論爬蟲（CL3 版）
    ✅ 可被 LangChain Agent 呼叫
    ✅ 自動滾動＋加速封鎖圖片
    ✅ 時間與數量雙限制
    """
    url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
    print(f"🌍 開啟地圖頁面：{url}")
    print(f"📊 最大評論數：{max_reviews}（限制時間：{duration_limit} 秒）")

    reviews, seen = [], set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        # 加速：封鎖圖片、影片
        page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ["image", "media"]
            else route.continue_(),
        )

        page.goto(url, timeout=60000)
        page.wait_for_timeout(3000)

        # 點擊「查看全部評論」
        try:
            btn = page.locator("button[aria-label*='評論'], button[aria-label*='review']").first
            btn.click()
            print("✅ 已點擊評論按鈕，等待評論區開啟...")
            page.wait_for_timeout(3000)
            page.wait_for_selector("div[data-review-id]", timeout=15000)
        except Exception as e:
            print(f"⚠️ 找不到評論按鈕或超時: {e}")
            browser.close()
            return []

        # 滾動評論
        scroll_script = """
        () => {
            const el = document.querySelector('div.m6QErb.DxyBCb.kA9KIf.dS8AEf.XiKgde');
            if (!el) return 0;
            el.scrollTo(0, el.scrollHeight);
            return el.scrollTop;
        }
        """

        print(f"⚡ 連續滾動評論中（最長 {duration_limit} 秒）...")
        start = time.time()
        last_count, no_new = 0, 0

        while len(reviews) < max_reviews and (time.time() - start < duration_limit):
            for _ in range(3):
                page.evaluate(scroll_script)
                page.wait_for_timeout(200)
            page.wait_for_timeout(500)
            count = page.locator("div[data-review-id]").count()
            if count == last_count:
                no_new += 1
            else:
                no_new = 0
            if no_new >= 3:
                print("⏹ 無新評論，停止滾動。")
                break
            last_count = count

            # print(f"🌀 已載入約 {count} 則評論")

        # print(f"✅ 滾動結束，共載入 {last_count} 則評論，開始解析...")

        elements = page.locator("div[data-review-id]")
        for i in range(elements.count()):
            try:
                el = elements.nth(i)
                text = el.locator("span.wiI7pd, span[jsname='bN97Pc']").first.inner_text(timeout=500)
                stars = el.locator("span[aria-label*='星']").first.get_attribute("aria-label")
                match = re.search(r'(\d(?:\.\d)?)', stars or "")
                val = float(match.group(1)) if match else None
                if text.strip() and text.strip() not in seen:
                    seen.add(text.strip())
                    reviews.append({"text": text.strip(), "stars": val})
                    if len(reviews) >= max_reviews:
                        break
            except:
                continue

        print(f"🎯 抓取完成，共 {len(reviews)} 則評論")
        browser.close()
        return reviews


# ========== ⚙️ LangChain Tool ==========
class ReviewScraperInput(BaseModel):
    place_name: str = Field(..., description="店家名稱")
    place_id: str = Field(..., description="Google Maps Place ID")
    max_reviews: Optional[int] = Field(100, description="最大評論數")
    base_dir: Optional[str] = Field("output", description="輸出資料夾")


class ReviewScraperTool(BaseTool):
    name: str = "review_scraper_tool"
    description: str = "用於爬取 Google Maps 的評論資料（繁體中文，自動滾動）"
    args_schema: Type[BaseModel] = ReviewScraperInput

    def _run(self, place_name: str, place_id: str, max_reviews: int = 100, base_dir: str = "output"):
        data = scrape_reviews_tw(place_id, max_reviews=max_reviews)
        path = save_reviews(place_name, place_id, data, base_dir)
        return {"status": "success", "count": len(data), "file_path": path}

    async def _arun(self, **kwargs):
        raise NotImplementedError("不支援 async 模式")


# ========== 🔁 外部函式（供 Agent 使用） ==========
def get_all_reviews(place_name: str, place_id: str, max_reviews: int = 100):
    """外部呼叫封裝（給 RecommendAgent 用）"""
    try:
        data = scrape_reviews_tw(place_id, max_reviews=max_reviews)
        if data:
            save_reviews(place_name, place_id, data, base_dir="data/reviews")
        return data
    except Exception as e:
        print(f"⚠️ 抓取 {place_name} 失敗：{e}")
        return []


# ========== 🧪 測試執行 ==========
if __name__ == "__main__":
    test_name = "手工殿麻辣鍋物 信義店"
    test_place_id = "ChIJ-8qspuojaDQRc01XrVuo2sc"

    print("🚀 開始測試評論擷取工具（目標 50 則）...")
    start = time.time()
    reviews = get_all_reviews(test_name, test_place_id, max_reviews=50)

    print(f"\n📊 共擷取 {len(reviews)} 則評論，耗時 {time.time() - start:.1f} 秒。")
    for i, r in enumerate(reviews[:3], 1):
        print(f"{i}. ⭐{r['stars']}：{r['text'][:50]}...")
    print("\n💾 已自動儲存 JSON。")
