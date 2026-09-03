import asyncio
import json
import os

import pytest

from pllla_bridge.pairing import (
    PairingError,
    consume_pairing_token,
    load_state,
    parse_pair_response,
    save_state,
    state_file_path,
)

from _fixtures import PAIR_RESPONSE


def test_parse_pair_response_keeps_the_contract_and_identity():
    state = parse_pair_response(PAIR_RESPONSE, server_origin="https://pllla.com", account_id="agent-1")
    assert state.runtime_key == "rt-abc"
    assert state.identity.system_prompt == "Be brief."
    assert state.socket.task_event == "agent:task"
    assert state.socket.events["dispatchTool"] == "agent:dispatch-tool"
    assert state.account_id == "agent-1"
    assert state.owner_user_id == "owner1"
    # Older pair responses carried the owner only inside aiUser.
    nested = {**PAIR_RESPONSE, "aiUser": {**PAIR_RESPONSE["aiUser"], "ownerUserId": "owner2"}}
    nested.pop("ownerUserId")
    assert parse_pair_response(nested, server_origin="x").owner_user_id == "owner2"


def test_parse_pair_response_rejects_wrong_generation_and_missing_contract():
    with pytest.raises(PairingError, match="contract v1"):
        parse_pair_response({**PAIR_RESPONSE, "contractVersion": 2}, server_origin="x")
    with pytest.raises(PairingError, match="missing the socket contract"):
        parse_pair_response({**PAIR_RESPONSE, "socket": {}}, server_origin="x")
    with pytest.raises(PairingError, match="expired"):
        parse_pair_response({"success": False, "error": "Pairing token expired"}, server_origin="x")


def test_state_round_trips_with_0600_permissions(tmp_path):
    state = parse_pair_response(PAIR_RESPONSE, server_origin="https://pllla.com")
    path = state_file_path(tmp_path)
    save_state(path, state)
    assert path.exists()
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    loaded = load_state(path)
    assert loaded is not None
    assert loaded.runtime_key == "rt-abc"
    assert loaded.socket.events["task"] == "agent:task"
    assert loaded.identity.name == "Hermes Bot"
    assert loaded.owner_user_id == "owner1"
    # 손상된 파일은 "페어링 없음" 으로 본다.
    path.write_text("{not json", encoding="utf-8")
    assert load_state(path) is None


def test_consume_pairing_token_posts_the_token_and_label():
    calls = []

    async def fake_post(url, body):
        calls.append((url, body))
        return 200, PAIR_RESPONSE

    state = asyncio.run(
        consume_pairing_token(
            server_origin="https://pllla.com/",
            pairing_token="pair_live_x",
            runtime_label="hermes",
            http_post=fake_post,
        )
    )
    assert calls == [("https://pllla.com/api/user/ai-agent/pair", {"pairingToken": "pair_live_x", "runtimeLabel": "hermes"})]
    assert state.server_origin == "https://pllla.com/"
    assert json.loads(json.dumps(state.to_json()))["runtimeKey"] == "rt-abc"
