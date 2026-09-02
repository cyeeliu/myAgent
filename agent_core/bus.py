"""agent_core.bus — extracted from code.py (s20 comprehensive agent)."""
from dataclasses import dataclass, asdict, field
import json
import logging
import os
import random
import time
from pathlib import Path
from agent_core.env import session_dir, terminal_print

logger = logging.getLogger(__name__)


def _mailbox_dir():
    return session_dir() / ".mailboxes"

# Boss wait-lock listeners, keyed by str(session_dir()). When a teammate/leader
# writes to the "boss" mailbox, the matching listener (registered by the main
# loop's `wait` tool) pokes the session's WaitLock so a blocked agent resumes
# immediately instead of waiting for the timeout. The teammate thread runs with
# the boss's session_dir() restored (spawn_teammate_thread captures/restores it),
# so the key computed in MessageBus.send matches the one the boss registered.
_boss_listeners: dict[str, object] = {}


def register_boss_listener(session_path, cb):
    """Register a callback(content, msg_type) poked when a message is sent to
    the "boss" mailbox under session_path (= session_dir())."""
    _boss_listeners[str(session_path)] = cb


def unregister_boss_listener(session_path):
    _boss_listeners.pop(str(session_path), None)


# Bus taps, keyed by str(session_dir()). A tap is invoked after EVERY MessageBus
# send under that session_path with (from, to, content, msg_type, metadata) so a
# watcher (e.g. start_team in cluster mode) can bridge team conversation to the
# frontend group chat. Teammate threads run with the boss's session_dir()
# restored, so the key computed in send matches the one start_team registered.
_bus_taps: dict[str, object] = {}


def register_bus_tap(session_path, cb):
    """Register a callback(from, to, content, msg_type, metadata) invoked after
    every BUS send under session_path (= session_dir())."""
    _bus_taps[str(session_path)] = cb


def unregister_bus_tap(session_path):
    _bus_taps.pop(str(session_path), None)


# A2A team-message callbacks, keyed by str(session_dir()). When a teammate sends
# a substantive message (type "result" or "message") to "boss", this callback is
# invoked so the gateway can re-invoke the boss session with a fresh turn —
# replacing the blocking `wait` tool pattern. The callback receives
# (from_agent, content, msg_type, metadata).
_team_callbacks: dict[str, object] = {}


def register_team_callback(session_path, cb):
    """Register an A2A callback(from_agent, content, msg_type, metadata) invoked
    when a teammate sends a substantive message to 'boss'. The gateway uses this
    to re-invoke the boss session with a fresh turn instead of blocking on the
    `wait` tool."""
    _team_callbacks[str(session_path)] = cb


def unregister_team_callback(session_path):
    _team_callbacks.pop(str(session_path), None)


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
        # T-M4: write a cross-process poke file so a boss running in a
        # different gateway replica can detect the new message without
        # waiting for the next poll timeout.  The poke file is a tiny
        # JSON line under .mailboxes/.pokes/; check_cross_process_pokes()
        # consumes it and fires the local in-process callbacks.
        _write_poke(mdir, from_agent, to_agent, content, msg_type, metadata)
        # Poke a waiting boss so its `wait` tool resumes instantly. The mailbox
        # line is already written, so even if there's no listener the next
        # check_inbox will find it — this is just a latency optimization + a
        # way to unblock a wait that has no timeout-pressure.
        if to_agent == "boss":
            cb = _boss_listeners.get(str(mdir.parent))
            if cb is not None:
                try:
                    cb(content, msg_type)
                except Exception:
                    pass
        # Bridge team conversation to the frontend group chat (cluster mode).
        # The tap is registered by start_team; it emits a team_message event
        # for substantive messages (filtered inside the tap). Best-effort.
        tap = _bus_taps.get(str(mdir.parent))
        if tap is not None:
            try:
                tap(from_agent, to_agent, content, msg_type, metadata or {})
            except Exception:
                pass
        # A2A: when a teammate sends a substantive message to the boss,
        # trigger the team callback so the gateway can re-invoke the boss
        # session with a fresh turn. This includes result, message, and
        # plan_approval_request (so the boss can review a leader's plan
        # without blocking on `wait`). Protocol handshake messages
        # (plan_approval_response, shutdown_request/response) are handled
        # by the boss listener / check_inbox instead.
        if to_agent == "boss" and msg_type in (
                "result", "message", "plan_approval_request"):
            tcb = _team_callbacks.get(str(mdir.parent))
            if tcb is not None:
                try:
                    tcb(from_agent, content, msg_type, metadata or {})
                except Exception:
                    pass

    def read_inbox(self, agent: str) -> list[dict]:
        """Atomically read and clear the agent's inbox.

        Uses os.rename to swap the mailbox file to a temp name before
        reading, so messages appended by BUS.send (from another thread)
        between read and delete are NOT lost (T-C3)."""
        import os as _os
        inbox = _mailbox_dir() / f"{agent}.jsonl"
        if not inbox.exists():
            return []
        # Atomic swap: rename to a unique temp file, then read the temp.
        # Any BUS.send that arrives during the read will create a NEW
        # {agent}.jsonl (since the original was renamed away), and its
        # messages will be picked up on the next read_inbox call.
        tmp = inbox.with_suffix(".jsonl.reading")
        # Ensure unique temp name if a stale .reading file exists
        i = 0
        while tmp.exists():
            i += 1
            tmp = inbox.with_suffix(f".jsonl.reading{i}")
        try:
            _os.rename(str(inbox), str(tmp))
        except FileNotFoundError:
            # Another reader got there first
            return []
        try:
            msgs = [json.loads(line) for line in tmp.read_text().splitlines()
                    if line.strip()]
        except Exception:
            logger.exception("Failed to read swapped inbox %s", tmp)
            msgs = []
        finally:
            try:
                tmp.unlink()
            except Exception:
                pass
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

def _poke_dir(mdir: Path) -> Path:
    """Directory for cross-process poke files."""
    d = mdir / ".pokes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_poke(mdir: Path, from_agent: str, to_agent: str,
                content: str, msg_type: str, metadata: dict) -> None:
    """T-M4: write a cross-process poke file so a boss in a different
    process can detect the new message immediately via
    check_cross_process_pokes() instead of waiting for the next poll."""
    try:
        d = _poke_dir(mdir)
        poke = {
            "from": from_agent, "to": to_agent,
            "type": msg_type, "ts": time.time(),
            "content": content[:200],  # truncate for poke file
        }
        fname = f"poke_{int(time.time() * 1000)}_{random.randint(0, 999999)}.json"
        (d / fname).write_text(json.dumps(poke))
    except Exception:
        pass  # best-effort; mailbox line is already written


def check_cross_process_pokes(session_path) -> int:
    """T-M4: consume cross-process poke files and fire local callbacks.

    Called from the boss's idle poll / check_inbox path.  Reads poke
    files written by other processes, fires the local ``_boss_listeners``
    / ``_bus_taps`` / ``_team_callbacks`` for each, then deletes them.
    Returns the number of pokes consumed.
    """
    sp = Path(str(session_path))
    mdir = sp / ".mailboxes"
    pdir = mdir / ".pokes"
    if not pdir.exists():
        return 0
    consumed = 0
    key = str(sp)
    for pf in sorted(pdir.glob("poke_*.json")):
        try:
            poke = json.loads(pf.read_text())
            to_agent = poke.get("to", "")
            from_agent = poke.get("from", "")
            content = poke.get("content", "")
            msg_type = poke.get("type", "")
            # Fire boss listener (immediate wakeup)
            if to_agent == "boss":
                cb = _boss_listeners.get(key)
                if cb is not None:
                    try:
                        cb(content, msg_type)
                    except Exception:
                        pass
            # Fire bus tap (frontend bridge)
            tap = _bus_taps.get(key)
            if tap is not None:
                try:
                    tap(from_agent, to_agent, content, msg_type, {})
                except Exception:
                    pass
            # Fire team callback (A2A re-invoke)
            if to_agent == "boss" and msg_type in (
                    "result", "message", "plan_approval_request"):
                tcb = _team_callbacks.get(key)
                if tcb is not None:
                    try:
                        tcb(from_agent, content, msg_type, {})
                    except Exception:
                        pass
            consumed += 1
        except Exception:
            pass
        finally:
            try:
                pf.unlink()
            except Exception:
                pass
    return consumed


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
    # T-L1: use uuid4 for collision-free request IDs instead of
    # random.randint(0, 999999) which only has 1M possibilities.
    import uuid
    return f"req_{uuid.uuid4().hex[:12]}"

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
    # T-H3: delete the entry after matching so pending_requests doesn't
    # grow unboundedly. Each request is matched at most once.
    pending_requests.pop(request_id, None)

def consume_boss_inbox(route_protocol=True) -> list[dict]:
    # T-M4: consume cross-process poke files first, firing local
    # callbacks (_boss_listeners / _bus_taps / _team_callbacks) for
    # messages sent by teammates in other gateway replicas.  This gives
    # immediate wakeup / A2A re-invoke / frontend bridge without waiting
    # for the next poll timeout.
    try:
        check_cross_process_pokes(session_dir())
    except Exception:
        pass
    msgs = BUS.read_inbox("boss")
    if route_protocol:
        for msg in msgs:
            meta = msg.get("metadata", {})
            req_id = meta.get("request_id", "")
            msg_type = msg.get("type", "")
            if req_id and msg_type.endswith("_response"):
                match_response(msg_type, req_id, meta.get("approve", False))
    # T-H3: sweep stale pending_requests (entries that never got a response).
    # Anything older than 10 minutes is unlikely to ever be matched — the
    # teammate that sent it has either exited or moved on.
    _sweep_stale_requests()
    return msgs

_REQUEST_TTL = 600  # 10 minutes

def _sweep_stale_requests():
    """Remove pending_requests older than _REQUEST_TTL seconds."""
    now = time.time()
    stale = [rid for rid, st in pending_requests.items()
             if now - st.created_at > _REQUEST_TTL]
    for rid in stale:
        pending_requests.pop(rid, None)
