"""Tool search ranking — ported verbatim from pllla-agent
``plllaToolDiscovery.ts`` (and the OpenClaw bridge's ``tools.ts``) so every
bridge ranks the same query the same way.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List

DEFAULT_RESULT_LIMIT = 6
MAX_RESULT_LIMIT = 12

_SEPARATORS = re.compile(r"[._:/-]+")
_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = _SEPARATORS.sub(" ", text)
    # \w in Python's re with UNICODE matches letters, digits and underscore;
    # the TS original drops everything that is not a letter or a number.
    text = _NON_ALNUM.sub(" ", text).replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


def tokens(value: str) -> List[str]:
    seen: List[str] = []
    for token in normalize(value).split(" "):
        if token and token not in seen:
            seen.append(token)
    return seen


def score_tool(tool: Dict[str, Any], query: str) -> int:
    normalized_query = normalize(query)
    if not normalized_query:
        return 1
    name = str(tool.get("name") or "")
    normalized_name = normalize(name)
    normalized_description = normalize(str(tool.get("description") or ""))
    score = 0
    if name.lower() == query.strip().lower():
        score += 2_000
    if normalized_name == normalized_query:
        score += 1_000
    if normalized_query in normalized_name:
        score += 400
    if normalized_query in normalized_description:
        score += 160
    name_words = normalized_name.split(" ")
    description_words = normalized_description.split(" ")
    for token in tokens(normalized_query):
        if token in name_words:
            score += 120
        elif token in normalized_name:
            score += 70
        if token in description_words:
            score += 35
        elif token in normalized_description:
            score += 15
    return score


def clamp_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_RESULT_LIMIT
    if value != value or value in (float("inf"), float("-inf")):
        return DEFAULT_RESULT_LIMIT
    return min(MAX_RESULT_LIMIT, max(1, int(value)))


def search_tools(task_tools: List[Dict[str, Any]], args: Dict[str, Any]) -> Dict[str, Any]:
    query = args.get("query") if isinstance(args, dict) else None
    query = query.strip() if isinstance(query, str) else ""
    limit = clamp_limit(args.get("limit") if isinstance(args, dict) else None)
    scored = []
    for index, tool in enumerate(task_tools):
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        score = score_tool(tool, query)
        if score > 0:
            scored.append((score, index, tool))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    matches = []
    for _score, _index, tool in scored[:limit]:
        entry: Dict[str, Any] = {"name": tool["name"]}
        if tool.get("description"):
            entry["description"] = tool["description"]
        entry["input_schema"] = tool.get("input_schema") or {"type": "object"}
        matches.append(entry)
    return {
        "ok": True,
        "result": {"query": query, "totalAvailable": len(task_tools), "matches": matches},
    }
