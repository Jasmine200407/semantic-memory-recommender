import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from recommender.recommend_agent import build_recommend_graph

app = FastAPI()

# === Serve frontend directory ===
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BACKEND_DIR)
frontend_dir = os.path.join(ROOT_DIR, "frontend")
frontend_dir = os.path.abspath(frontend_dir)

app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

@app.get("/")
async def index():
    return FileResponse(os.path.join(frontend_dir, "index.html"))

# === Build Graph ===
raw_graph = build_recommend_graph()
graph = raw_graph.compile()

# === WebSocket Chat ===
@app.websocket("/ws")
async def chat(ws: WebSocket):
    await ws.accept()
    
    # 使用字典儲存狀態
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
        "message": None,
        "waiting_for_confirmation": False,
        "waiting_for_preference": False
    }
    
    print("[WebSocket] 使用者已連線")
    
    # 節點完成時的進度訊息（只保留關鍵步驟）
    node_completion_map = {
        "confirm_response_node": {
            "place_search_node": "確認完成，開始搜尋餐廳...",
        },
        "place_search_node": {
            "review_fetch_node": "餐廳搜尋完成，開始蒐集評論...",
        },
        "review_fetch_node": {
            "analysis_node": "評論蒐集完成，開始分析餐廳...",
        },
        "analysis_node": {
            "ranking_node": "餐廳分析完成，開始排序...",
        },
        "ranking_node": {
            "response_node": "排序完成，準備推薦結果...",
        }
    }
    
    try:
        while True:
            # 接收使用者輸入
            text = await ws.receive_text()
            print(f"[WebSocket] 收到訊息：{text}")
            
            state["user_input"] = text
            
            # 使用 astream 執行圖
            try:
                async for event in graph.astream(state):
                    for node_name, node_output in event.items():
                        print(f"[WebSocket] 節點 {node_name} 完成")
                        
                        # 更新狀態
                        if isinstance(node_output, dict):
                            state.update(node_output)
                            
                            # 獲取下一個節點
                            next_node = node_output.get("next", "end")
                            
                            # 發送自訂進度訊息
                            if node_name in node_completion_map:
                                progress_msgs = node_completion_map[node_name]
                                if next_node in progress_msgs:
                                    progress_msg = progress_msgs[next_node]
                                    if progress_msg:  # 只有非空字串才發送
                                        try:
                                            await ws.send_json({
                                                "type": "progress",
                                                "text": progress_msg
                                            })
                                        except Exception as e:
                                            print(f"[WebSocket] 發送進度失敗：{e}")
                            
                            # 發送節點的 message（如果有且不包含「為你推薦」）
                            if "message" in node_output:
                                msg = node_output["message"]
                                if msg and not msg.startswith("為你推薦"):
                                    try:
                                        await ws.send_json({
                                            "type": "message",
                                            "text": msg
                                        })
                                    except Exception as e:
                                        print(f"[WebSocket] 發送訊息失敗：{e}")
                
                # 發送推薦結果
                if state.get("recommendations"):
                    print(f"[WebSocket] 發送推薦數量：{len(state['recommendations'])}")
                    
                    # 先顯示推薦訊息
                    await ws.send_json({
                        "type": "message",
                        "text": "右側為你們的推薦結果"
                    })
                    
                    # 再發送卡片
                    await ws.send_json({
                        "type": "recommendations", 
                        "data": state["recommendations"]
                    })
                
                # 清除 user_input
                state["user_input"] = None
                
            except Exception as e:
                print(f"[WebSocket] 執行圖時發生錯誤：{e}")
                import traceback
                traceback.print_exc()
                await ws.send_json({
                    "type": "error",
                    "text": f"處理請求時發生錯誤：{str(e)}"
                })
    
    except WebSocketDisconnect:
        print("[WebSocket] 使用者離線")
    except Exception as e:
        print(f"[WebSocket] 連線錯誤：{e}")
        import traceback
        traceback.print_exc()