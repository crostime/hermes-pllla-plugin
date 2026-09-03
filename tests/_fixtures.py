"""Shared fixtures for the bridge tests."""

PAIR_RESPONSE = {
    "success": True,
    "contractVersion": 1,
    "runtimeKey": "rt-abc",
    "aiUser": {"id": "u1", "username": "hermesbot", "name": "Hermes Bot"},
    "ownerUserId": "owner1",
    "identity": {"name": "Hermes Bot", "bio": "helper", "systemPrompt": "Be brief."},
    "externalRuntime": {"id": "hermes", "label": "Hermes"},
    "socket": {
        "url": "https://pllla.com",
        "path": "/socket.io",
        "transports": ["polling", "websocket"],
        "protocol": "socket.io",
        "auth": {"apiKey": "rt-abc"},
        "events": {
            "runtimeReady": "agent:runtime_ready",
            "task": "agent:task",
            "response": "agent:response",
            "sendMessage": "agent:send_message",
            "createChat": "agent:create_chat",
            "dispatchTool": "agent:dispatch-tool",
        },
    },
}
