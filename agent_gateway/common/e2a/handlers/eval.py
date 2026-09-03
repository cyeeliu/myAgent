"""Evaluation handlers: eval.datasets.list / eval.run.start / list / get / cancel / delete / compare / trend."""
from __future__ import annotations

from ...schema.agent import AgentResponse
from ...schema.message import ReqMethod
from ..dispatcher import handler, HandlerContext
from ....services.eval_run_manager import get_eval_runs


def _ensure_event_callback(mgr, ctx: HandlerContext):
    """E-C1: Wire the EvalRunManager event callback to push eval events to
    the originating session's EventPipe so they reach the WS frontend.

    Idempotent — safe to call on every eval.run.start.
    """
    if mgr._event_callback is not None:
        return  # already wired

    def _eval_event_callback(session_id: str, kind: str, payload: dict):
        """Publish an eval event to the session's EventPipe."""
        if not session_id:
            return
        try:
            gs = ctx.sessions.get(session_id)
            if gs is None:
                return
            # Generate a seq number from the session's agent counter.
            seq = getattr(gs.agent, "_seq", 0) + 1
            gs.pipe.publish(seq, kind, payload)
        except Exception:
            pass  # best-effort — don't break the eval run

    mgr.set_event_callback(_eval_event_callback)


@handler(ReqMethod.EVAL_DATASETS_LIST)
async def eval_datasets_list(req, ctx: HandlerContext):
    mgr = get_eval_runs()
    return AgentResponse(req.request_id, payload={"datasets": mgr.list_datasets()})


@handler(ReqMethod.EVAL_RUN_START)
async def eval_run_start(req, ctx: HandlerContext):
    dataset = str(req.params.get("dataset") or "").strip()
    model = str(req.params.get("model") or "").strip()
    # E-L4: Clamp repeat to [1, 100] to prevent excessive concurrency.
    repeat = max(1, min(100, int(req.params.get("repeat") or 1)))
    mode = str(req.params.get("mode") or "online").strip()
    limit = max(0, min(10000, int(req.params.get("limit") or 0)))
    if not dataset or not model:
        return AgentResponse(req.request_id, ok=False, error="dataset and model are required")
    mgr = get_eval_runs()
    # E-C1: Wire the event callback so eval events reach the WS pipe.
    _ensure_event_callback(mgr, ctx)
    try:
        run_id = mgr.start(dataset, model, repeat, mode, limit, session_id=req.session_id or "")
        return AgentResponse(req.request_id, payload={"run_id": run_id})
    except Exception as e:
        return AgentResponse(req.request_id, ok=False, error=str(e))


@handler(ReqMethod.EVAL_RUN_LIST)
async def eval_run_list(req, ctx: HandlerContext):
    offset = int(req.params.get("offset") or 0)
    limit = int(req.params.get("limit") or 20)
    mgr = get_eval_runs()
    return AgentResponse(req.request_id, payload={"runs": mgr.list_runs(offset, limit)})


@handler(ReqMethod.EVAL_RUN_GET)
async def eval_run_get(req, ctx: HandlerContext):
    run_id = str(req.params.get("run_id") or "").strip()
    if not run_id:
        return AgentResponse(req.request_id, ok=False, error="run_id is required")
    mgr = get_eval_runs()
    report = mgr.get_run(run_id)
    if report is None:
        return AgentResponse(req.request_id, ok=False, error="run not found")
    return AgentResponse(req.request_id, payload=report)


@handler(ReqMethod.EVAL_RUN_CANCEL)
async def eval_run_cancel(req, ctx: HandlerContext):
    run_id = str(req.params.get("run_id") or "").strip()
    mgr = get_eval_runs()
    ok = mgr.cancel(run_id)
    return AgentResponse(req.request_id, payload={"cancelled": ok})


@handler(ReqMethod.EVAL_RUN_DELETE)
async def eval_run_delete(req, ctx: HandlerContext):
    run_id = str(req.params.get("run_id") or "").strip()
    mgr = get_eval_runs()
    ok = mgr.delete(run_id)
    return AgentResponse(req.request_id, payload={"deleted": ok})


@handler(ReqMethod.EVAL_COMPARE)
async def eval_compare(req, ctx: HandlerContext):
    run_ids = req.params.get("run_ids") or []
    if not isinstance(run_ids, list) or len(run_ids) < 2:
        return AgentResponse(req.request_id, ok=False, error="run_ids must be a list of >= 2 run IDs")
    mgr = get_eval_runs()
    return AgentResponse(req.request_id, payload=mgr.compare(run_ids))


@handler(ReqMethod.EVAL_TREND)
async def eval_trend(req, ctx: HandlerContext):
    dataset = str(req.params.get("dataset") or "").strip()
    metric = str(req.params.get("metric") or "").strip()
    if not dataset or not metric:
        return AgentResponse(req.request_id, ok=False, error="dataset and metric are required")
    mgr = get_eval_runs()
    return AgentResponse(req.request_id, payload={"points": mgr.trend(dataset, metric)})
