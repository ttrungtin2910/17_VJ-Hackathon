"""Tạo ảnh đồng thời; chỉ luồng điều phối được truy cập ledger/DB."""

import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from garblo.ai import AIProviderError
from garblo.catalogue.budget import CallLedger
from garblo.images import sanitize_image


def _generate(provider, image, mime, description):
    reports = []
    provider.on_result = lambda *args: reports.append(args)
    provider.on_validation = None
    started = time.monotonic()
    try:
        generated = provider.generate_component_image(
            image=image,
            mime_type=mime,
            description=description,
            before_call=lambda: None,  # Đã reserve/kiểm consent, lease trước submit.
        )
        return generated, reports, None, int((time.monotonic() - started) * 1000)
    except Exception as exc:
        return None, reports, exc, int((time.monotonic() - started) * 1000)


def generate_parallel(worker, claim, analysis, image, mime, primary_provider):
    model = claim.ref.get("image_model")
    if not model:
        raise AIProviderError("Job thiếu OpenAI image model", "provider_not_configured")
    missing = deque(
        (index, component, False) for index, component in enumerate(analysis.result.items)
    )
    pending, segments = {}, {}
    concurrency = claim.ref["component_image_concurrency"]
    active_providers = set()

    def complete(future):
        index, component, ledger, provider = pending.pop(future)
        try:
            generated, reports, error, _elapsed = future.result()
            # Mỗi tác vụ chỉ có một physical call và một reservation riêng.
            if reports:
                ledger.on_result(*reports[0])
            ledger.finish_unknown(getattr(error, "code", "provider_error") if error else None)
            if error:
                raise error
            try:
                clean, output_mime = sanitize_image(
                    generated, worker.settings.max_upload_bytes, worker.settings.max_image_pixels
                )
                if output_mime != "image/png":
                    raise ValueError("not PNG")
            except ValueError as exc:
                ledger.on_validation("invalid_model_output")
                raise AIProviderError("PNG không hợp lệ", "invalid_model_output") from exc
            ledger.on_validation()
            segments[index] = {
                "candidate_index": index,
                "image_bytes": clean,
                "mime_type": output_mime,
            }
        except AIProviderError as exc:
            if exc.code != "invalid_model_output":
                raise
            missing.append((index, component, True))
        finally:
            active_providers.discard(id(provider))
            close = getattr(provider, "close", None)
            if close:
                close()

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        try:
            while missing or pending:
                while missing and len(pending) < concurrency:
                    index, component, repair = missing[0]
                    ledger = CallLedger(worker, claim)
                    ledger.provider, ledger.stage, ledger.model = "openai", "component_image", model
                    if repair:
                        ledger.repair_stage = ledger.stage
                    # Không dùng lại primary hoặc instance đang có request chạy.
                    provider = worker.providers.resolve(
                        "openai", worker.settings.openai_vlm_model or model, model
                    )
                    if provider is primary_provider or id(provider) in active_providers:
                        raise AIProviderError(
                            "Tạo ảnh cần provider riêng", "provider_request_error"
                        )
                    try:
                        ledger.before_call()
                    except AIProviderError as exc:
                        close = getattr(provider, "close", None)
                        if close:
                            close()
                        # Usage của call đang chạy có thể giải phóng token đã reserve.
                        if pending and exc.code in {
                            "model_call_budget_exceeded",
                            "token_budget_exceeded",
                        }:
                            break
                        raise
                    missing.popleft()
                    active_providers.add(id(provider))
                    future = executor.submit(
                        _generate, provider, image, mime, component.model_dump(mode="json")
                    )
                    pending[future] = (index, component, ledger, provider)
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                # Thu nhận hết audit trong đợt hoàn thành trước khi gửi thêm request.
                error = None
                for future in done:
                    try:
                        complete(future)
                    except Exception as exc:
                        error = error or exc
                if error:
                    raise error
        finally:
            # Request đã gửi vẫn có thể bị tính phí: chờ và lưu audit kể cả job thất bại.
            for future in list(pending):
                try:
                    complete(future)
                except Exception:
                    pass
    return [segments[index] for index in range(len(analysis.result.items))]
