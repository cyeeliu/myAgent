"""Team protocol logic: plan approval and shutdown handshake.

Provides a single implementation of ``submit_plan``, ``request_plan``,
``request_shutdown``, and ``review_plan`` that is used by both the
boss-side (main loop) and teammate-side (leader) code paths. This
eliminates the duplicated closures that previously lived inside
``spawn_teammate_thread`` and the top-level ``run_*`` functions in
``teammates.py``.
"""
from __future__ import annotations

from agent_core.bus import (
    BUS,
    ProtocolState,
    new_request_id,
    pending_requests,
)


def submit_plan(from_name: str, plan: str, overseer: str = "boss") -> str:
    """Submit a plan for approval to the overseer (leader or boss).

    Creates a ``ProtocolState`` and sends a ``plan_approval_request``
    message via the bus. Returns a string containing the request_id
    so the caller can track the pending approval.
    """
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id,
        type="plan_approval",
        sender=from_name,
        target=overseer,
        status="pending",
        payload=plan,
    )
    BUS.send(from_name, overseer, plan, "plan_approval_request",
             {"request_id": req_id})
    return f"Plan submitted ({req_id})"


def request_plan(from_name: str, teammate: str, task: str) -> str:
    """Ask a teammate to submit a plan for a task."""
    BUS.send(from_name, teammate, f"Submit plan for: {task}", "message")
    return f"Asked {teammate} to submit a plan"


def request_shutdown(from_name: str, teammate: str) -> str:
    """Request a teammate to shut down.

    Creates a ``ProtocolState`` and sends a ``shutdown_request`` via
    the bus. The teammate responds with a ``shutdown_response`` that
    is matched by ``request_id`` in ``bus.match_response``.
    """
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id,
        type="shutdown",
        sender=from_name,
        target=teammate,
        status="pending",
        payload="",
    )
    BUS.send(from_name, teammate, "Shut down.", "shutdown_request",
             {"request_id": req_id})
    return f"Shutdown request sent to {teammate}"


def review_plan(
    reviewer: str,
    request_id: str,
    approve: bool,
    feedback: str = "",
    *,
    self_plan_guard: bool = False,
) -> str:
    """Review (approve or reject) a submitted plan.

    ``reviewer`` is the name of the agent doing the review.
    ``request_id`` identifies the pending ``ProtocolState``.
    ``self_plan_guard`` — when ``True``, refuse if the reviewer is the
    same as the plan's original submitter (prevents a leader from
    approving its own plan, which should go to the boss instead).
    """
    state = pending_requests.get(request_id)
    if not state:
        return f"Request {request_id} not found"
    if self_plan_guard and state.sender == reviewer:
        return (
            f"Request {request_id} is YOUR OWN plan submission — "
            "the boss reviews it, not you. Only review_plan a "
            "member's submitted plan (the request_id the member's "
            "submit_plan returned)."
        )
    state.status = "approved" if approve else "rejected"
    BUS.send(reviewer, state.sender,
             feedback or ("Approved" if approve else "Rejected"),
             "plan_approval_response",
             {"request_id": request_id, "approve": approve})
    return f"Plan {'approved' if approve else 'rejected'}"
