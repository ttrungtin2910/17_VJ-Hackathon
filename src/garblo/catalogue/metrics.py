"""Thống kê đo được từ audit, không suy đoán accuracy hoặc giá upstream."""

import math

from sqlalchemy import select

from garblo.models import AiCallAttempt, AiJob, GarmentCandidate


def summarize_catalogue(db, user_id: str, limit: int = 1000) -> dict:
    jobs = list(
        db.scalars(
            select(AiJob)
            .where(AiJob.user_id == user_id, AiJob.job_type == "catalogue")
            .order_by(AiJob.created_at.desc())
            .limit(limit)
        )
    )
    ids = [job.id for job in jobs]
    calls = list(db.scalars(select(AiCallAttempt).where(AiCallAttempt.job_id.in_(ids))))
    candidates = list(db.scalars(select(GarmentCandidate).where(GarmentCandidate.job_id.in_(ids))))
    latencies = sorted(call.latency_ms for call in calls if call.latency_ms is not None)

    def percentile(p):
        return latencies[max(0, math.ceil(p * len(latencies)) - 1)] if latencies else None

    valid = [call for call in calls if call.validation_status in {"valid", "invalid"}]
    corrected = sum(
        candidate.attributes_json != candidate.original_attributes
        or candidate.details_json != candidate.review_json.get("original_details", {})
        for candidate in candidates
    )
    escalated = {call.job_id for call in calls if call.stage.startswith("escalation")}
    token_usage = {}
    unknown_usage = 0
    for call in calls:
        if not call.usage:
            unknown_usage += 1
        for key, value in call.usage.items():
            token_usage[key] = token_usage.get(key, 0) + value
    return {
        "window": {"max_jobs": limit, "jobs": len(jobs)},
        "calls": len(calls),
        "unknown_usage_calls": unknown_usage,
        "tokens": token_usage,
        "model_call_latency_ms": {"p50": percentile(0.5), "p95": percentile(0.95)},
        "json_validity_rate": sum(call.validation_status == "valid" for call in valid) / len(valid)
        if valid
        else None,
        "human_correction_rate": corrected / len(candidates) if candidates else None,
        "escalation_rate": len(escalated) / len(jobs) if jobs else None,
        "retry_rate": sum(job.attempts > 1 for job in jobs) / len(jobs) if jobs else None,
        "cost_usd": None,
        "quality_accuracy": None,
        "notes": [
            "Giá và accuracy chưa được đo; null không có nghĩa là 0.",
            "Latency đo mỗi physical call, không bao gồm queue wait.",
        ],
    }
