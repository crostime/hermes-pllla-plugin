"""A failed turn, told honestly to PLLLA.

The server's ``agent:response`` accepts a ``failure`` *object*
(``{kind, engineType, model}``) and rejects a string — a string made the
server drop the response, the task time out, and the chat show "The bot did
not respond" while the cause sat in the runtime log (measured 2026-09-03 on
the OpenClaw bridge; this bridge had the same shape). Kind detection mirrors
pllla-agent's classifyEngineFailure and the OpenClaw bridge's failure.ts —
keep the three in step.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

ENGINE_TYPE = "hermes"
MAX_MODEL_LENGTH = 256

_RULES = [
    ("turn_budget_exceeded", re.compile(r"maximum number of turns|max[_ -]?turns", re.I)),
    (
        "context_exhausted",
        re.compile(
            r"prompt is too long|context[_ -]?length[_ -]?exceeded|maximum context length|context window (?:exceeded|is full|too small)|too many tokens",
            re.I,
        ),
    ),
    (
        "usage_exhausted",
        re.compile(
            r"usage limit|usage_limit|insufficient_quota|resource_exhausted|quota (?:has been )?exceeded|quota exhausted|credit balance.*(?:empty|exhausted)",
            re.I,
        ),
    ),
    ("billing_required", re.compile(r"billing|hard[_ -]?limit|payment required|credits? required", re.I)),
    (
        "authentication",
        re.compile(
            r"unauthorized|authenticat|invalid api key|login required|not signed in|sign in again|refresh token.*already used|could not be refreshed|token.*expired|session expired|no inference provider|\b401\b|\b403\b",
            re.I,
        ),
    ),
    (
        "model_unavailable",
        re.compile(r"model.*(?:not found|unavailable|unsupported|does not exist)|unknown model|invalid model", re.I),
    ),
    ("overloaded", re.compile(r"at capacity|overloaded|server is busy", re.I)),
    ("rate_limited", re.compile(r"rate[_ -]?limit|too many requests|\b429\b", re.I)),
    (
        "connection_lost",
        re.compile(
            r"connection error|fetch failed|socket hang up|econnreset|econnrefused|enotfound|epipe|premature close|network error|stream (?:closed|ended) unexpectedly|other side closed",
            re.I,
        ),
    ),
    ("timeout", re.compile(r"timed? ?out|timeout|deadline exceeded", re.I)),
    ("aborted", re.compile(r"aborted|interrupt(?:ed)?|cancelled|canceled", re.I)),
]


def classify_failure_kind(message: str) -> str:
    for kind, pattern in _RULES:
        if pattern.search(message or ""):
            return kind
    return "unknown"


def build_failure(message: str, *, model: str = "", kind: Optional[str] = None) -> Dict[str, Any]:
    return {
        "kind": kind or classify_failure_kind(message),
        "engineType": ENGINE_TYPE,
        "model": (model or ENGINE_TYPE)[:MAX_MODEL_LENGTH],
    }


# Hermes' gateway answers some failures itself, as a reply that starts with
# a warning sign ("⚠️ Provider authentication failed. …"). That text reaches
# the person as content; the structured failure rides along so PLLLA records
# it the same way as a failure we raised ourselves.
GATEWAY_ERROR_PREFIX = "⚠️"


def failure_from_gateway_reply(content: str, *, model: str = "") -> Optional[Dict[str, Any]]:
    text = (content or "").lstrip()
    if not text.startswith(GATEWAY_ERROR_PREFIX):
        return None
    return build_failure(text, model=model)


def failure_chat_text(kind: str, raw_message: str, *, runtime_label: str = "Hermes") -> str:
    detail = (raw_message or "").strip()[:200]
    if kind == "authentication":
        return f"{runtime_label}이(가) 쓰는 모델의 로그인이 만료됐거나 인증 정보가 유효하지 않아요. {runtime_label}이(가) 있는 기계에서 그 모델에 다시 로그인해 주세요. ({detail})"
    if kind == "usage_exhausted":
        return f"{runtime_label}이(가) 쓰는 모델의 사용량 한도에 도달했어요. 한도가 풀리거나 {runtime_label}에서 모델을 바꾼 뒤 다시 보내 주세요. ({detail})"
    if kind == "rate_limited":
        return f"{runtime_label}이(가) 쓰는 모델이 일시적으로 요청을 제한하고 있어요. 잠시 후 다시 보내 주세요. ({detail})"
    if kind == "billing_required":
        return f"{runtime_label}이(가) 쓰는 모델 계정의 결제 또는 크레딧 상태를 확인해 주세요. ({detail})"
    if kind == "model_unavailable":
        return f"{runtime_label}에 설정된 모델을 현재 사용할 수 없어요. {runtime_label}에서 다른 모델을 골라 주세요. ({detail})"
    if kind == "overloaded":
        return f"{runtime_label}이(가) 쓰는 모델이 지금 혼잡해요. 잠시 후 다시 보내 주세요. ({detail})"
    if kind == "timeout":
        return f"{runtime_label}의 응답 시간이 초과됐어요. 다시 보내 주세요. ({detail})"
    if kind == "context_exhausted":
        return f"이 대화가 {runtime_label} 모델의 컨텍스트 한도를 넘었어요. 새 채팅에서 이어가 주세요. ({detail})"
    if kind == "connection_lost":
        return f"{runtime_label}과(와) 모델 사이 연결이 끊겼어요. 잠시 후 다시 보내 주세요. ({detail})"
    if kind == "turn_budget_exceeded":
        return f'{runtime_label}이(가) 한 번의 실행 한도에 걸려 멈췄어요. "계속"이라고 보내면 이어가요. ({detail})'
    if kind == "aborted":
        return f"{runtime_label}의 작업이 중단됐어요. ({detail})"
    return f"{runtime_label}에서 오류가 났어요: {detail} — 자세한 내용은 {runtime_label} 로그를 봐 주세요."
