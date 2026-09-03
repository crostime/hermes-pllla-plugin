import asyncio

from pllla_bridge.lane import PendingTurn, PendingTurns, PlllaLane
from pllla_bridge.pairing import parse_pair_response
from _fixtures import PAIR_RESPONSE


class FakeSio:
    """The tiny surface PlllaLane uses from socketio.AsyncClient."""

    def __init__(self):
        self.handlers = {}
        self.emitted = []
        self.calls = []
        self.connected = False
        self.connect_args = None
        self.call_response = {"ok": True, "result": {"echo": 1}}

    def on(self, event):
        def decorator(fn):
            self.handlers[event] = fn
            return fn

        return decorator

    async def connect(self, url, **kwargs):
        self.connected = True
        self.connect_args = (url, kwargs)
        await self.handlers["connect"]()

    async def disconnect(self):
        self.connected = False

    async def emit(self, event, payload):
        self.emitted.append((event, payload))

    async def call(self, event, payload):
        self.calls.append((event, payload))
        return self.call_response


def _state():
    return parse_pair_response(PAIR_RESPONSE, server_origin="https://pllla.com")


def test_pending_turns_fifo_per_chat_and_expiry():
    pending = PendingTurns(turn_timeout=0.01)
    pending.push(PendingTurn(chat_id="c1", task_id="t1"))
    pending.push(PendingTurn(chat_id="c1", task_id="t2"))
    pending.push(PendingTurn(chat_id="c2", task_id=None))
    assert len(pending) == 3
    assert pending.pop("c1").task_id == "t1"
    assert pending.peek("c1").task_id == "t2"
    pending.remove("c1", "t2")
    assert pending.peek("c1") is None
    # 만료된 턴은 조용히 버려진다.
    import time

    time.sleep(0.02)
    assert pending.pop("c2") is None


def test_lane_connects_with_the_contract_and_routes_task_and_greeting():
    sio = FakeSio()
    seen_tasks, greetings, statuses = [], [], []

    async def on_task(task):
        seen_tasks.append(task)

    async def on_greeting(chat_id):
        greetings.append(chat_id)

    lane = PlllaLane(
        _state(),
        on_task=on_task,
        on_greeting=on_greeting,
        on_status=lambda kind, detail: statuses.append(kind),
        socket_factory=lambda: sio,
        log=lambda line: None,
    )

    async def run():
        await lane.start()
        assert sio.connect_args[0] == "https://pllla.com"
        assert sio.connect_args[1]["socketio_path"] == "/socket.io"
        assert sio.connect_args[1]["auth"] == {"apiKey": "rt-abc"}
        assert lane.connected
        await sio.handlers["agent:task"]({"task": {"id": "t1", "chatId": "c1", "messages": []}})
        await sio.handlers["agent:task"]({"nope": 1})
        await sio.handlers["agent:runtime_ready"]({"greeting": {"shouldSend": True, "chatId": "c9"}})
        await sio.handlers["agent:runtime_ready"]({"greeting": {"shouldSend": False, "chatId": "c9"}})
        await lane.emit_response("t1", "hello")
        await lane.emit_response(
            "t2", "", failure={"kind": "unknown", "engineType": "hermes", "model": "hermes"}
        )
        assert await lane.send_chat_message("c9", "hi") is True
        result = await lane.dispatch_tool("t1", "pllla.library.search", {"query": "x"})
        await lane.stop()
        return result

    result = asyncio.run(run())
    assert [task["id"] for task in seen_tasks] == ["t1"]
    assert greetings == ["c9"]
    assert statuses[0] == "connected"
    assert sio.emitted == [
        ("agent:response", {"taskId": "t1", "aiUserId": "u1", "content": "hello"}),
        (
            "agent:response",
            {
                "taskId": "t2",
                "aiUserId": "u1",
                "content": "",
                "failure": {"kind": "unknown", "engineType": "hermes", "model": "hermes"},
            },
        ),
        ("agent:send_message", {"chatId": "c9", "aiUserId": "u1", "content": "hi"}),
    ]
    assert sio.calls == [("agent:dispatch-tool", {"taskId": "t1", "toolName": "pllla.library.search", "toolInput": {"query": "x"}})]
    assert result == {"ok": True, "result": {"echo": 1}}
    assert not lane.connected


def test_create_chat_uses_the_ack_and_returns_none_when_refused_or_absent():
    sio = FakeSio()
    lane = PlllaLane(_state(), on_task=lambda task: None, socket_factory=lambda: sio, log=lambda line: None)

    async def run():
        assert await lane.create_chat("owner1") is None  # not connected yet
        await lane.start()
        sio.call_response = {"success": True, "data": {"chatId": "c-owner", "isNew": True}}
        created = await lane.create_chat("owner1")
        sio.call_response = {"code": "AUTH_FORBIDDEN", "error": "You can only create direct chats with friends"}
        refused = await lane.create_chat("stranger")
        await lane.stop()
        return created, refused

    created, refused = asyncio.run(run())
    assert created == "c-owner"
    assert refused is None
    assert sio.calls == [
        ("agent:create_chat", {"participants": ["owner1"], "aiUserId": "u1"}),
        ("agent:create_chat", {"participants": ["stranger"], "aiUserId": "u1"}),
    ]


def test_dispatch_without_a_lane_is_an_honest_failure():
    lane = PlllaLane(_state(), on_task=lambda task: None, socket_factory=lambda: FakeSio(), log=lambda line: None)
    result = asyncio.run(lane.dispatch_tool("t", "x", {}))
    assert result["ok"] is False
    assert result["code"] == "PLLLA_LANE_DISCONNECTED"
