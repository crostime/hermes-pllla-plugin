"""PLLLA pairing exchange and the per-profile state file.

One POST turns a one-time ``pair_live_…`` token into everything the bridge
needs: the rt- runtime key, the self-describing socket contract (url, path,
transports, event names), and the agent's identity for the persona
(docs/agent/EXTERNAL_RUNTIME.md §1). One Hermes profile is one PLLLA agent,
so the state file holds exactly one account.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .contract import SUPPORTED_CONTRACT_VERSION


class PairingError(RuntimeError):
    """The server refused the token or the response is unusable."""


@dataclass
class SocketContract:
    url: str
    path: str
    transports: List[str]
    protocol: str
    auth: Dict[str, str]
    events: Dict[str, str]

    @property
    def task_event(self) -> str:
        return self.events["task"]

    @property
    def response_event(self) -> str:
        return self.events["response"]


@dataclass
class Identity:
    name: str = ""
    bio: str = ""
    system_prompt: str = ""


@dataclass
class PairState:
    contract_version: int
    runtime_key: str
    ai_user: Dict[str, str]
    identity: Identity
    external_runtime: Optional[Dict[str, str]]
    socket: SocketContract
    server_origin: str
    account_id: str = ""
    # The agent's owner — whose DM is the home channel (contract.HOME_CHANNEL_OWNER).
    owner_user_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {
            "contractVersion": self.contract_version,
            "runtimeKey": self.runtime_key,
            "aiUser": dict(self.ai_user),
            "ownerUserId": self.owner_user_id,
            "identity": asdict(self.identity),
            "externalRuntime": dict(self.external_runtime)
            if self.external_runtime
            else None,
            "socket": asdict(self.socket),
            "serverOrigin": self.server_origin,
            "accountId": self.account_id,
        }

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "PairState":
        socket = data.get("socket") or {}
        identity = data.get("identity") or {}
        ai_user = dict(data.get("aiUser") or {})
        return PairState(
            contract_version=int(data.get("contractVersion") or 0),
            runtime_key=str(data.get("runtimeKey") or ""),
            ai_user=ai_user,
            owner_user_id=str(data.get("ownerUserId") or ai_user.get("ownerUserId") or ""),
            identity=Identity(
                name=str(identity.get("name") or ""),
                bio=str(identity.get("bio") or ""),
                system_prompt=str(
                    identity.get("system_prompt") or identity.get("systemPrompt") or ""
                ),
            ),
            external_runtime=dict(data["externalRuntime"])
            if data.get("externalRuntime")
            else None,
            socket=SocketContract(
                url=str(socket.get("url") or ""),
                path=str(socket.get("path") or "/socket.io"),
                transports=list(socket.get("transports") or ["polling", "websocket"]),
                protocol=str(socket.get("protocol") or "socket.io"),
                auth=dict(socket.get("auth") or {}),
                events=dict(socket.get("events") or {}),
            ),
            server_origin=str(data.get("serverOrigin") or ""),
            account_id=str(data.get("accountId") or ""),
        )


def parse_pair_response(
    data: Any, *, server_origin: str, account_id: str = ""
) -> PairState:
    """Validate the server's pair response the way the TypeScript bridge does."""
    if not isinstance(data, dict) or not data.get("success"):
        error = data.get("error") if isinstance(data, dict) else None
        raise PairingError(str(error or "PLLLA pairing failed."))
    socket = data.get("socket") or {}
    events = socket.get("events") or {}
    if not data.get("runtimeKey") or not socket.get("url") or not events.get("task"):
        raise PairingError("PLLLA pairing response is missing the socket contract.")
    version = int(data.get("contractVersion") or 0)
    if version != SUPPORTED_CONTRACT_VERSION:
        raise PairingError(
            f"This bridge speaks PLLLA contract v{SUPPORTED_CONTRACT_VERSION}, "
            f"but the server speaks v{version}. Update the plugin: "
            "hermes plugins update pllla"
        )
    state = PairState.from_json(
        {
            **data,
            "serverOrigin": server_origin,
            "accountId": account_id,
        }
    )
    state.contract_version = version
    return state


async def consume_pairing_token(
    *,
    server_origin: str,
    pairing_token: str,
    runtime_label: str,
    account_id: str = "",
    http_post=None,
) -> PairState:
    """POST /api/user/ai-agent/pair. ``http_post`` is injectable for tests;
    the default uses aiohttp (always present in the Hermes venv)."""
    url = f"{server_origin.rstrip('/')}/api/user/ai-agent/pair"
    body = {"pairingToken": pairing_token, "runtimeLabel": runtime_label}
    if http_post is None:
        http_post = _aiohttp_post_json
    status, data = await http_post(url, body)
    if status >= 400 and not (isinstance(data, dict) and data.get("error")):
        raise PairingError(f"PLLLA pairing failed (HTTP {status}).")
    return parse_pair_response(data, server_origin=server_origin, account_id=account_id)


async def _aiohttp_post_json(url: str, body: Dict[str, Any]):
    import aiohttp  # Hermes venv ships it

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30)
    ) as session:
        async with session.post(url, json=body) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:  # noqa: BLE001 — non-JSON error body
                data = None
            return response.status, data


def state_file_path(hermes_home: Path) -> Path:
    from .contract import STATE_DIR_NAME, STATE_FILE_NAME

    return Path(hermes_home) / STATE_DIR_NAME / STATE_FILE_NAME


def load_state(path: Path) -> Optional[PairState]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("runtimeKey"):
        return None
    return PairState.from_json(data)


def save_state(path: Path, state: PairState) -> None:
    """0600 — the runtime key is a credential."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_json(), indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
