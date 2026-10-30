from __future__ import annotations

import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


def sanitize_image(body: bytes, max_bytes: int, max_pixels: int) -> tuple[bytes, str]:
    """Decode ảnh thật, áp dụng orientation rồi bỏ EXIF/GPS và metadata phụ."""
    if not body or len(body) > max_bytes:
        raise ValueError("Ảnh rỗng hoặc vượt giới hạn dung lượng")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(body)) as image:
                fmt = image.format
                if fmt not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Chỉ hỗ trợ JPEG, PNG và WebP")
                if image.width * image.height > max_pixels or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Ảnh quá lớn hoặc có nhiều frame")
                image.load()
                pixels = ImageOps.exif_transpose(image)
                mode = "RGBA" if "A" in pixels.getbands() and fmt != "JPEG" else "RGB"
                clean = Image.new(mode, pixels.size)
                clean.paste(pixels.convert(mode))
                output = io.BytesIO()
                # JPEG -> PNG để không tái nén mất pixel sau decode/orientation.
                if fmt == "JPEG":
                    fmt = "PNG"
                options = {"lossless": True} if fmt == "WEBP" else {}
                clean.save(output, format=fmt, **options)
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("Ảnh không hợp lệ hoặc quá lớn") from exc
    normalized = output.getvalue()
    if len(normalized) > max_bytes:
        raise ValueError("Ảnh sau chuẩn hóa vượt giới hạn dung lượng")
    return normalized, Image.MIME[fmt]
