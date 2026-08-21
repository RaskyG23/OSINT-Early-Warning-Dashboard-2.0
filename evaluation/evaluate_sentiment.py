"""Reproducible three-class evaluation of country-targeted sentiment."""

import csv
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.supply_chain import country_sentiment  # noqa: E402


def evaluate(path=Path(__file__).with_name("sentiment_benchmark.csv")):
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    labels = ("Positive", "Negative", "Neutral")
    counts = {label: {"tp": 0, "fp": 0, "fn": 0, "support": 0} for label in labels}
    errors = []
    for row in rows:
        expected = row["label"]
        predicted = country_sentiment(row["text"], row["country"])["label"]
        counts[expected]["support"] += 1
        for label in labels:
            if predicted == label and expected == label:
                counts[label]["tp"] += 1
            elif predicted == label:
                counts[label]["fp"] += 1
            elif expected == label:
                counts[label]["fn"] += 1
        if predicted != expected:
            errors.append((expected, predicted, row["text"]))
    metrics = {}
    for label, values in counts.items():
        precision = values["tp"] / max(1, values["tp"] + values["fp"])
        recall = values["tp"] / max(1, values["tp"] + values["fn"])
        f1 = 2 * precision * recall / max(1e-12, precision + recall)
        metrics[label] = {"precision": precision, "recall": recall, "f1": f1,
                          "support": values["support"]}
    metrics["macro_f1"] = sum(metrics[label]["f1"] for label in labels) / len(labels)
    metrics["accuracy"] = sum(counts[label]["tp"] for label in labels) / max(1, len(rows))
    return metrics, errors


if __name__ == "__main__":
    results, mistakes = evaluate()
    for label in ("Positive", "Negative", "Neutral"):
        item = results[label]
        print(f'{label:8} precision={item["precision"]:.3f} recall={item["recall"]:.3f} '
              f'f1={item["f1"]:.3f} support={item["support"]}')
    print(f'Macro F1={results["macro_f1"]:.3f}')
    print(f'Accuracy={results["accuracy"]:.3f}')
    if mistakes:
        print("Misclassifications:")
        for expected, predicted, text in mistakes:
            print(f"- expected={expected} predicted={predicted}: {text}")
