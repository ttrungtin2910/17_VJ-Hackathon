"""Chỉ chọn fallback từ snapshot và audit của hai attempts vision transient."""

from dataclasses import replace

from sqlalchemy import select

from garblo.jobs import attempt_limit
from garblo.models import AiCallAttempt, AiJob


def execution_claim(worker, claim):
    policy = claim.ref.get("vision_fallback")
    if (
        claim.job_type != "catalogue"
        or claim.provider != "openai"
        or not isinstance(policy, dict)
        or policy.get("version") != 1
        or policy.get("provider") != "fpt"
        or policy.get("primary_attempts") != 2
        or not isinstance(policy.get("model"), str)
        or not policy["model"].strip()
        or attempt_limit(worker.settings, claim.ref) < 3
    ):
        return claim
    db = worker.db
    job = db.get(AiJob, claim.id)
    if job.attempts != 3:
        return claim
    calls = list(
        db.scalars(
            select(AiCallAttempt)
            .where(AiCallAttempt.job_id == claim.id)
            .order_by(AiCallAttempt.call_index)
        )
    )
    # Không fallback sau schema repair, safety/consent, segmentation, crash với
    # kết quả chưa rõ hoặc attempts bị reclaim mà không có physical-call audit.
    if any(
        call.provider != "openai"
        or call.status == "reserved"
        or call.validation_status == "invalid"
        or call.error_code not in {None, "provider_transient_error"}
        for call in calls
    ):
        return claim
    for attempt in (1, 2):
        previous = [call for call in calls if call.worker_attempt == attempt]
        if (
            not previous
            or previous[-1].error_code != "provider_transient_error"
            or previous[-1].stage not in {"screening", "analysis", "escalation"}
        ):
            return claim
    return replace(
        claim,
        provider="fpt",
        ref={
            **claim.ref,
            "provider": "fpt",
            "model": policy["model"],
            "native_structured_output": False,
            "screening_model": policy.get("screening_model"),
            "escalation_model": policy.get("escalation_model"),
        },
    )
