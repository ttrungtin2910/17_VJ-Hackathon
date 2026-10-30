from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from garblo.catalogue.contracts import Component, ComponentDetails, GarmentAttributes


@dataclass(frozen=True)
class QualityAssessment:
    attributes: dict[str, Any]
    details: dict[str, Any]
    reasons: list[str]

    @property
    def needs_review(self) -> bool:
        return bool(self.reasons)


def assess_quality(
    attributes: GarmentAttributes, details: ComponentDetails | None = None
) -> QualityAssessment:
    """Heuristic có thể audit, không phải confidence hoặc quyền tự động commit.

    Chỉ category/color trống và uncertainty khai báo mới cần cảnh báo. Thiếu chất
    liệu/fit không phải lỗi: một ảnh thường không đủ bằng chứng để suy ra chúng.
    Mọi component vẫn phải qua human approval, kể cả khi không có cảnh báo.
    """
    if details is None:
        details = attributes.details if isinstance(attributes, Component) else ComponentDetails()
    legacy = GarmentAttributes.model_validate(
        {name: getattr(attributes, name) for name in GarmentAttributes.model_fields}
    )
    reasons = [
        f"missing_{name}"
        for name in ("category", "color")
        if getattr(legacy, name) is None or not getattr(legacy, name).strip()
    ]
    reasons.extend(f"uncertain_{name}" for name in sorted(set(details.uncertain_fields)))
    return QualityAssessment(legacy.model_dump(), details.model_dump(), reasons)


def assess_component(component: Component) -> QualityAssessment:
    return assess_quality(component)
