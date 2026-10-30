"""MVP xác định: tối đa 8 món/vai trò, 512 tổ hợp/template, 1024 tổng.

Không suy luận thuộc tính thiếu, không gọi provider, không gợi ý mua sắm.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from itertools import islice, product

from garblo.recommendations.schemas import (
    EditConstraints,
    GarmentSnapshot,
    RecommendationContext,
    ScoreBreakdown,
)

ENGINE_VERSION = "rules-v1"
POOL_PER_ROLE = 8
MAX_PER_TEMPLATE = 512
MAX_COMBINATIONS = 1024
FEATURE_CAP = 3.0
PERSONALIZATION_CAP = 8.0
SCORE_NOTICE = "Điểm xếp hạng theo quy tắc, không phải độ tin cậy hay độ chính xác của AI."
ROLE_ORDER = {"top": 0, "bottom": 1, "dress": 2, "shoes": 3, "outerwear": 4, "accessory": 5}


def normalize(text: str | None) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower().replace("đ", "d"))
    return " ".join("".join(c for c in text if unicodedata.category(c) != "Mn").split())


CATEGORIES = {
    "top": ("top", "shirt", "t-shirt", "tshirt", "blouse", "sweater", "ao"),
    "bottom": ("bottom", "pants", "trousers", "shorts", "skirt", "quan", "chan vay"),
    "shoes": ("shoes", "shoe", "sneakers", "boots", "sandals", "giay", "dep"),
    "dress": ("dress", "jumpsuit", "vay lien", "dam"),
    "outerwear": ("outerwear", "jacket", "coat", "blazer", "ao khoac"),
    "accessory": ("accessory", "accessories", "phu kien"),
}


def category_role(category: str | None) -> str | None:
    value = normalize(category)
    # Ghép cụm dài trước để áo khoác không trở thành áo.
    matches = [
        (len(alias), role)
        for role, aliases in CATEGORIES.items()
        for alias in aliases
        if value == alias or value.startswith(alias + " ")
    ]
    return max(matches)[1] if matches else None


STYLE_ALIASES = {
    "formal": ("formal", "trang trong", "cong so", "lich su"),
    "smart-casual": ("smart-casual", "smart casual", "thanh lich", "cong so"),
    "casual": ("casual", "thuong ngay", "don gian"),
    "minimal": ("minimal", "minimalist", "toi gian"),
    "streetwear": ("streetwear", "duong pho"),
    "elegant": ("elegant", "thanh lich", "sang trong"),
    "sporty": ("sporty", "sport", "the thao"),
}
COLORS = {
    "black": ("black", "den"),
    "white": ("white", "trang"),
    "gray": ("gray", "grey", "xam"),
    "beige": ("beige", "be"),
    "navy": ("navy", "xanh navy", "xanh dam"),
    "blue": ("blue", "xanh duong"),
    "red": ("red", "do"),
    "green": ("green", "xanh la"),
    "brown": ("brown", "nau"),
}


def color_key(value: str | None) -> str:
    value = normalize(value)
    return next((key for key, aliases in COLORS.items() if value in aliases), value)


def evidence(item: GarmentSnapshot) -> str:
    uncertain = set(item.details.get("uncertain_fields", []))
    parts = []
    for field in ("category", "material", "season"):
        if field not in uncertain:
            parts.append(getattr(item, field) or "")
    if "tags" not in uncertain:
        parts.extend(item.tags)
    for field in ("subcategory", "seasons"):
        if field not in uncertain:
            value = item.details.get(field)
            parts.extend(value if isinstance(value, list) else [value or ""])
    return normalize(" ".join(parts))


def has_cue(text: str, cues: tuple[str, ...]) -> bool:
    return any(f" {cue} " in f" {text} " for cue in cues)


def style_keys(item: GarmentSnapshot) -> set[str]:
    uncertain = item.details.get("uncertain_fields", [])
    values = ([] if "tags" in uncertain else item.tags) + (
        [] if "styles" in uncertain else item.details.get("styles", [])
    )
    text = normalize(" ".join(values))
    return {key for key, aliases in STYLE_ALIASES.items() if has_cue(text, aliases)}


def weather_allowed(item: GarmentSnapshot, context: RecommendationContext) -> bool:
    if not context.weather:
        return True
    text = evidence(item)
    weather = context.weather.id
    if weather in ("cold", "snowy") and has_cue(
        text, ("summer only", "chi mua he", "tank top", "sandal", "sandals", "dep", "ao ba lo")
    ):
        return False
    if weather in ("sunny", "humid") and has_cue(
        text, ("winter only", "chi mua dong", "ao phao", "down jacket", "heavy wool")
    ):
        return False
    return not (
        weather in ("rainy", "snowy")
        and has_cue(text, ("suede", "da lon", "not waterproof", "khong chong nuoc"))
    )


def features(items: list[GarmentSnapshot]) -> list[str]:
    result = set()
    for item in items:
        result.add(f"role:{item.role}")
        if item.color and "color" not in item.details.get("uncertain_fields", []):
            result.add(f"color:{color_key(item.color)}")
        result.update(f"style:{style}" for style in style_keys(item))
    return sorted(result)


def learn_weights(signals: list[tuple[str, list[str]]]) -> dict[str, float]:
    """Mỗi outfit chỉ có một latest signal; shrinkage 4, cap từng feature 3."""
    sums: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    for signal, snapshot in signals:
        for feature in set(snapshot):
            sums[feature] += 1 if signal == "like" else -1
            counts[feature] += 1
    return {
        key: round(max(-FEATURE_CAP, min(FEATURE_CAP, FEATURE_CAP * value / (counts[key] + 4))), 4)
        for key, value in sorted(sums.items())
    }


def complete(items: list[GarmentSnapshot], context: RecommendationContext) -> bool:
    ids = [item.garment_id for item in items]
    roles = [item.role for item in items]
    core = set(roles) - {"outerwear", "accessory"}
    return (
        len(ids) == len(set(ids))
        and len(roles) == len(set(roles))
        and core in ({"top", "bottom", "shoes"}, {"dress", "shoes"})
        and all(category_role(item.category) == item.role for item in items)
        and all(weather_allowed(item, context) for item in items)
    )


def matches_constraints(item: GarmentSnapshot, constraints: EditConstraints | None) -> bool:
    if constraints is None:
        return True
    uncertain = set(item.details.get("uncertain_fields", []))
    if constraints.colors and "color" in uncertain:
        return False
    if constraints.materials and "material" in uncertain:
        return False
    return (
        (not constraints.colors or color_key(item.color) in map(color_key, constraints.colors))
        and (
            not constraints.materials
            or normalize(item.material) in map(normalize, constraints.materials)
        )
        and (not constraints.styles or bool(style_keys(item) & set(constraints.styles)))
    )


@dataclass
class RankedOutfit:
    items: list[GarmentSnapshot]
    score: float
    breakdown: ScoreBreakdown
    explanation: list[str]
    features: list[str]

    @property
    def stable_key(self) -> tuple[str, ...]:
        return tuple(item.garment_id for item in self.items)


def score_outfit(
    items: list[GarmentSnapshot], context: RecommendationContext, weights: dict[str, float]
) -> RankedOutfit:
    items = sorted(items, key=lambda item: (ROLE_ORDER[item.role], item.garment_id))
    styles = set().union(*(style_keys(item) for item in items))
    event_styles = {
        "work": {"formal", "smart-casual", "minimal"},
        "school": {"casual", "smart-casual"},
        "interview": {"formal", "smart-casual"},
        "presentation": {"formal", "smart-casual"},
        "date": {"elegant", "smart-casual"},
        "wedding": {"formal", "elegant"},
        "casual": {"casual", "streetwear", "sporty", "minimal"},
    }.get(context.event.id, set())
    context_score = min(20.0, 10.0 * len(styles & event_styles))
    # Chỉ cộng tính thuận tiện di chuyển khi chính món đồ có bằng chứng.
    if set(context.transports) & {"walk", "bicycle", "motorbike"}:
        shoes = next((item for item in items if item.role == "shoes"), None)
        if shoes and has_cue(evidence(shoes), ("sneakers", "flat", "de bang", "the thao")):
            context_score = min(20.0, context_score + 3)
    style_score = 20.0 if context.style.id in styles else 0.0
    colors = {
        color_key(item.color)
        for item in items
        if item.color and "color" not in item.details.get("uncertain_fields", [])
    }
    neutral = {"black", "white", "gray", "beige", "navy", "brown"}
    color_score = 0.0 if not colors else (15.0 if len(colors - neutral) <= 1 else 6.0)
    weather_score = 0.0
    if context.weather:
        cues = {
            "cold": ("winter", "dong", "wool", "len", "fleece", "ni"),
            "snowy": ("winter", "dong", "wool", "len", "waterproof", "chong nuoc"),
            "sunny": ("summer", "he", "linen", "lanh", "breathable", "thoang"),
            "humid": ("linen", "lanh", "breathable", "thoang"),
            "rainy": ("waterproof", "chong nuoc"),
            "mild": ("spring", "autumn", "xuan", "thu"),
        }.get(context.weather.id, ())
        weather_score = min(15.0, 5.0 * sum(has_cue(evidence(item), cues) for item in items))
        if context.weather.id in ("cold", "snowy") and any(i.role == "outerwear" for i in items):
            weather_score = min(15.0, weather_score + 5)
    repetition = -min(10.0, sum(max(0, item.wear_count) for item in items) / len(items))
    snapshot = features(items)
    personalization = max(
        -PERSONALIZATION_CAP,
        min(PERSONALIZATION_CAP, sum(weights.get(feature, 0.0) for feature in snapshot)),
    )
    breakdown = ScoreBreakdown(
        context=context_score,
        style=style_score,
        color=color_score,
        weather=weather_score,
        repetition=round(repetition, 3),
        personalization=round(personalization, 3),
    )
    explanation = ["Bộ đồ đủ thành phần và chỉ dùng món đang có trong tủ của bạn."]
    if context_score:
        explanation.append("Nhãn phong cách của món đồ phù hợp với sự kiện đã chọn.")
    if style_score:
        explanation.append("Có món mang nhãn phong cách bạn đã chọn.")
    if color_score:
        explanation.append("Ưu tiên bảng màu ít màu nhấn hoặc có màu trung tính.")
    if context.weather is None:
        explanation.append("Bạn chưa chọn thời tiết; hệ thống không tự suy đoán thời tiết.")
    elif weather_score:
        explanation.append("Có bằng chứng thuộc tính hoặc lớp áo phù hợp thời tiết đã chọn.")
    else:
        explanation.append("Chưa có đủ bằng chứng để cộng điểm phù hợp thời tiết.")
    if repetition < 0:
        explanation.append("Giảm nhẹ điểm cho các món có số lần mặc cao hơn.")
    if personalization:
        explanation.append("Điều chỉnh có giới hạn theo phản hồi gần nhất trên các bộ trước.")
    explanation.append(SCORE_NOTICE)
    return RankedOutfit(
        items, round(sum(breakdown.model_dump().values()), 3), breakdown, explanation, snapshot
    )


@dataclass
class RecommendationResult:
    outfits: list[RankedOutfit]
    reason: str | None
    missing_categories: list[str]
    combinations_evaluated: int


def recommend(
    wardrobe: list[GarmentSnapshot], context: RecommendationContext, weights: dict[str, float]
) -> RecommendationResult:
    pools: dict[str, list[GarmentSnapshot]] = defaultdict(list)
    # Input trùng ID cũng không được tạo một món ở hai vai trò.
    seen = set()
    for item in sorted(wardrobe, key=lambda i: i.garment_id):
        if item.garment_id not in seen and weather_allowed(item, context):
            pools[item.role].append(item)
            seen.add(item.garment_id)
    for role, items in pools.items():
        pools[role] = sorted(
            items,
            key=lambda item: (-score_outfit([item], context, weights).score, item.garment_id),
        )[:POOL_PER_ROLE]
    candidates = []
    evaluated = 0
    cold = context.weather is not None and context.weather.id in ("cold", "snowy")
    layers = [None, *pools["outerwear"][:2]] if cold else [None]
    for roles in (("top", "bottom", "shoes"), ("dress", "shoes")):
        combinations = product(*(pools[role] for role in roles), layers)
        for combination in islice(combinations, MAX_PER_TEMPLATE):
            evaluated += 1
            items = [item for item in combination if item is not None]
            if complete(items, context):
                candidates.append(score_outfit(items, context, weights))
    selected = []
    while candidates and len(selected) < 3:

        def diversity_key(candidate):
            ids = set(candidate.stable_key)
            overlap = max(
                (
                    len(ids & set(old.stable_key)) / len(ids | set(old.stable_key))
                    for old in selected
                ),
                default=0,
            )
            return (-(candidate.score - 12 * overlap), candidate.stable_key)

        best = min(candidates, key=diversity_key)
        selected.append(best)
        candidates = [
            candidate for candidate in candidates if candidate.stable_key != best.stable_key
        ]
    missing = []
    reason = None
    if not selected:
        options = [
            [role for role in roles if not pools[role]]
            for roles in (("top", "bottom", "shoes"), ("dress", "shoes"))
        ]
        missing = min(options, key=lambda values: (len(values), tuple(values)))
        reason = "Chưa đủ món phù hợp để tạo bộ hoàn chỉnh từ tủ đồ hiện tại."
    elif len(selected) < 3:
        reason = "Tủ đồ hiện tại chỉ tạo được số bộ hoàn chỉnh này; không tạo thêm bộ giả."
    return RecommendationResult(selected, reason, missing, evaluated)
