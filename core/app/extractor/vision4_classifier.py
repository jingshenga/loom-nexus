"""MobileNetV4 four-class ONNX inference for OCR classification."""
import hashlib
import json
import logging
import math
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger("nexus-core.vision4")

_MODES = {"off", "shadow", "active"}
_IMAGE_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
_CLASSIFICATION_MAP = {
    "profile": "info_card",
    "person": "profile_photo",
    "report": "sixiang",
    "other": "other",
}


def map_category(category: str | None) -> str:
    return _CLASSIFICATION_MAP.get(category, "uncertain")


class Vision4Classifier:
    def __init__(self, mode: str = "off", model_dir: str = "/app/data/models/vision-model-v1"):
        if mode not in _MODES:
            raise ValueError("NEXUS_VISION4_MODE must be off, shadow, or active")
        self.mode = mode
        self.model_dir = Path(model_dir)
        self._lock = threading.Lock()
        self._session = None
        self._labels = None
        self._preprocessing = None
        self._manifest = None
        self._load_error = None
        self._load_ms = None
        self._reported_errors = set()
        if mode == "active":
            logger.info("vision4 active mode enabled; new V4 classifications require review")

    @property
    def enabled(self) -> bool:
        return self.mode in {"shadow", "active"}

    def predict(self, image_path: str) -> dict:
        return self.predict_batch([image_path])[0]

    def predict_batch(self, image_paths: list[str]) -> list[dict]:
        if not self.enabled:
            return [{"status": "off", "mode": self.mode} for _ in image_paths]
        if not image_paths:
            return []

        started = time.perf_counter()
        prediction_started = None
        try:
            self._ensure_loaded()
            prediction_started = time.perf_counter()
            import numpy as np

            tensors = []
            positions = []
            results = [None] * len(image_paths)
            for index, image_path in enumerate(image_paths):
                try:
                    tensors.append(self._preprocess(Path(image_path)))
                    positions.append(index)
                except Exception as exc:
                    results[index] = self._error_result(self._error_code(exc), started)
            if not tensors:
                return results
            batch = np.concatenate(tensors, axis=0)
            output = self._session.run(
                [self._session.get_outputs()[0].name],
                {self._session.get_inputs()[0].name: batch},
            )[0]
            logits = np.asarray(output, dtype=np.float64)
            if logits.shape != (len(positions), len(self._labels)) or not np.isfinite(logits).all():
                raise ValueError("invalid_model_output")
            inference_ms = round((time.perf_counter() - prediction_started) * 1000, 3)
            for output_index, input_index in enumerate(positions):
                values = logits[output_index] - np.max(logits[output_index])
                scores = np.exp(values)
                scores /= scores.sum()
                label_index = int(np.argmax(scores))
                results[input_index] = {
                    "status": "classified",
                    "mode": self.mode,
                    "category": self._labels[label_index]["category"],
                    "category_zh": self._labels[label_index]["category_zh"],
                    "mapped_classification": map_category(self._labels[label_index]["category"]),
                    "scores": {
                        item["category"]: float(scores[i])
                        for i, item in enumerate(self._labels)
                    },
                    "score_semantics": "uncalibrated_softmax_score_not_probability",
                    "model_version": self._manifest["model_version"],
                    "model_sha": self._manifest["model_sha256"],
                    "model_load_ms": self._load_ms,
                    "inference_ms": inference_ms,
                    "review_recommended": True,
                }
            return results
        except Exception as exc:
            code = self._load_error or self._error_code(exc)
            if self._load_error is None and code in {
                "model_unavailable", "model_integrity_error", "model_config_error", "model_load_error",
            }:
                self._load_error = code
            if code not in self._reported_errors:
                self._reported_errors.add(code)
                logger.warning("vision4 inference unavailable (code=%s)", code)
            return [self._error_result(code, started) for _ in image_paths]

    def _error_result(self, code: str, started: float) -> dict:
        if code not in self._reported_errors:
            self._reported_errors.add(code)
            logger.warning("vision4 inference unavailable (code=%s)", code)
        return {
            "status": "unavailable" if code.startswith("model_") else "error",
            "mode": self.mode,
            "category": None,
            "scores": {},
            "model_version": (self._manifest or {}).get("model_version") or self.model_dir.name,
            "model_sha": (self._manifest or {}).get("model_sha256"),
            "model_load_ms": self._load_ms,
            "inference_ms": round((time.perf_counter() - started) * 1000, 3),
            "error_code": code,
            "review_recommended": True,
        }

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        if self._load_error:
            raise RuntimeError(self._load_error)
        with self._lock:
            if self._session is not None:
                return
            if self._load_error:
                raise RuntimeError(self._load_error)
            started = time.perf_counter()
            try:
                self._load_package()
            except Exception as exc:
                self._load_error = self._error_code(exc)
                raise
            self._load_ms = round((time.perf_counter() - started) * 1000, 3)
            logger.info(
                "vision4 model loaded (version=%s sha=%s load_ms=%.3f)",
                self._manifest["model_version"], self._manifest["model_sha256"], self._load_ms,
            )

    def _load_package(self) -> None:
        try:
            manifest = json.loads((self.model_dir / "manifest.json").read_text(encoding="utf-8"))
            labels_doc = json.loads((self.model_dir / "labels.json").read_text(encoding="utf-8"))
            preprocessing = json.loads((self.model_dir / "preprocess.json").read_text(encoding="utf-8"))
        except OSError as exc:
            raise RuntimeError("model_unavailable") from exc
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("model_config_error") from exc

        if not isinstance(manifest, dict) or not isinstance(labels_doc, dict) or not isinstance(preprocessing, dict):
            raise RuntimeError("model_config_error")
        labels = labels_doc.get("labels")
        expected_labels = manifest.get("labels")
        if (
            not isinstance(labels, list)
            or len(labels) != 4
            or not all(isinstance(item, dict) for item in labels)
            or not isinstance(expected_labels, list)
            or [item.get("category") for item in labels] != expected_labels
            or [item.get("index") for item in labels] != list(range(len(labels)))
            or len({item.get("category") for item in labels}) != len(labels)
            or preprocessing != manifest.get("preprocessing")
        ):
            raise RuntimeError("model_config_error")
        if not all(
            isinstance(preprocessing.get(name), list)
            for name in ("input_size", "mean", "std", "value_range_before_normalization")
        ):
            raise RuntimeError("model_config_error")
        if (
            preprocessing.get("color_space") != "RGB"
            or preprocessing.get("resize") != "direct_resize_to_input_size"
            or preprocessing.get("resampling") != "bilinear"
            or preprocessing.get("value_range_before_normalization") != [0.0, 1.0]
            or preprocessing.get("exif_orientation") != "ignored_to_match_training_loader"
            or preprocessing.get("antialias") is not True
            or len(preprocessing.get("input_size", [])) != 2
            or len(preprocessing.get("mean", [])) != 3
            or len(preprocessing.get("std", [])) != 3
            or any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
                   for value in preprocessing.get("input_size", []))
            or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                   or not math.isfinite(value)
                   for value in preprocessing.get("mean", []) + preprocessing.get("std", []))
            or any(float(value) == 0 for value in preprocessing.get("std", []))
        ):
            raise RuntimeError("model_config_error")

        try:
            self._verify_package(manifest)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("model_integrity_error") from exc

        model_name = manifest.get("model_file")
        model_path = self.model_dir / str(model_name or "")
        expected_sha = str(manifest.get("model_sha256") or "").lower()
        if (
            not model_name
            or Path(model_name).name != model_name
            or model_path.is_symlink()
            or not model_path.is_file()
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
        ):
            raise RuntimeError("model_unavailable")
        digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
        if digest != expected_sha or model_path.stat().st_size != manifest.get("model_bytes"):
            raise RuntimeError("model_integrity_error")

        try:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            session = ort.InferenceSession(
                str(model_path), sess_options=options, providers=["CPUExecutionProvider"],
            )
            input_meta = session.get_inputs()[0]
            width, height = (int(value) for value in preprocessing["input_size"])
            output_meta = session.get_outputs()[0]
            if (
                input_meta.type != "tensor(float)"
                or input_meta.shape[1:] != [3, height, width]
                or len(output_meta.shape) != 2
                or output_meta.shape[-1] != len(labels)
            ):
                raise RuntimeError("model_config_error")
            if "CPUExecutionProvider" not in session.get_providers():
                raise RuntimeError("model_load_error")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("model_load_error") from exc

        self._labels = labels
        self._preprocessing = preprocessing
        self._manifest = manifest
        self._session = session

    def _verify_package(self, manifest: dict) -> None:
        sums_file = self.model_dir / "SHA256SUMS"
        if sums_file.is_symlink() or not sums_file.is_file():
            raise RuntimeError("model_integrity_error")
        entries = {}
        for line in sums_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            expected, name = line.split("  ", 1)
            path = self.model_dir / name
            if (
                Path(name).name != name
                or path.is_symlink()
                or not path.is_file()
                or not re.fullmatch(r"[0-9a-fA-F]{64}", expected)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected.lower()
            ):
                raise RuntimeError("model_integrity_error")
            entries[name] = expected.lower()

        required = {"model.onnx", "manifest.json", "labels.json", "preprocess.json"}
        if not required.issubset(entries):
            raise RuntimeError("model_integrity_error")
        export_name = manifest.get("onnx_export", {}).get("export_manifest_file")
        export_sha = manifest.get("onnx_export", {}).get("export_manifest_sha256")
        if (
            not export_name
            or Path(export_name).name != export_name
            or entries.get(export_name) != export_sha
        ):
            raise RuntimeError("model_integrity_error")
        export_manifest = json.loads((self.model_dir / export_name).read_text(encoding="utf-8"))
        if export_manifest.get("model_sha256") != manifest.get("model_sha256"):
            raise RuntimeError("model_integrity_error")

    def _preprocess(self, image_path: Path):
        if image_path.suffix.lower() not in _IMAGE_SUFFIXES:
            raise ValueError("unsupported_image")
        import numpy as np
        from PIL import Image

        width, height = (int(value) for value in self._preprocessing["input_size"])
        with Image.open(image_path) as source:
            image = source.convert("RGB").resize(
                (width, height), resample=Image.Resampling.BILINEAR,
            )
        array = np.asarray(image, dtype=np.float32) / np.float32(255.0)
        mean = np.asarray(self._preprocessing["mean"], dtype=np.float32)
        std = np.asarray(self._preprocessing["std"], dtype=np.float32)
        return np.transpose((array - mean) / std, (2, 0, 1))[None, ...]

    @staticmethod
    def _error_code(exc: Exception) -> str:
        message = str(exc)
        if message in {
            "model_unavailable", "model_integrity_error", "model_config_error", "model_load_error",
            "invalid_model_output", "unsupported_image",
        }:
            return message
        return "image_or_inference_error"
