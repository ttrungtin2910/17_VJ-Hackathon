"""Audit riêng từng request; không hứa exactly-once billing khi provider timeout."""

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from garblo.ai import AIProviderError
from garblo.models import AiCallAttempt, AiJob

logger = logging.getLogger(__name__)


class CallLedger:
    def __init__(self, worker, claim):
        self.worker, self.claim = worker, claim
        self.started = time.monotonic()
        self.pending_id = None
        self.last_id = None
        self.repair_stage = None
        self.call_started = None
        self.stage = "analysis"
        self.model = claim.ref["model"]
        self.provider = claim.provider

    def before_call(self):
        worker, claim = self.worker, self.claim
        worker._before_call(claim)
        self.ensure_deadline()
        job = worker._owned_claim(claim, lock=True)
        if not job:
            worker.db.rollback()
            raise AIProviderError("Worker đã mất lease", "worker_lease_lost")
        calls = list(
            worker.db.scalars(select(AiCallAttempt).where(AiCallAttempt.job_id == claim.id))
        )
        if len(calls) >= claim.ref.get("max_calls", worker.settings.catalogue_max_calls):
            worker.db.rollback()
            raise AIProviderError("Đã hết ngân sách gọi model", "model_call_budget_exceeded")
        tokens = sum(self.charged_tokens(call) for call in calls)
        reserve = claim.ref.get(
            "call_token_reservation", worker.settings.catalogue_call_token_reservation
        )
        if tokens + reserve > claim.ref.get("token_budget", worker.settings.catalogue_token_budget):
            worker.db.rollback()
            raise AIProviderError(
                "Không đủ ngân sách token để giữ chỗ request", "token_budget_exceeded"
            )
        if job.execution_deadline_at is None:
            job.execution_deadline_at = datetime.now(timezone.utc) + timedelta(
                seconds=claim.ref.get("deadline_seconds", 300)
            )
        stage = self.stage + "_repair" if self.repair_stage == self.stage else self.stage
        self.repair_stage = None
        call = AiCallAttempt(
            job_id=job.id,
            user_id=job.user_id,
            call_index=len(calls) + 1,
            worker_attempt=job.attempts,
            stage=stage,
            provider=self.provider,
            model=self.model,
            prompt_version=claim.ref.get("prompt_version"),
            schema_version=claim.ref.get("schema_version", 1),
            reserved_tokens=reserve,
        )
        worker.db.add(call)
        worker.db.commit()  # Reservation tồn tại kể cả process chết ngay sau request.
        self.pending_id = call.id
        self.call_started = time.monotonic()

    def ensure_deadline(self):
        db = self.worker.db
        deadline = db.scalar(select(AiJob.execution_deadline_at).where(AiJob.id == self.claim.id))
        db.rollback()
        if deadline and (
            deadline.astimezone(timezone.utc)
            if deadline.tzinfo
            else deadline.replace(tzinfo=timezone.utc)
        ) <= datetime.now(timezone.utc):
            raise AIProviderError("Đã hết thời gian xử lý", "job_deadline_exceeded")

    @staticmethod
    def charged_tokens(call):
        usage = call.usage
        if "total_tokens" in usage:
            return usage["total_tokens"]
        if {"prompt_tokens", "completion_tokens"} <= usage.keys():
            return usage["prompt_tokens"] + usage["completion_tokens"]
        if {"input_tokens", "output_tokens"} <= usage.keys():
            return usage["input_tokens"] + usage["output_tokens"]
        # Timeout hoặc thiếu usage: không hoàn lại reservation một cách suy đoán.
        return call.reserved_tokens

    def on_result(self, usage, latency_ms, error_code=None):
        if not self.pending_id:
            return
        db = self.worker.db
        db.rollback()
        call = db.get(AiCallAttempt, self.pending_id)
        if call and call.status == "reserved":
            call.status = "failed" if error_code else "succeeded"
            call.usage = self.worker._safe_usage(usage)
            call.latency_ms = max(0, int(latency_ms))
            call.error_code = error_code
            if error_code == "invalid_model_output":
                call.validation_status = "invalid"
                self.repair_stage = self.stage
            db.commit()
            logger.info(
                "AI call=%s job=%s provider=%s model=%s attempt=%s stage=%s latency_ms=%s status=%s",
                call.id,
                call.job_id,
                call.provider,
                call.model,
                call.worker_attempt,
                call.stage,
                call.latency_ms,
                call.status,
            )
        self.last_id, self.pending_id = self.pending_id, None

    def on_validation(self, error_code=None):
        if not self.last_id:
            return
        db = self.worker.db
        call = db.get(AiCallAttempt, self.last_id)
        if call:
            call.validation_status = "invalid" if error_code else "valid"
            call.error_code = error_code
            db.commit()
        self.repair_stage = self.stage if error_code else None

    def finish_unknown(self, error_code=None):
        # Adapter legacy/test không có hook: biết thời gian nhưng không đoán usage.
        if self.pending_id:
            elapsed = int((time.monotonic() - self.call_started) * 1000)
            self.on_result({}, elapsed, error_code)
