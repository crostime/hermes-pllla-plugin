import asyncio
import json

from pllla_bridge.turns import (
    CURRENT_TASK,
    TaskContext,
    attachments_note,
    clean_user_text,
    context_for_prompt,
    handle_call,
    handle_search,
    last_user_text,
    turn_text,
)

TASK = {
    "id": "t1",
    "chatId": "c1",
    "systemPrompt": "Your name is Bot.",
    "messages": [
        {"role": "user", "content": "[10:00 AM] <<<user_message>>>hi<<</user_message>>>"},
        {"role": "ai", "content": "hello"},
        {
            "role": "user",
            "content": "[10:01 AM] <<<user_message>>>send the photo<<</user_message>>>",
            "attachments": [{"name": "a.png", "url": "/x/a.png"}],
        },
    ],
    "tools": [{"name": "pllla.chat.send_media", "description": "Send an image", "input_schema": {"type": "object"}}],
}


def test_turn_text_is_the_newest_non_agent_message_as_typed_plus_attachment_names():
    assert last_user_text(TASK["messages"]) == "send the photo"
    assert attachments_note(TASK["messages"]) == "[attachments] a.png"
    assert turn_text(TASK) == "send the photo\n\n[attachments] a.png"
    assert turn_text({"messages": []}) == ""


def test_clean_user_text_drops_the_markers_and_only_a_leading_bubble_time():
    # The exact shape the gateway logged on 2026-09-03.
    assert clean_user_text("[09:59 AM] <<<user_message>>>안녕! 너는 누구야?<<</user_message>>>") == "안녕! 너는 누구야?"
    # Korean-locale bubble time; a bracket without a clock is content.
    assert clean_user_text("[오전 9:59] hello") == "hello"
    assert clean_user_text("[note] keep me") == "[note] keep me"
    # Only the first bracket goes; a time later in the text is content.
    assert clean_user_text("[10:00 AM] meet at [11:00]") == "meet at [11:00]"


def test_context_for_prompt_wraps_the_server_prompt():
    ctx = TaskContext(task_id="t1", chat_id="c1", system_prompt="Your name is Bot.")
    assert context_for_prompt(ctx) == "[PLLLA context for this turn]\nYour name is Bot.\n[END PLLLA context]"
    assert context_for_prompt(TaskContext(task_id="t", chat_id="c", system_prompt="  ")) == ""


def test_tools_refuse_without_an_active_task_and_dispatch_only_granted_names():
    async def run():
        no_task = json.loads(await handle_search({"query": "x"}))
        assert no_task["code"] == "PLLLA_NO_ACTIVE_TASK"

        dispatched = []

        async def dispatch(task_id, name, tool_input):
            dispatched.append((task_id, name, tool_input))
            return {"ok": True, "result": {"sent": True}}

        ctx = TaskContext(task_id="t1", chat_id="c1", system_prompt="", tools=TASK["tools"], dispatch=dispatch)
        token = CURRENT_TASK.set(ctx)
        try:
            found = json.loads(await handle_search({"query": "image"}))
            assert [m["name"] for m in found["result"]["matches"]] == ["pllla.chat.send_media"]
            # The model must not route matches through Hermes' own tool_call.
            assert found["result"]["call_with"] == "pllla_tools_call"
            assert "tool_call" in found["result"]["note"]
            denied = json.loads(await handle_call({"name": "pllla.library.search", "input": {}}))
            assert denied["code"] == "PLLLA_TOOL_NOT_GRANTED"
            ok = json.loads(await handle_call({"name": "pllla.chat.send_media", "input": {"path": "/x"}}))
            assert ok == {"ok": True, "result": {"sent": True}}
            missing = json.loads(await handle_call({"input": {}}))
            assert missing["code"] == "PLLLA_TOOL_NAME_REQUIRED"
        finally:
            CURRENT_TASK.reset(token)
        assert dispatched == [("t1", "pllla.chat.send_media", {"path": "/x"})]

    asyncio.run(run())
