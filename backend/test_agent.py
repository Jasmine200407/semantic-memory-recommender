"""
餐廳推薦 Agent 測試腳本
"""
from recommender.recommend_agent import build_recommend_graph, RecommendState

def test_recommend_agent():
    graph = build_recommend_graph().compile()
    
    state = {
        "user_input": None,
        "location": None,
        "category": None,
        "preferences": None,
        "restaurants": None,
        "review_batches": None,
        "analyzed": None,
        "recommendations": None,
        "ranked": [],
        "next": None,
        "message": None
    }

    print("=" * 60)
    print("餐廳推薦助手已啟動")
    print("=" * 60)
    print("提示：")
    print("  - 可以分步輸入地點和類型")
    print("  - 輸入 'reset' 重置狀態")
    print("  - 輸入 'q' 離開")
    print("=" * 60)
    print()

    while True:
        msg = input("你：").strip()
        
        if msg.lower() == "q":
            print("\n👋 再見！")
            break
        
        if msg.lower() == "reset":
            # 重置狀態
            state = {
                "user_input": None,
                "location": None,
                "category": None,
                "preferences": None,
                "restaurants": None,
                "review_batches": None,
                "analyzed": None,
                "recommendations": None,
                "ranked": [],
                "next": None,
                "message": None
            }
            print("\n狀態已重置\n")
            continue
        
        if not msg:
            continue

        # 設定使用者輸入
        state["user_input"] = msg
        
        try:
            # ★ 執行圖並獲取結果
            result = graph.invoke(state)
            
            # ★ 更新狀態（只更新有值的欄位）
            for key, value in result.items():
                if value is not None:
                    state[key] = value
            
            # 顯示回應
            if state.get("message"):
                print(f"\nAI：{state['message']}\n")
            
            # 顯示推薦結果（如果有）
            if state.get("recommendations"):
                print("\n" + "=" * 60)
                print("推薦結果：")
                print("=" * 60)
                for i, rec in enumerate(state["recommendations"], 1):
                    print(f"\n{i}. {rec.get('name', '未命名')}")
                    print(f"評分：{rec.get('rating', 'N/A')}")
                    print(f"地址：{rec.get('address', 'N/A')}")
                    if rec.get('reason'):
                        print(f"推薦理由：{rec['reason']}")
                print("=" * 60)
                print()
            
            # 清除 user_input，避免下次被重複使用
            state["user_input"] = None
            
            # Debug：顯示目前狀態（可選）
            print(f"[Debug] 目前狀態 - 地點:{state.get('location')}, 類型:{state.get('category')}")
            print()
            
        except Exception as e:
            print(f"\n錯誤：{e}\n")
            import traceback
            traceback.print_exc()
            print()


if __name__ == "__main__":
    test_recommend_agent()