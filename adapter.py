"""Hermes-facing side of the PLLLA bridge: the platform adapter, the two
discovery tools, and the ``pre_llm_call`` hook.

Measured against Hermes Agent 0.21.0 (2026-09-03): ``ctx.register_platform``
builds a ``PlatformEntry``; the gateway calls ``adapter_factory(PlatformConfig)``
for ``gateway.platforms.pllla`` and then ``connect()``; inbound turns go
through ``BasePlatformAdapter.handle_message(MessageEvent)`` and the reply
comes back as ``adapter.send(chat_id, content)`` from the stream consumer.
Tools register only inside ``register(ctx)``; turn context reaches their
handlers through a ContextVar (``pllla_bridge.turns``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .pllla_bridge.contract import (
    CALL_TOOL_NAME,
    FIRST_GREETING_PROMPT,
    HOME_CHANNEL_ENV,
    HOME_CHANNEL_OWNER,
    PLATFORM_LABEL,
    PLATFORM_NAME,
    RUNTIME_LABEL,
    SEARCH_TOOL_NAME,
    TOOLSET_NAME,
    TURN_TIMEOUT_SECONDS,
)
from .pllla_bridge.deps import ensure_socketio, socketio_available
from .pllla_bridge.failure import (
    build_failure,
    failure_chat_text,
    failure_from_gateway_reply,
)
from .pllla_bridge.lane import PendingTurn, PlllaLane
from .pllla_bridge.pairing import (
    PairState,
    PairingError,
    consume_pairing_token,
    load_state,
    save_state,
    state_file_path,
    token_fingerprint,
)
from .pllla_bridge.persona import install_persona
from .pllla_bridge.turns import (
    CALL_TOOL_SCHEMA,
    CURRENT_TASK,
    SEARCH_TOOL_SCHEMA,
    TaskContext,
    context_for_prompt,
    handle_call,
    handle_search,
    turn_text,
)

logger = logging.getLogger("pllla.adapter")


def _hermes_home() -> Path:
    try:
        from hermes_cli.config import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001 — outside Hermes (tests)
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def ensure_home_channel_env(environ: Any = os.environ) -> str:
    """Hermes reads a platform's home channel from ``<PLATFORM>_HOME_CHANNEL``
    (gateway/run.py, cron/scheduler.py). Default ours to the owner-DM
    sentinel; a person who set a real chat id keeps it."""
    current = str(environ.get(HOME_CHANNEL_ENV) or "").strip()
    if current:
        return current
    environ[HOME_CHANNEL_ENV] = HOME_CHANNEL_OWNER
    return HOME_CHANNEL_OWNER


def _extra(config: Any) -> Dict[str, Any]:
    extra = getattr(config, "extra", None)
    return dict(extra) if isinstance(extra, dict) else {}


def _server_origin(config: Any) -> str:
    return str(
        _extra(config).get("serverOrigin")
        or os.environ.get("PLLLA_SERVER_ORIGIN")
        or "https://pllla.com"
    ).rstrip("/")


def _pairing_token(config: Any) -> str:
    return str(_extra(config).get("pairingToken") or os.environ.get("PLLLA_PAIRING_TOKEN") or "").strip()


def _is_connected(config: Any) -> bool:
    """Registry ``is_connected``: credentials present — a saved pairing or a token to consume."""
    return state_file_path(_hermes_home()).exists() or bool(_pairing_token(config))


def _validate_config(config: Any) -> bool:
    """Registry contract: True = usable. Logged hint instead of a returned string."""
    if _is_connected(config):
        return True
    logger.warning(
        "[pllla] not paired yet — set gateway.platforms.pllla.extra.pairingToken "
        "(from the agent's connect card) or run: npx pllla-connect <token> --runtime hermes"
    )
    return False


def _check_deps() -> bool:
    return socketio_available()


def _ensure_deps() -> bool:
    return ensure_socketio(_hermes_home())


def _build_adapter(config: Any) -> "PlllaAdapter":
    return PlllaAdapter(config)


def _pre_llm_call(**kwargs: Any) -> Optional[Dict[str, str]]:
    """Inject the server's per-task prompt into this turn's user message."""
    if str(kwargs.get("platform") or "") != PLATFORM_NAME:
        return None
    task = CURRENT_TASK.get()
    if task is None:
        return None
    context = context_for_prompt(task)
    return {"context": context} if context else None


def register(ctx: Any) -> None:
    """Plugin entry point — called once by the Hermes plugin system."""
    ctx.register_platform(
        name=PLATFORM_NAME,
        label=PLATFORM_LABEL,
        adapter_factory=_build_adapter,
        check_fn=_check_deps,
        validate_config=_validate_config,
        is_connected=_is_connected,
        required_env=[],
        install_hint="pip install python-socketio (the connector does this for you)",
        ensure_deps_fn=_ensure_deps,
        max_message_length=PlllaAdapter.MAX_MESSAGE_LENGTH,
        cron_deliver_env_var=HOME_CHANNEL_ENV,
        emoji="🟣",
    )
    ctx.register_tool(
        name=SEARCH_TOOL_NAME,
        toolset=TOOLSET_NAME,
        schema=SEARCH_TOOL_SCHEMA,
        handler=handle_search,
        is_async=True,
        description=SEARCH_TOOL_SCHEMA["description"],
        emoji="🔎",
    )
    ctx.register_tool(
        name=CALL_TOOL_NAME,
        toolset=TOOLSET_NAME,
        schema=CALL_TOOL_SCHEMA,
        handler=handle_call,
        is_async=True,
        description=CALL_TOOL_SCHEMA["description"],
        emoji="🛠️",
    )
    ctx.register_hook("pre_llm_call", _pre_llm_call)


try:  # Hermes runtime — absent under plain pytest, where the adapter is not exercised.
    from gateway.config import Platform, PlatformConfig  # type: ignore
    from gateway.platforms.base import (  # type: ignore
        BasePlatformAdapter,
        MessageEvent,
        MessageType,
        SendResult,
    )

    HERMES_AVAILABLE = True
except Exception:  # noqa: BLE001
    HERMES_AVAILABLE = False
    BasePlatformAdapter = object  # type: ignore[misc,assignment]


class PlllaAdapter(BasePlatformAdapter):  # type: ignore[misc]
    """One Hermes profile ↔ one PLLLA agent account."""

    MAX_MESSAGE_LENGTH = 100_000
    supports_code_blocks = True

    def __init__(self, config: Any):
        if HERMES_AVAILABLE:
            super().__init__(config, Platform(PLATFORM_NAME))
        else:  # tests
            self.config = config
        self._state: Optional[PairState] = None
        self._lane: Optional[PlllaLane] = None
        self._home = _hermes_home()
        # The owner's DM — learned from the first greeting or created on demand.
        self._owner_chat_id: Optional[str] = None
        # PlatformConfig defaults: keep operator-flavoured gateway pings out
        # of the agent's PLLLA chat and never chunk the reply.
        try:
            config.gateway_restart_notification = False
            config.typing_indicator = False
        except Exception:  # noqa: BLE001
            pass

    # ── Hermes capability flags ───────────────────────────────────────────

    @property
    def authorization_is_upstream(self) -> bool:
        # PLLLA authorized the sender before the task reached this lane (an
        # authenticated rt- socket; sender ids are PLLLA accounts, not Hermes
        # platform accounts an operator could allowlist here) — the relay
        # adapter's contract, measured: without it the gateway logs
        # "Unauthorized user … on pllla" and drops every turn (2026-09-03).
        return True

    @property
    def supports_draft_streaming(self) -> bool:  # type: ignore[override]
        return False

    def accepts_tool_progress(self) -> bool:  # type: ignore[override]
        return False

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        try:
            state = await self._resolve_state()
        except PairingError as error:
            logger.error("[pllla] pairing failed: %s", error)
            return False
        if not ensure_socketio(self._home):
            logger.error("[pllla] python-socketio is not installed and could not be installed")
            return False
        ensure_home_channel_env()
        if self._lane is not None:
            await self._lane.stop()
        self._lane = PlllaLane(
            state,
            on_task=self._handle_task,
            on_greeting=self._handle_greeting,
            on_status=self._on_lane_status,
            log=lambda line: logger.info("[pllla] %s", line),
        )
        await self._lane.start()
        self._running = True
        return True

    async def disconnect(self) -> None:
        if self._lane is not None:
            await self._lane.stop()
            self._lane = None
        if HERMES_AVAILABLE:
            self._mark_disconnected()
        else:
            self._running = False

    def _on_lane_status(self, kind: str, detail: str) -> None:
        if not HERMES_AVAILABLE:
            return
        if kind == "connected":
            self._mark_connected()
        elif kind == "disconnected":
            self._mark_disconnected()

    async def _resolve_state(self) -> PairState:
        """The pairing to run on.

        A saved pairing is reused unless the config carries a token this
        pairing did not come from — then the new token is consumed and
        replaces it (a new agent, or a re-issued token after the old key
        expired). If that new token turns out unusable (already consumed,
        expired) the saved pairing stays in force and the token is marked
        seen so the next start does not retry it.
        """
        if self._state is not None:
            return self._state
        path = state_file_path(self._home)
        saved = load_state(path)
        token = _pairing_token(self.config)
        fingerprint = token_fingerprint(token)
        if saved is not None and (not fingerprint or saved.pairing_fingerprint == fingerprint):
            self._state = saved
            return saved
        if not token:
            raise PairingError(
                "PLLLA is not paired: no saved pairing and no pairingToken in gateway.platforms.pllla.extra"
            )
        try:
            state = await self._consume(
                server_origin=_server_origin(self.config),
                pairing_token=token,
                runtime_label=RUNTIME_LABEL,
                account_id=str(_extra(self.config).get("accountId") or ""),
            )
        except PairingError as error:
            if saved is None:
                raise
            logger.warning(
                "[pllla] the configured pairing token was not accepted (%s) — keeping the saved pairing for %s",
                error,
                saved.ai_user.get("username") or saved.account_id or "this agent",
            )
            saved.pairing_fingerprint = fingerprint
            save_state(path, saved)
            self._state = saved
            return saved
        state.pairing_fingerprint = fingerprint
        save_state(path, state)
        # The persona comes with the pairing — Hermes reads SOUL.md as slot 1.
        install_persona(self._home, state.identity)
        if saved is not None:
            logger.info(
                "[pllla] re-paired: %s → %s",
                saved.ai_user.get("username") or saved.account_id or "?",
                state.ai_user.get("username") or state.account_id or "?",
            )
            self._owner_chat_id = None
        self._state = state
        return state

    # Seam for tests; the module function is the real thing.
    _consume = staticmethod(consume_pairing_token)

    # ── inbound: PLLLA task → Hermes turn ─────────────────────────────────

    async def _handle_task(self, task: Dict[str, Any]) -> None:
        assert self._lane is not None
        chat_id = str(task.get("chatId") or "")
        task_id = str(task.get("id") or "")
        if not chat_id or not task_id:
            return
        sender = task.get("sender") if isinstance(task.get("sender"), dict) else {}
        tools = task.get("tools") if isinstance(task.get("tools"), list) else []
        context = TaskContext(
            task_id=task_id,
            chat_id=chat_id,
            system_prompt=str(task.get("systemPrompt") or ""),
            tools=[tool for tool in tools if isinstance(tool, dict)],
            dispatch=self._lane.dispatch_tool,
        )
        text = turn_text(task) or "(empty message)"
        async with self._lane.turn_lock(chat_id):
            turn = PendingTurn(chat_id=chat_id, task_id=task_id, done=asyncio.get_event_loop().create_future())
            self._lane.pending.push(turn)
            token = CURRENT_TASK.set(context)
            try:
                await self._deliver(chat_id, text, sender, message_id=task_id)
            finally:
                CURRENT_TASK.reset(token)
            try:
                await asyncio.wait_for(turn.done, timeout=TURN_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                self._lane.pending.remove(chat_id, task_id)
                message = "Hermes did not answer within the turn timeout."
                await self._lane.emit_response(
                    task_id,
                    failure_chat_text("timeout", message),
                    failure=build_failure(message, kind="timeout"),
                )
            except Exception as error:  # noqa: BLE001 — surfaced honestly
                self._lane.pending.remove(chat_id, task_id)
                message = str(error)
                failure = build_failure(message)
                await self._lane.emit_response(
                    task_id, failure_chat_text(failure["kind"], message), failure=failure
                )

    async def _handle_greeting(self, chat_id: str) -> None:
        assert self._lane is not None
        # The first greeting goes to the owner — that chat is the home channel.
        self._owner_chat_id = chat_id
        context = TaskContext(task_id="", chat_id=chat_id, system_prompt="", is_greeting=True)
        async with self._lane.turn_lock(chat_id):
            turn = PendingTurn(chat_id=chat_id, task_id=None, done=asyncio.get_event_loop().create_future())
            self._lane.pending.push(turn)
            token = CURRENT_TASK.set(context)
            try:
                await self._deliver(chat_id, FIRST_GREETING_PROMPT, {}, message_id=f"greeting:{chat_id}")
            finally:
                CURRENT_TASK.reset(token)
            try:
                await asyncio.wait_for(turn.done, timeout=TURN_TIMEOUT_SECONDS)
            except Exception:  # noqa: BLE001
                self._lane.pending.remove(chat_id, None)

    async def _deliver(self, chat_id: str, text: str, sender: Dict[str, Any], *, message_id: str) -> None:
        if not HERMES_AVAILABLE:
            return
        user_id = str(sender.get("id") or chat_id)
        user_name = str(sender.get("username") or sender.get("name") or "PLLLA user")
        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            user_id=user_id,
            user_name=user_name,
            source=self.build_source(
                chat_id=chat_id,
                chat_name=f"PLLLA chat {chat_id}",
                chat_type="dm",
                user_id=user_id,
                user_name=user_name,
            ),
            message_id=message_id,
        )
        await self.handle_message(event)

    # ── outbound: Hermes reply → PLLLA ────────────────────────────────────

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        lane = self._lane
        if lane is None:
            return _send_result(False, error="PLLLA lane is not started")
        resolved = await self._resolve_chat_id(str(chat_id))
        if resolved is None:
            return _send_result(
                False,
                error="PLLLA owner chat is unknown — pair again so the bridge learns the owner.",
            )
        turn = lane.pending.pop(resolved)
        try:
            if turn is not None and turn.task_id:
                # A gateway-side error reply ("⚠️ Provider authentication
                # failed …") stays as the text, with the structured failure
                # riding along so PLLLA records the kind.
                await lane.emit_response(
                    turn.task_id, content, failure=failure_from_gateway_reply(content)
                )
            else:
                # First greeting, a home-channel delivery (cron), or a message
                # Hermes initiated on its own.
                if not await lane.send_chat_message(resolved, content):
                    return _send_result(False, error="PLLLA lane is not connected")
        finally:
            if turn is not None and turn.done is not None and not turn.done.done():
                turn.done.set_result(None)
        return _send_result(True, message_id=turn.task_id if turn else None)

    async def _resolve_chat_id(self, chat_id: str) -> Optional[str]:
        """The home-channel sentinel → the owner's DM; anything else is a real chat id."""
        if chat_id != HOME_CHANNEL_OWNER:
            return chat_id
        if self._owner_chat_id:
            return self._owner_chat_id
        owner_id = self._state.owner_user_id if self._state is not None else ""
        if not owner_id or self._lane is None:
            return None
        self._owner_chat_id = await self._lane.create_chat(owner_id)
        return self._owner_chat_id

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        if chat_id == HOME_CHANNEL_OWNER:
            return {"id": chat_id, "name": "Owner DM (PLLLA)", "type": "dm"}
        return {"id": chat_id, "name": f"PLLLA chat {chat_id}", "type": "dm"}


def _send_result(success: bool, *, message_id: Optional[str] = None, error: Optional[str] = None) -> Any:
    if HERMES_AVAILABLE:
        return SendResult(success=success, message_id=message_id, error=error)
    return {"success": success, "message_id": message_id, "error": error}
