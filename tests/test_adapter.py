"""The adapter outside Hermes (``HERMES_AVAILABLE`` is False under pytest):
the home-channel sentinel and the reply correlation are pure enough to test."""

import asyncio

from pllla.adapter import PlllaAdapter, ensure_home_channel_env
from pllla.pllla_bridge.contract import HOME_CHANNEL_ENV, HOME_CHANNEL_OWNER
from pllla.pllla_bridge.lane import PendingTurn, PendingTurns
from pllla.pllla_bridge.pairing import parse_pair_response

from _fixtures import PAIR_RESPONSE


class FakeLane:
    def __init__(self, *, owner_chat="c-owner"):
        self.pending = PendingTurns()
        self.connected = True
        self.responses = []
        self.sent = []
        self.created = []
        self.owner_chat = owner_chat

    async def emit_response(self, task_id, content, failure=None):
        self.responses.append((task_id, content, failure))

    async def send_chat_message(self, chat_id, content):
        self.sent.append((chat_id, content))
        return True

    async def create_chat(self, target_user_id):
        self.created.append(target_user_id)
        return self.owner_chat


def _adapter(*, owner_user_id="owner1"):
    adapter = PlllaAdapter(config=object())
    state = parse_pair_response(PAIR_RESPONSE, server_origin="https://pllla.com")
    state.owner_user_id = owner_user_id
    adapter._state = state
    adapter._lane = FakeLane()
    return adapter


def test_home_channel_env_defaults_to_the_owner_sentinel_and_keeps_a_chosen_chat():
    env = {}
    assert ensure_home_channel_env(env) == HOME_CHANNEL_OWNER
    assert env[HOME_CHANNEL_ENV] == HOME_CHANNEL_OWNER
    chosen = {HOME_CHANNEL_ENV: "6a98c31e237363edfdd6267d"}
    assert ensure_home_channel_env(chosen) == "6a98c31e237363edfdd6267d"


def test_send_to_the_owner_sentinel_creates_the_owner_dm_once_then_reuses_it():
    adapter = _adapter()
    lane = adapter._lane

    async def run():
        first = await adapter.send(HOME_CHANNEL_OWNER, "cron result")
        second = await adapter.send(HOME_CHANNEL_OWNER, "another")
        return first, second

    first, second = asyncio.run(run())
    assert first["success"] and second["success"]
    assert lane.created == ["owner1"]
    assert lane.sent == [("c-owner", "cron result"), ("c-owner", "another")]
    assert asyncio.run(adapter.get_chat_info(HOME_CHANNEL_OWNER))["name"] == "Owner DM (PLLLA)"


def test_send_to_the_owner_sentinel_fails_honestly_without_an_owner_id():
    adapter = _adapter(owner_user_id="")
    result = asyncio.run(adapter.send(HOME_CHANNEL_OWNER, "x"))
    assert result["success"] is False
    assert "owner" in result["error"]
    assert adapter._lane.created == []


def test_send_answers_the_pending_task_and_frees_the_turn():
    adapter = _adapter()
    lane = adapter._lane

    async def run():
        turn = PendingTurn(chat_id="c1", task_id="t1", done=asyncio.get_event_loop().create_future())
        lane.pending.push(turn)
        result = await adapter.send("c1", "hello")
        return result, turn.done.done()

    result, freed = asyncio.run(run())
    assert result == {"success": True, "message_id": "t1", "error": None}
    assert freed
    assert lane.responses == [("t1", "hello", None)]
    assert lane.sent == []
