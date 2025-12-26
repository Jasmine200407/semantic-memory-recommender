# -*- coding: utf-8 -*-
import os
import re
import json
import time
from typing import Optional, Type, List, Dict, Any
from pydantic import BaseModel, Field
from langchain.tools import BaseTool
from playwright.sync_api import sync_playwright


def sanitize_filename(name: str) -> str:
    """移除不合法字元（若未來要用）"""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


# 核心爬蟲
def scrape_reviews_tw(place_id: str, max_reviews: int = 100, duration_limit: int = 20, headless: bool = True):
    url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
    print(f"開啟地圖頁面：{url}")
    print(f"最大評論數：{max_reviews}（限制時間：{duration_limit} 秒）")

    reviews, seen = [], set()

    with sync_playwright() as p:
        # Headless Anti-detection 
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1280,800",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

        context = browser.new_context(
            locale="zh-TW",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            java_script_enabled=True,
        )

        # 讓 Google 無法偵測 headless
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        page = context.new_page()

        # 阻擋圖片
        page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ["image", "media"]
            else route.continue_(),
        )

        # 開始爬蟲
        page.goto(url, timeout=60000)
        page.wait_for_timeout(2000)

        # 點擊「查看全部評論」按鈕
        try:
            btn = page.locator("button[aria-label*='評論'], button[aria-label*='review']").first
            btn.click()
            print("已點擊評論按鈕")
            page.wait_for_timeout(2000)
            page.wait_for_selector("div[data-review-id]", timeout=15000)
        except Exception as e:
            print(f"找不到評論按鈕或超時: {e}")
            context.close()
            browser.close()
            return []

        print(f"正在滾動評論（最長 {duration_limit} 秒）...")

        scroll_script = """
        () => {
            const el = document.querySelector('div.m6QErb.DxyBCb.kA9KIf.dS8AEf.XiKgde');
            if (!el) return 0;
            el.scrollTo(0, el.scrollHeight);
            return el.scrollTop;
        }
        """

        start_time = time.time()
        while len(reviews) < max_reviews and (time.time() - start_time < duration_limit):
            page.evaluate(scroll_script)
            page.wait_for_timeout(500)

        # 抓取評論
        elements = page.locator("div[data-review-id]")
        print("正在解析評論...")

        for i in range(elements.count()):
            try:
                el = elements.nth(i)
                text = el.locator("span.wiI7pd, span[jsname='bN97Pc']").first.inner_text(timeout=500)

                stars_raw = el.locator("span[aria-label*='星']").first.get_attribute("aria-label")
                match = re.search(r'(\d(\.\d)?)', stars_raw or "")
                stars = float(match.group(1)) if match else None

                if text not in seen:
                    seen.add(text)
                    reviews.append({"text": text.strip(), "stars": stars})

                    if len(reviews) >= max_reviews:
                        break
            except:
                continue

        print(f" 完成：共 {len(reviews)} 則評論")
        context.close()
        browser.close()
        return reviews

# ────────────────────────────────
# LangChain Tool 包裝
# ────────────────────────────────
class ReviewScraperInput(BaseModel):
    place_id: str = Field(..., description="Google Maps Place ID")
    max_reviews: Optional[int] = Field(100, description="最大評論數")


class ReviewScraperTool(BaseTool):
    name: str = "review_scraper_tool"
    description: str = "爬取 Google Maps 的評論資料（繁中）"
    args_schema: Type[BaseModel] = ReviewScraperInput

    def _run(self, place_id: str, max_reviews: int = 100):
        # 不存檔！只送出 reviews
        data = scrape_reviews_tw(place_id, max_reviews=max_reviews)
        return data

    async def _arun(self, **kwargs):
        raise NotImplementedError("不支援 async")

# 提供給 Agent 呼叫
def get_all_reviews(place_name: str, place_id: str, max_reviews: int = 100) -> List[Dict[str, Any]]:
    try:
        return scrape_reviews_tw(place_id, max_reviews=max_reviews)
    except Exception as e:
        print(f"抓取 {place_name} 失敗：{e}")
        return []
