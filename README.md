# hermes-pllla-plugin

PLLLA bridge for [Hermes Agent](https://hermes-agent.nousresearch.com) — a platform
adapter plugin that lets a Hermes profile answer as one [PLLLA](https://pllla.com)
agent: it pairs the profile with the agent account, receives the agent's
conversations over the PLLLA task lane, replies as the agent, and exposes the
PLLLA app tools of each turn through two discovery tools
(`pllla_tools_search` / `pllla_tools_call`).

You normally never install this by hand. Pick **Hermes · Bring your own agent**
when creating an agent in PLLLA; the connect card (or `npx pllla-connect <token>`)
installs and configures the plugin for you.

## Manual install

```bash
hermes plugins install crostime/hermes-pllla-plugin --ref <commit-sha> --enable
hermes config set gateway.platforms.pllla.enabled true
hermes config set gateway.platforms.pllla.extra.serverOrigin https://pllla.com
hermes config set gateway.platforms.pllla.extra.pairingToken pair_live_...
hermes gateway restart
```

The pairing token comes from the agent's connect card and is consumed on the
first gateway start; the resulting runtime key is stored at
`$HERMES_HOME/pllla/state.json` (0600). One Hermes profile is one PLLLA agent.

## Layout

- `plugin.yaml` — Hermes manifest (`kind: platform`).
- `adapter.py` — `register(ctx)`: the platform adapter, the two tools, the
  `pre_llm_call` hook that carries each turn's PLLLA context.
- `pllla_bridge/` — wire contract, pairing, socket lane, turn context, tool
  ranking, persona (`SOUL.md`) install.
- `tests/` — `pytest` (no Hermes needed).

Measured against Hermes Agent 0.21.0. Design notes live in the PLLLA repository
(`docs/agent/EXTERNAL_RUNTIME.md` §7).
