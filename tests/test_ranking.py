from pllla_bridge.ranking import clamp_limit, normalize, score_tool, search_tools

TOOLS = [
    {"name": "pllla.library.search", "description": "Search the owner's library", "input_schema": {"type": "object"}},
    {"name": "pllla.chat.send_media", "description": "Send an image or file into the chat"},
    {"name": "pllla.memory.search", "description": "Search shared memories"},
]


def test_normalize_matches_the_typescript_port():
    assert normalize("pllla.library.search") == "pllla library search"
    assert normalize("Send_Media!") == "send media"


def test_exact_name_outranks_substring_and_description_hits():
    exact = score_tool(TOOLS[0], "pllla.library.search")
    partial = score_tool(TOOLS[0], "library")
    description_only = score_tool(TOOLS[1], "image")
    assert exact > partial > description_only > 0


def test_search_orders_by_score_then_declaration_order_and_clamps_limit():
    result = search_tools(TOOLS, {"query": "search", "limit": 50})
    names = [match["name"] for match in result["result"]["matches"]]
    assert names[:2] == ["pllla.library.search", "pllla.memory.search"]
    assert result["result"]["totalAvailable"] == 3
    assert clamp_limit(50) == 12
    assert clamp_limit(None) == 6
    assert clamp_limit(True) == 6
    # 빈 질의는 전부 1점 → 선언 순서.
    empty = search_tools(TOOLS, {"query": "", "limit": 2})
    assert [m["name"] for m in empty["result"]["matches"]] == ["pllla.library.search", "pllla.chat.send_media"]
    assert empty["result"]["matches"][1]["input_schema"] == {"type": "object"}
