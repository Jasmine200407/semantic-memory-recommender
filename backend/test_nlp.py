from recommend_agent import build_recommend_graph

graph = build_recommend_graph()
app = graph.compile()

query = "我想在信義區找適合約會的火鍋"
result = app.invoke({"user_input": query})

print(result.get("message"))
 