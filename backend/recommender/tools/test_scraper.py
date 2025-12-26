from review_scraper_tool import scrape_reviews_tw

# 測試爬取一間餐廳評論
place_id = "ChIJqaDWsIqrQjQR08MOJh3nNuA"   # 無老鍋(台北信義店)

reviews = scrape_reviews_tw(
    place_id=place_id,
    max_reviews=20,
    duration_limit=20,
    headless=True # 為了實際瀏覽器操作過程觀察
)

print("共抓到評論數：", len(reviews))
for r in reviews:
    print(r)
