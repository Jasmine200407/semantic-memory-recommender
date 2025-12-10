from review_scraper_tool import scrape_reviews_tw

# 測試一間店
place_id = "ChIJqaDWsIqrQjQR08MOJh3nNuA"   # 無老鍋(台北信義店)

reviews = scrape_reviews_tw(
    place_id=place_id,
    max_reviews=20,
    duration_limit=20,
    headless=True  # <<<< 重要！一定要看到瀏覽器
)

print("共抓到評論數：", len(reviews))
for r in reviews:
    print(r)
