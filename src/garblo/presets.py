from garblo.schemas import LookbookCreateRequest

PRESETS = {
    "everyday": "Create a practical, balanced everyday outfit.",
    "office": "Create a polished office outfit with restrained styling.",
    "weekend": "Create a relaxed outfit for a casual weekend.",
    "evening": "Create an elegant evening outfit.",
}
ALIASES = {
    "casual": "everyday",
    "daily": "everyday",
    "work": "office",
    "đi làm": "office",
    "hằng ngày": "everyday",
    "cuối tuần": "weekend",
    "dự tiệc": "evening",
}


def select_preset(payload: LookbookCreateRequest) -> str:
    """Giữ field cũ nhưng chỉ chấp nhận giá trị có ánh xạ preset an toàn."""
    if payload.instruction or payload.pose_asset_id or payload.background_asset_id:
        raise ValueError("P0 chỉ hỗ trợ preset; chưa hỗ trợ prompt tự do, pose hoặc background")
    selected = []
    for value in (payload.preset_id, payload.theme, payload.occasion):
        if value:
            key = value.strip().lower()
            key = ALIASES.get(key, key)
            if key not in PRESETS:
                raise ValueError("Preset không được hỗ trợ: everyday, office, weekend, evening")
            selected.append(key)
    if len(set(selected)) > 1:
        raise ValueError("theme/occasion/preset_id phải cùng một preset")
    return selected[0] if selected else "everyday"
