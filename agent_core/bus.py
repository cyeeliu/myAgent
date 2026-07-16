"""agent_core.bus — extracted from code.py (s20 comprehensive agent)."""
from dataclasses import dataclass, asdict, field
import json
import random
import time
from agent_core.env import session_dir, terminal_print


def _mailbox_dir():
    return session_dir() / ".mailboxes"

# Lead wait-lock listeners, keyed by str(session_dir()). When a teammate/leader
# writes to the "lead" mailbox, the matching listener (registered by the main
# loop's `wait` tool) pokes the session's WaitLock so a blocked agent resumes
# immediately instead of waiting for the timeout. The teammate thread runs with
# the lead's session_dir() restored (spawn_teammate_thread captures/restores it),
# so the key computed in MessageBus.send matches the one the lead registered.
_lead_listeners: dict[str, object] = {}


def register_lead_listener(session_path, cb):
    """Register a callback(content, msg_type) poked when a message is sent to
    the "lead" mailbox under session_path (= session_dir())."""
    _lead_listeners[str(session_path)] = cb


def unregister_lead_listener(session_path):
    _lead_listeners.pop(str(session_path), None)


class MessageBus:
    def send(self, from_agent: str, to_agent: str, content: str,
             msg_type: str = "message", metadata: dict = None):
        msg = {"from": from_agent, "to": to_agent,
               "content": content, "type": msg_type,
               "ts": time.time(), "metadata": metadata or {}}
        mdir = _mailbox_dir()
        inbox = mdir / f"{to_agent}.jsonl"
        with open(inbox, "a") as f:
            f.write(json.dumps(msg) + "\n")
        terminal_print(f"  \033[33m[bus] {from_agent} → {to_agent}: "
                       f"({msg_type}) {content[:50]}\033[0m")
        # Poke a waiting lead so its `wait` tool resumes instantly. The mailbox
        # line is already written, so even if there's no listener the next
        # check_inbox will find it — this is just a latency optimization + a
        # way to unblock a wait that has no timeout-pressure.
        if to_agent == "lead":
            cb = _lead_listeners.get(str(mdir.parent))
            if cb is not None:
                try:
                    cb(content, msg_type)
                except Exception:
                    pass

    def read_inbox(self, agent: str) -> list[dict]:
        inbox = _mailbox_dir() / f"{agent}.jsonl"
        if not inbox.exists():
            return []
        msgs = [json.loads(line) for line in inbox.read_text().splitlines()
                if line.strip()]
        inbox.unlink()
        return msgs

    def has_inbox(self, agent: str) -> bool:
        """Peek whether the agent's mailbox has any undrained messages, WITHOUT
        draining it. Used by the `wait` tool to close the check-then-wait race
        (a message that arrived between check_inbox and wait would otherwise
        only be noticed on the next wake/timeout)."""
        inbox = _mailbox_dir() / f"{agent}.jsonl"
        try:
            return inbox.exists() and inbox.stat().st_size > 0
        except Exception:
            return False

BUS = MessageBus()

@dataclass
class ProtocolState:
    request_id: str
    type: str
    sender: str
    target: str
    status: str
    payload: str
    created_at: float = field(default_factory=time.time)

pending_requests: dict[str, ProtocolState] = {}

def new_request_id() -> str:
    return f"req_{random.randint(0, 999999):06d}"

def match_response(response_type: str, request_id: str, approve: bool):
    # Responses are matched by request_id so one protocol reply cannot approve
    # a different pending request.
    state = pending_requests.get(request_id)
    if not state:
        return
    if state.type == "shutdown" and response_type != "shutdown_response":
        return
    if state.type == "plan_approval" and response_type != "plan_approval_response":
        return
    state.status = "approved" if approve else "rejected"

def consume_lead_inbox(route_protocol=True) -> list[dict]:
    msgs = BUS.read_inbox("lead")
    if route_protocol:
        for msg in msgs:
            meta = msg.get("metadata", {})
            req_id = meta.get("request_id", "")
            msg_type = msg.get("type", "")
            if req_id and msg_type.endswith("_response"):
                match_response(msg_type, req_id, meta.get("approve", False))
    return msgs
