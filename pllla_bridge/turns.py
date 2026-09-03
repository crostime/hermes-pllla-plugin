"""How a PLLLA task becomes a Hermes turn, and how the turn's context reaches
the tools and the prompt.

Hermes builds its own system prompt (SOUL.md = persona, installed at pairing)
and lets plugins add *ephemeral* context to the current user message through
the ``pre_llm_call`` hook (never the system prompt — cache prefix). So the
server's per-task prompt (conversation context, page, memory, tool-bridge
instructions, trust boundary) travels as that context, keyed off a
``ContextVar`` set right before ``handle_message`` — background tasks and
``asyncio.to_thread`` inherit it, which is where hooks and tool handlers run.
"""

from __future__ import annotations

import contextvars
import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .contract import CALL_TOOL_NAME as CALL_TOOL_NAME_FOR_HINT
from .contract import PLLLA_CALL_NAME, PLLLA_SEARCH_NAME
from .ranking import search_tools

# The server wraps other people's words in data markers and prefixes every
# non-agent message with its bubble time (`[07:52 AM] …`). Hermes shows the
# user turn to the model as-is, so both come off here — the same rule as
# pllla-agent's Aside engine (unwrapOwnerMessage / stripLeadingBubbleTime).
# The server prompt still explains the trust boundary in the turn context.
USER_MESSAGE_MARKER_RE = re.compile(r"<<</?user_message>>>")
BUBBLE_TIME_PREFIX_RE = re.compile(r"^\s*\[(?=[^\]]*\d{1,2}:\d{2})[^\]]{1,32}\]\s*")


def unwrap_user_message(text: str) -> str:
    return USER_MESSAGE_MARKER_RE.sub("", text)


def strip_leading_bubble_time(text: str) -> str:
    return BUBBLE_TIME_PREFIX_RE.sub("", text, count=1)


def clean_user_text(text: str) -> str:
    return strip_leading_bubble_time(unwrap_user_message(text)).strip()

DispatchFn = Callable[[str, str, Dict[str, Any]], Awaitable[Dict[str, Any]]]


@dataclass
class TaskContext:
    task_id: str
    chat_id: str
    system_prompt: str
    tools: List[Dict[str, Any]] = field(default_factory=list)
    dispatch: Optional[DispatchFn] = None
    is_greeting: bool = False


CURRENT_TASK: contextvars.ContextVar[Optional[TaskContext]] = contextvars.ContextVar(
    "pllla_current_task", default=None
)


def last_user_text(messages: List[Dict[str, Any]]) -> str:
    """The message Hermes answers — the newest non-agent message, as the
    person typed it (markers and bubble time removed)."""
    for message in reversed(messages):
        role = str(message.get("role") or "")
        if role in ("ai", "assistant"):
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return clean_user_text(content)
    return ""


def attachments_note(messages: List[Dict[str, Any]]) -> str:
    """Names the newest message's attachments; image download is phase 2."""
    for message in reversed(messages):
        if str(message.get("role") or "") in ("ai", "assistant"):
            continue
        attachments = message.get("attachments")
        if isinstance(attachments, list) and attachments:
            names = [
                str(item.get("name") or item.get("url") or "attachment")
                for item in attachments
                if isinstance(item, dict)
            ]
            if names:
                return "[attachments] " + ", ".join(names)
        break
    return ""


def turn_text(task: Dict[str, Any]) -> str:
    """User-visible text of the turn: the newest message (+ attachment names)."""
    messages = task.get("messages") if isinstance(task.get("messages"), list) else []
    text = last_user_text(messages)
    note = attachments_note(messages)
    return f"{text}\n\n{note}".strip() if note else text


def context_for_prompt(task: TaskContext) -> str:
    """What ``pre_llm_call`` injects — the server prompt, framed as data."""
    prompt = task.system_prompt.strip()
    if not prompt:
        return ""
    return "[PLLLA context for this turn]\n" + prompt + "\n[END PLLLA context]"


# ── discovery tools ─────────────────────────────────────────────────────────


def _result(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _no_active_task() -> str:
    return _result(
        {
            "ok": False,
            "code": "PLLLA_NO_ACTIVE_TASK",
            "error": "No PLLLA task is active for this turn, so no PLLLA app tools are available right now.",
        }
    )


CALL_HINT = (
    "Run any match with pllla_tools_call(name=<name>, input={...}). "
    "These are PLLLA tools, not Hermes deferred tools — Hermes' tool_call "
    "does not know them (measured: a model wasted 13 calls that way)."
)


async def handle_search(args: Dict[str, Any], **_: Any) -> str:
    task = CURRENT_TASK.get()
    if task is None:
        return _no_active_task()
    found = search_tools(task.tools, args or {})
    result = found.get("result")
    if isinstance(result, dict):
        result["call_with"] = CALL_TOOL_NAME_FOR_HINT
        result["note"] = CALL_HINT
    return _result(found)


async def handle_call(args: Dict[str, Any], **_: Any) -> str:
    task = CURRENT_TASK.get()
    if task is None:
        return _no_active_task()
    name = str((args or {}).get("name") or "").strip()
    tool_input = (args or {}).get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    if not name:
        return _result({"ok": False, "code": "PLLLA_TOOL_NAME_REQUIRED", "error": "`name` is required."})
    if name in (PLLLA_SEARCH_NAME, PLLLA_CALL_NAME):
        return _result(search_tools(task.tools, tool_input)) if name == PLLLA_SEARCH_NAME else _result(
            {"ok": False, "code": "PLLLA_TOOL_NOT_GRANTED", "error": "Call a discovered tool by its own name."}
        )
    granted = {str(tool.get("name")) for tool in task.tools if isinstance(tool, dict)}
    if name not in granted:
        return _result(
            {
                "ok": False,
                "code": "PLLLA_TOOL_NOT_GRANTED",
                "error": f"`{name}` is not granted to this task. Search first with {PLLLA_SEARCH_NAME}.",
            }
        )
    if task.dispatch is None:
        return _result({"ok": False, "code": "PLLLA_LANE_DISCONNECTED", "error": "The PLLLA lane is not connected."})
    response = await task.dispatch(task.task_id, name, tool_input)
    if response.get("ok") is False:
        return _result(
            {
                "ok": False,
                "code": response.get("code") or "error",
                "error": response.get("error") or "unknown error",
            }
        )
    return _result({"ok": True, "result": response.get("result")})


SEARCH_TOOL_SCHEMA = {
    "name": "pllla_tools_search",
    "description": (
        "Search the PLLLA app tools granted to the current conversation turn. "
        "Returns tool names, descriptions, and input schemas. Search before "
        "concluding a PLLLA app capability is unavailable. Run a match with "
        "pllla_tools_call — these are not Hermes tool_search/tool_call tools."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What you want to do (keywords or a short phrase)."},
            "limit": {"type": "integer", "description": "Max results (default 8)."},
        },
        "required": [],
    },
}

CALL_TOOL_SCHEMA = {
    "name": "pllla_tools_call",
    "description": (
        "Execute one PLLLA app tool discovered with pllla_tools_search, by its exact name. "
        "The tool runs on the PLLLA server for the current task."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact tool name from pllla_tools_search."},
            "input": {"type": "object", "description": "Tool input matching its input_schema."},
        },
        "required": ["name"],
    },
}
