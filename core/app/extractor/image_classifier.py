"""Small, local text-and-image-signal classifier for OCR media categories."""
from collections import Counter, defaultdict
import math
import re


_LATIN = re.compile(r"[a-z0-9_]+", re.I)
_CJK = re.compile(r"[\u3400-\u9fff]+")


def feature_tokens(text: str | None, signals: dict | None = None) -> list[str]:
    tokens: list[str] = []
    value = (text or "").lower()
    for word in _LATIN.findall(value):
        if len(word) > 1:
            tokens.append("w:" + word)
    for chunk in _CJK.findall(value):
        tokens.extend("c1:" + char for char in chunk)
        tokens.extend("c2:" + chunk[index:index + 2] for index in range(len(chunk) - 1))
        tokens.extend("c3:" + chunk[index:index + 3] for index in range(len(chunk) - 2))

    signals = signals or {}
    face_score = signals.get("face_score")
    tokens.append("face:yes" if face_score is not None and float(face_score) > 0.05 else "face:no")
    chars = int(signals.get("text_chars") or len(re.sub(r"\s+", "", value)))
    lines = int(signals.get("text_lines") or len([line for line in value.splitlines() if line.strip()]))
    coverage = float(signals.get("text_coverage") or 0)
    uniformity = float(signals.get("uniformity") or 0)
    tokens.append("text:short" if chars < 16 else "text:medium" if chars < 60 else "text:long")
    tokens.append("lines:few" if lines < 3 else "lines:some" if lines < 6 else "lines:many")
    tokens.append("coverage:low" if coverage < 0.04 else "coverage:high")
    tokens.append("uniformity:low" if uniformity < 0.3 else "uniformity:high")
    return tokens


def _fit(samples: list[dict], classes: list[str], temperature: float = 1.0) -> dict:
    class_docs = Counter()
    token_counts: dict[str, Counter] = defaultdict(Counter)
    totals = Counter()
    vocabulary = set()
    for sample in samples:
        label = sample["label"]
        if label not in classes:
            continue
        class_docs[label] += 1
        tokens = feature_tokens(sample.get("text"), sample.get("signals"))
        token_counts[label].update(tokens)
        totals[label] += len(tokens)
        vocabulary.update(tokens)
    return {
        "classes": classes,
        "docs": {label: class_docs[label] for label in classes},
        "totals": {label: totals[label] for label in classes},
        "token_counts": {label: dict(token_counts[label]) for label in classes},
        "vocabulary": sorted(vocabulary),
        "temperature": temperature,
    }


def _logits(model: dict, text: str | None, signals: dict | None = None) -> dict[str, float]:
    classes = model["classes"]
    docs = model["docs"]
    all_docs = sum(docs.values())
    vocabulary_size = max(1, len(model["vocabulary"]))
    tokens = feature_tokens(text, signals)
    result = {}
    for label in classes:
        prior = math.log((docs[label] + 1) / (all_docs + len(classes)))
        denom = model["totals"][label] + vocabulary_size
        counts = model["token_counts"][label]
        result[label] = prior + sum(math.log((counts.get(token, 0) + 1) / denom) for token in tokens)
    return result


def _probabilities(logits: dict[str, float], temperature: float) -> dict[str, float]:
    scaled = {key: value / max(0.05, temperature) for key, value in logits.items()}
    peak = max(scaled.values())
    values = {key: math.exp(value - peak) for key, value in scaled.items()}
    total = sum(values.values()) or 1.0
    return {key: value / total for key, value in values.items()}


def predict(model: dict, text: str | None, signals: dict | None = None) -> tuple[str, float, dict[str, float]]:
    probabilities = _probabilities(_logits(model, text, signals), model.get("temperature", 1.0))
    label = max(probabilities, key=probabilities.get)
    return label, probabilities[label], probabilities


def split_samples(samples: list[dict], classes: list[str]) -> tuple[list[dict], list[dict], dict[str, str]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for sample in sorted(samples, key=lambda item: int(item["id"])):
        if sample["label"] in classes:
            grouped[sample["label"]].append(sample)
    training, validation, strategies = [], [], {}
    for label in classes:
        rows = grouped[label]
        source_groups = {str(row.get("source_group")) for row in rows if row.get("source_group") is not None}
        message_groups = {str(row.get("message_group")) for row in rows if row.get("message_group") is not None}
        if len(source_groups) > 1:
            key_name = "source_group"
            strategy = "采集来源留出"
        elif len(message_groups) > 1:
            key_name = "message_group"
            strategy = "消息留出"
        else:
            key_name = "id"
            strategy = "图片留出（来源/消息不足以分组）"
        buckets: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            buckets[str(row.get(key_name, row["id"]))].append(row)
        keys = sorted(buckets)
        holdout_count = max(1, round(len(keys) * 0.2))
        holdout_count = min(holdout_count, len(keys) - 1)
        held_out = set(keys[-holdout_count:])
        validation.extend(row for key, bucket in buckets.items() if key in held_out for row in bucket)
        training.extend(row for key, bucket in buckets.items() if key not in held_out for row in bucket)
        strategies[label] = strategy
    return training, validation, strategies


def train_and_validate(samples: list[dict], classes: list[str]) -> tuple[dict, dict]:
    """Fit a multinomial Naive Bayes text classifier and holdout metrics."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for sample in sorted(samples, key=lambda item: int(item["id"])):
        if sample["label"] in classes:
            grouped[sample["label"]].append(sample)
    counts = {label: len(grouped[label]) for label in classes}
    if len(classes) < 2 or any(counts[label] < 2 for label in classes):
        raise ValueError("每个启用类别至少需要 2 张人工标注图片，且至少有 2 个类别")

    training, validation, strategies = split_samples(samples, classes)

    model = _fit(training, classes)
    validation_logits = [(_logits(model, row.get("text"), row.get("signals")), row["label"]) for row in validation]
    temperatures = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
    def loss(temperature: float) -> float:
        total = 0.0
        for logits, label in validation_logits:
            probability = _probabilities(logits, temperature).get(label, 1e-12)
            total -= math.log(max(probability, 1e-12))
        return total / max(1, len(validation_logits))

    model["temperature"] = min(temperatures, key=loss)
    matrix = {actual: {predicted: 0 for predicted in classes} for actual in classes}
    correct = 0
    for logits, actual in validation_logits:
        probs = _probabilities(logits, model["temperature"])
        predicted = max(probs, key=probs.get)
        matrix[actual][predicted] += 1
        correct += predicted == actual
    per_class = {}
    f1_values = []
    for label in classes:
        tp = matrix[label][label]
        fp = sum(matrix[actual][label] for actual in classes if actual != label)
        fn = sum(matrix[label][predicted] for predicted in classes if predicted != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "support": sum(matrix[label].values()),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
        f1_values.append(f1)
    metrics = {
        "training_count": len(training),
        "validation_count": len(validation),
        "class_counts": counts,
        "accuracy": round(correct / max(1, len(validation)), 4),
        "macro_f1": round(sum(f1_values) / max(1, len(f1_values)), 4),
        "per_class": per_class,
        "confusion_matrix": matrix,
        "temperature": model["temperature"],
        "validation_strategy": strategies,
    }
    return model, metrics
