from __future__ import annotations

import io
from typing import Any

from garblo.application.wardrobe import ApplicationError, validate_vector
from garblo.config import Settings
from garblo.domain.entities import DetectedGarment


class LocalModelUnavailable(ApplicationError):
    """Raised when the optional local ML image is not installed or cannot load a model."""


GARMENT_TAXONOMY = [
    "top",
    "bottom",
    "dress",
    "outerwear",
    "shoes",
    "bag",
    "hat",
    "jewelry",
    "scarf",
    "accessory",
]


class GroundingDinoGarmentDetector:
    """Lazy Grounding DINO adapter. Importing it never downloads model weights in the API process."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._pipeline: Any | None = None

    def detect(self, image_bytes: bytes, mime_type: str) -> list[DetectedGarment]:
        image = self._image(image_bytes)
        detector = self._get_pipeline()
        try:
            predictions = detector(image, candidate_labels=GARMENT_TAXONOMY, threshold=0.25)
        except Exception as exc:
            raise LocalModelUnavailable("Grounding DINO detection failed") from exc
        detections: list[DetectedGarment] = []
        width, height = image.size
        for prediction in predictions:
            box = prediction.get("box", {}) if isinstance(prediction, dict) else {}
            if not isinstance(box, dict):
                continue
            left = max(0, min(width, int(box.get("xmin", 0))))
            top = max(0, min(height, int(box.get("ymin", 0))))
            right = max(left, min(width, int(box.get("xmax", 0))))
            bottom = max(top, min(height, int(box.get("ymax", 0))))
            if right - left < 8 or bottom - top < 8:
                continue
            crop = image.crop((left, top, right, bottom))
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG")
            label = str(prediction.get("label", "accessory")).lower()
            detections.append(
                DetectedGarment(
                    category=label if label in GARMENT_TAXONOMY else "accessory",
                    confidence=float(prediction.get("score", 0.0)),
                    bbox={"left": left, "top": top, "right": right, "bottom": bottom},
                    crop_bytes=buffer.getvalue(),
                )
            )
        return detections

    def _get_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        try:
            import torch
            from transformers import pipeline

            device = self._device(torch)
            self._pipeline = pipeline(
                task="zero-shot-object-detection",
                model=self.settings.detector_model,
                device=device,
            )
            return self._pipeline
        except ImportError as exc:
            raise LocalModelUnavailable("Local ML dependencies are missing; install .[ml]") from exc
        except Exception as exc:
            raise LocalModelUnavailable("Grounding DINO could not be loaded") from exc

    def _device(self, torch: Any) -> int:
        if self.settings.ml_device == "cpu":
            return -1
        if self.settings.ml_device == "cuda" or (self.settings.ml_device == "auto" and torch.cuda.is_available()):
            return 0
        return -1

    @staticmethod
    def _image(image_bytes: bytes) -> Any:
        try:
            from PIL import Image

            return Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except ImportError as exc:
            raise LocalModelUnavailable("Pillow is required for local garment detection") from exc
        except Exception as exc:
            raise LocalModelUnavailable("Uploaded image cannot be decoded") from exc


class SigLIPImageEmbedder:
    """Lazy local SigLIP2 image embedder with an explicit fixed vector size."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model: Any | None = None
        self._processor: Any | None = None
        self._torch: Any | None = None
        self._device: str | None = None

    def embed_image(self, image_bytes: bytes, mime_type: str) -> list[float]:
        image = GroundingDinoGarmentDetector._image(image_bytes)
        model, processor, torch, device = self._get_model()
        try:
            inputs = processor(images=image, return_tensors="pt").to(device)
            with torch.no_grad():
                features = model.get_image_features(**inputs)
                features = features / features.norm(p=2, dim=-1, keepdim=True)
            vector = features[0].detach().cpu().tolist()
            return validate_vector([float(value) for value in vector], self.settings.image_embedding_dimensions, "SigLIP")
        except ApplicationError:
            raise
        except Exception as exc:
            raise LocalModelUnavailable("SigLIP image embedding failed") from exc

    def _get_model(self) -> tuple[Any, Any, Any, str]:
        if self._model is not None:
            return self._model, self._processor, self._torch, self._device or "cpu"
        try:
            import torch
            from transformers import AutoProcessor, SiglipModel

            use_cuda = self.settings.ml_device == "cuda" or (
                self.settings.ml_device == "auto" and torch.cuda.is_available()
            )
            device = "cuda" if use_cuda else "cpu"
            self._processor = AutoProcessor.from_pretrained(self.settings.image_embedding_model)
            self._model = SiglipModel.from_pretrained(self.settings.image_embedding_model).to(device).eval()
            self._torch, self._device = torch, device
            return self._model, self._processor, torch, device
        except ImportError as exc:
            raise LocalModelUnavailable("Local ML dependencies are missing; install .[ml]") from exc
        except Exception as exc:
            raise LocalModelUnavailable("SigLIP could not be loaded") from exc
