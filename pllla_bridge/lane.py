"""The PLLLA task lane — one Socket.IO connection per profile/account.

Speaks the self-describing socket contract the pair response handed us
(url/path/transports/event names), the same one pllla-agent and the OpenClaw
bridge use (docs/agent/EXTERNAL_RUNTIME.md §1, §6.3):

  server → us:  events.task        {task: {...}}
                events.runtimeReady {greeting: {shouldSend, chatId}}
  us → server:  events.response    {taskId, aiUserId, content, failure?}
                events.sendMessage {chatId, aiUserId, content}
                events.dispatchTool (ack) {taskId, toolName, toolInput}

Hermes returns the agent's reply through ``adapter.send(chat_id, …)`` rather
than as a return value, so the lane remembers which task each chat is
answering (``PendingTurns``) — the correlation §7.3 called out.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional

from .contract import (
    DISPATCH_TIMEOUT_SECONDS,
    DISPATCH_TOOL_EVENT_DEFAULT,
    TURN_TIMEOUT_SECONDS,
)
from .pairing import PairState

logger = logging.getLogger("pllla.lane")


@dataclass
class PendingTurn:
    """A chat is waiting for Hermes to answer this task (or greeting)."""

    chat_id: str
    task_id: Optional[str]  # None = first greeting → sendMessage instead of response
    started_at: float = field(default_factory=time.monotonic)
    done: "asyncio.Future[None] | None" = None


class PendingTurns:
    """chat_id → FIFO of turns awaiting a reply. Pure, unit-tested."""

    def __init__(self, *, turn_timeout: float = TURN_TIMEOUT_SECONDS):
        self._by_chat: Dict[str, Deque[PendingTurn]] = {}
        self._turn_timeout = turn_timeout

    def push(self, turn: PendingTurn) -> None:
        self._by_chat.setdefault(turn.chat_id, deque()).append(turn)

    def peek(self, chat_id: str) -> Optional[PendingTurn]:
        queue = self._by_chat.get(chat_id)
        self._drop_expired(queue)
        return queue[0] if queue else None

    def pop(self, chat_id: str) -> Optional[PendingTurn]:
        queue = self._by_chat.get(chat_id)
        self._drop_expired(queue)
        if not queue:
            return None
        turn = queue.popleft()
        if not queue:
            self._by_chat.pop(chat_id, None)
        return turn

    def remove(self, chat_id: str, task_id: Optional[str]) -> None:
        queue = self._by_chat.get(chat_id)
        if not queue:
            return
        kept = deque(turn for turn in queue if turn.task_id != task_id)
        if kept:
            self._by_chat[chat_id] = kept
        else:
            self._by_chat.pop(chat_id, None)

    def _drop_expired(self, queue: Optional[Deque[PendingTurn]]) -> None:
        if not queue:
            return
        now = time.monotonic()
        while queue and now - queue[0].started_at > self._turn_timeout:
            expired = queue.popleft()
            logger.warning("Turn for chat %s (task %s) timed out without a reply", expired.chat_id, expired.task_id)

    def __len__(self) -> int:
        return sum(len(queue) for queue in self._by_chat.values())


TaskHandler = Callable[[Dict[str, Any]], Awaitable[None]]
GreetingHandler = Callable[[str], Awaitable[None]]
StatusHandler = Callable[[str, str], None]


class PlllaLane:
    """Socket.IO client for one paired account.

    ``socket_factory`` is injectable (tests pass a fake with the same tiny
    surface: ``on``, ``connect``, ``disconnect``, ``emit``, ``call``,
    ``connected``); the default builds ``socketio.AsyncClient``.
    """

    def __init__(
        self,
        state: PairState,
        *,
        on_task: TaskHandler,
        on_greeting: Optional[GreetingHandler] = None,
        on_status: Optional[StatusHandler] = None,
        socket_factory: Optional[Callable[[], Any]] = None,
        log: Callable[[str], None] = logger.info,
    ):
        self.state = state
        self.pending = PendingTurns()
        self._on_task = on_task
        self._on_greeting = on_greeting
        self._on_status = on_status
        self._socket_factory = socket_factory or _default_socket_factory
        self._sio: Any = None
        self._log = log
        self._turn_locks: Dict[str, asyncio.Lock] = {}

    # ── lifecycle ─────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return bool(self._sio is not None and getattr(self._sio, "connected", False))

    async def start(self) -> None:
        sio = self._socket_factory()
        self._sio = sio
        contract = self.state.socket

        @sio.on("connect")
        async def _connect() -> None:
            self._log("PLLLA lane connected.")
            if self._on_status:
                self._on_status("connected", "")

        @sio.on("disconnect")
        async def _disconnect(*args: Any) -> None:
            reason = str(args[0]) if args else ""
            self._log(f"PLLLA lane disconnected: {reason}")
            if self._on_status:
                self._on_status("disconnected", reason)

        @sio.on("connect_error")
        async def _connect_error(data: Any = None) -> None:
            self._log(f"PLLLA lane connect error: {data}")
            if self._on_status:
                self._on_status("error", str(data))

        ready_event = contract.events.get("runtimeReady")
        if ready_event:

            @sio.on(ready_event)
            async def _runtime_ready(payload: Any = None) -> None:
                await self._handle_runtime_ready(payload if isinstance(payload, dict) else {})

        @sio.on(contract.task_event)
        async def _task(payload: Any = None) -> None:
            task = payload.get("task") if isinstance(payload, dict) else None
            if isinstance(task, dict) and task.get("id"):
                await self._on_task(task)

        await sio.connect(
            contract.url,
            socketio_path=contract.path,
            transports=list(contract.transports),
            auth=dict(contract.auth),
        )

    async def stop(self) -> None:
        sio, self._sio = self._sio, None
        if sio is not None:
            try:
                await sio.disconnect()
            except Exception:  # noqa: BLE001 — already gone
                pass

    # ── outbound ──────────────────────────────────────────────────────────

    async def emit_response(
        self, task_id: str, content: str, failure: Optional[Dict[str, Any]] = None
    ) -> None:
        """``failure`` is the server's object shape (``failure.build_failure``) —
        never a bare string, which the server rejects and the task then times out."""
        payload: Dict[str, Any] = {
            "taskId": task_id,
            "aiUserId": self.state.ai_user.get("id", ""),
            "content": content,
        }
        if failure:
            payload["failure"] = dict(failure)
        await self._emit(self.state.socket.response_event, payload)

    async def send_chat_message(self, chat_id: str, content: str) -> bool:
        event = self.state.socket.events.get("sendMessage")
        if not event or not self.connected:
            return False
        await self._emit(event, {
            "chatId": chat_id,
            "aiUserId": self.state.ai_user.get("id", ""),
            "content": content,
        })
        return True

    async def dispatch_tool(
        self, task_id: str, tool_name: str, tool_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """The same ``agent:dispatch-tool`` ack round-trip pllla-agent uses —
        the server re-checks the task and its grant; we only carry the call."""
        event = self.state.socket.events.get("dispatchTool") or DISPATCH_TOOL_EVENT_DEFAULT
        if not self.connected:
            return {
                "ok": False,
                "code": "PLLLA_LANE_DISCONNECTED",
                "error": "The PLLLA lane is not connected right now.",
            }
        try:
            response = await asyncio.wait_for(
                self._sio.call(event, {"taskId": task_id, "toolName": tool_name, "toolInput": tool_input}),
                timeout=DISPATCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "code": "PLLLA_TOOL_TIMEOUT",
                "error": f"PLLLA tool dispatch timed out after {int(DISPATCH_TIMEOUT_SECONDS)}s",
            }
        if isinstance(response, dict):
            return response
        return {"ok": False, "error": "empty dispatch response"}

    async def create_chat(self, target_user_id: str) -> Optional[str]:
        """``agent:create_chat`` ack → the direct chat with that PLLLA user,
        created when missing (the server checks the friendship). None when
        the contract lacks the event, the lane is down, or the server refuses."""
        event = self.state.socket.events.get("createChat")
        if not event or not target_user_id or not self.connected:
            return None
        try:
            response = await asyncio.wait_for(
                self._sio.call(
                    event,
                    {"participants": [target_user_id], "aiUserId": self.state.ai_user.get("id", "")},
                ),
                timeout=DISPATCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return None
        data = response.get("data") if isinstance(response, dict) else None
        chat_id = data.get("chatId") if isinstance(data, dict) else None
        return str(chat_id) if chat_id else None

    async def _emit(self, event: str, payload: Dict[str, Any]) -> None:
        if self._sio is None:
            raise RuntimeError("PLLLA lane is not started")
        await self._sio.emit(event, payload)

    # ── turn serialization ────────────────────────────────────────────────

    def turn_lock(self, chat_id: str) -> asyncio.Lock:
        lock = self._turn_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._turn_locks[chat_id] = lock
        return lock

    async def _handle_runtime_ready(self, payload: Dict[str, Any]) -> None:
        greeting = payload.get("greeting") if isinstance(payload, dict) else None
        if not isinstance(greeting, dict) or not greeting.get("shouldSend"):
            return
        chat_id = greeting.get("chatId")
        if not chat_id or not self._on_greeting or not self.state.socket.events.get("sendMessage"):
            return
        await self._on_greeting(str(chat_id))


def _default_socket_factory() -> Any:
    import socketio  # python-socketio — installed by the connector / ensure_deps

    return socketio.AsyncClient(reconnection=True, logger=False, engineio_logger=False)


def last_user_message(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for message in reversed(messages):
        role = str(message.get("role") or "")
        if role not in ("ai", "assistant"):
            return message
    return None
