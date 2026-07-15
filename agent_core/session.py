"""agent_core.session — extracted from code.py (s20 comprehensive agent)."""
from dataclasses import dataclass, asdict, field
import queue
import threading


EVENT_KINDS = ("token", "text", "tool_start", "tool_result",
               "error", "permission_request", "ask_user", "widget", "compacted",
               "done", "context_usage", "task_notification", "memory", "todo")

class EventSink:
    """Protocol: emit(kind, payload). Subclasses render or buffer the event."""
    streaming = False  # ChannelSink overrides → live token streaming for API.

    def emit(self, kind: str, payload: dict):
        raise NotImplementedError

class TerminalSink(EventSink):
    # CLI: reproduce the exact prints the old loop did. No token streaming —
    # the final assistant text is printed by print_turn_assistants after the
    # turn, exactly as before.
    streaming = False

    def emit(self, kind: str, payload: dict):
        if kind == "tool_start":
            print(f"\033[36m> {payload.get('name')}\033[0m")
        elif kind == "tool_result":
            print(str(payload.get("content", ""))[:300])
        elif kind == "text":
            # Notices (cron inject, max_tokens) — old code used plain print.
            print(payload.get("text", ""))
        elif kind == "permission_request":
            print(f"\n\033[33m[permission] {payload.get('reason')}\033[0m")
            print(f"  {payload.get('detail', '')}")
        elif kind == "task_notification":
            print(f"\033[33m[background] {payload.get('task_id','?')} done: "
                  f"{str(payload.get('summary',''))[:120]}\033[0m")

class ChannelSink(EventSink):
    # API: each event is enqueued as a frame {seq, kind, payload} on a
    # thread-safe queue. The gateway's WS/SSE pump drains it to the client.
    streaming = True

    def __init__(self, out: "queue.Queue" = None):
        self.out = out if out is not None else queue.Queue()

    def emit(self, kind: str, payload: dict):
        self.out.put({"kind": kind, "payload": payload})

class RecordingSink(EventSink):
    # Tests: capture the full event sequence for assertion. No network.
    def __init__(self):
        self.events: list[dict] = []

    def emit(self, kind: str, payload: dict):
        self.events.append({"kind": kind, "payload": payload})

class Permission:
    """Protocol: request(block) → {allow: bool, modify: str|None}."""
    def request(self, block) -> dict:
        raise NotImplementedError

class CliPermission(Permission):
    # CLI: the original input("Allow? [y/N]") prompt.
    def request(self, block) -> dict:
        choice = input("  Allow? [y/N] ").strip().lower()
        return {"allow": choice in ("y", "yes"), "modify": None}

class FuturePermission(Permission):
    # API: block on a future the gateway resolves from a permission_response
    # frame (WS) or POST .../permissions/{request_id}/respond (SSE).
    def __init__(self, resolver, timeout: float = 120.0):
        # resolver(block, request_id) → Future; gateway calls grant(request_id, {allow, modify}).
        self.resolver = resolver
        self.timeout = timeout

    def request(self, block) -> dict:
        import uuid
        request_id = uuid.uuid4().hex[:12]
        fut = self.resolver(block, request_id)
        try:
            return fut.result(timeout=self.timeout)
        except Exception:
            return {"allow": False, "modify": None}

@dataclass
class Session:
    """Per-conversation state lifted out of module globals.

    Holds the chat record + LLM context (split so compaction can never destroy
    the durable conversation), side context, todo nudge counter, a per-session
    lock, the transport label, the event sinks (fan-out), and the permission
    object. `emit` stamps a monotonic seq on every event and fans out to all sinks.
    """
    record: list = field(default_factory=list)             # append-only chat record; NEVER compacted
    context_messages: list = field(default_factory=list)   # compactable LLM input context
    context: dict = field(default_factory=dict)
    transport: str = "cli"           # cli | ws | sse
    sinks: list = field(default_factory=list)
    record_sinks: list = field(default_factory=list)  # chat-record append hooks (e.g. chat:{sid} stream)
    permission: Permission = None
    ask_resolver: object = None   # gateway hook: ask_resolver(request_id) → Future;
                                 # set by GatewaySession so the ask_user tool can
                                 # block on a user answer from the WS client.
    lock: threading.RLock = field(default_factory=threading.RLock)
    rounds_since_todo: int = 0
    todos: list = field(default_factory=list)   # per-session todo list (todo_write); emitted to the TodoList panel
    interrupted: bool = False
    workdir: object = None        # per-session WORKDIR (workspace/<sid>/); set by gateway
    mcp_clients: dict = field(default_factory=dict)  # per-session MCP connections
    on_background_complete: object = None  # gateway hook: re-trigger the loop when
                                           # a background task finishes after the
                                           # turn ended. See GatewaySession.
    _seq: int = 0

    def append_both(self, msg: dict) -> None:
        """Append a message to the chat record (never compacted) AND the LLM
        context (compactable). Every turn-level append goes through here so the
        durable record and the working context stay in sync until compaction
        trims the context. Also fans out to record_sinks (e.g. the Redis
        chat:{sid} stream) so the durable chat record is populated live."""
        self.record.append(msg)
        self.context_messages.append(msg)
        for sink in self.record_sinks:
            try:
                sink.append(msg)
            except Exception:
                pass

    def append_context(self, msg: dict) -> None:
        """Append an agent-internal control message to the LLM context ONLY —
        not to the durable record and not to record_sinks. Used for nudges the
        model needs to see but the user must not: the todo `<reminder>`, the
        max_tokens `CONTINUATION_PROMPT`, and the `[Compacted. ...]` marker.
        Routing these through `append_both` leaked them to the live `chat.user`
        event (record_sinks → WS → frontend user bubble) and into the durable
        record (history.json / replay). They live in `context_messages` so the
        LLM still acts on them; compaction may later trim them as usual."""
        self.context_messages.append(msg)

    def emit(self, kind: str, payload: dict = None):
        if payload is None:
            payload = {}
        with self.lock:
            self._seq += 1
            seq = self._seq
        for sink in self.sinks:
            try:
                sink.emit(kind, {**payload, "seq": seq})
            except Exception:
                pass  # a sink failure must not break the agent loop.

    @property
    def streaming(self) -> bool:
        return any(getattr(s, "streaming", False) for s in self.sinks)
