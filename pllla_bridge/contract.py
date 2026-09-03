"""Wire constants shared with the server and the other bridges.

Mirrors ``packages/openclaw-pllla-plugin/src/{pairing,lane,tools}.ts`` —
keep the two in step (docs/agent/EXTERNAL_RUNTIME.md §1 layer 1).
"""

SUPPORTED_CONTRACT_VERSION = 1

PLATFORM_NAME = "pllla"
PLATFORM_LABEL = "PLLLA"
RUNTIME_LABEL = "hermes"

# Discovery contract (docs/agent/README.md §6): exactly two tools face the
# model; every PLLLA app tool of the current task is reached through them.
SEARCH_TOOL_NAME = "pllla_tools_search"
CALL_TOOL_NAME = "pllla_tools_call"
TOOLSET_NAME = "pllla"

# Server-side names the model must use inside the discovery envelope.
PLLLA_SEARCH_NAME = "pllla.tools.search"
PLLLA_CALL_NAME = "pllla.tools.call"

# Older servers omit `events.dispatchTool`; the lane defaults it.
DISPATCH_TOOL_EVENT_DEFAULT = "agent:dispatch-tool"
DISPATCH_TIMEOUT_SECONDS = 120.0

# One turn per chat at a time; a response that never comes frees the lane.
TURN_TIMEOUT_SECONDS = 10 * 60.0

STATE_FILE_NAME = "state.json"
STATE_DIR_NAME = "pllla"

# Hermes asks every platform for a "home channel" (where cron results and
# cross-platform messages land) and nudges the first turn of every session
# until one is set. Ours is the owner's DM, named by a sentinel the adapter
# resolves on send (measured: the nudge reached the PLLLA chat, 2026-09-03).
HOME_CHANNEL_ENV = "PLLLA_HOME_CHANNEL"
HOME_CHANNEL_OWNER = "owner"

FIRST_GREETING_PROMPT = (
    "You just came online for your owner for the first time. "
    "Send a short first greeting in your own voice."
)
