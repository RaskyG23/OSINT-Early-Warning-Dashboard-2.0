"""Small held-out evaluation of the dashboard's corpus-trained PPMI embedding.

The labels below were assigned from the factual meaning of stored headlines,
without looking at embedding scores.  Thresholds are selected on CALIBRATION
only and then frozen for TEST.  This evaluates the semantic component, not the
complete operational-risk model.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from app.embeddings import train_ppmi_embeddings


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "osint-dashboard.sqlite"
LABELS = ("disruption", "severity", "transport")

# id: (split, disruption, severity, transport)
# Disruption = an actual operational interruption/consequence, not merely a
# warning or policy discussion. Severity = physical hazard, attack, war or
# similarly acute threat. Transport = an airport, port, vessel, checkpoint,
# freight route or comparable transport asset is substantively involved.
CASES = {
    2813: ("calibration", 1, 1, 1),  # civil aircraft lost during conflict
    2784: ("calibration", 0, 0, 0),  # drought-management agreement
    2712: ("calibration", 0, 1, 0),  # strike drone crash
    2687: ("calibration", 1, 0, 1),  # airport halted by strike
    2682: ("calibration", 1, 0, 0),  # widespread power outage
    2403: ("calibration", 0, 0, 0),  # diplomatic discussion
    2713: ("calibration", 0, 1, 1),  # border checkpoint still operating
    2456: ("calibration", 1, 1, 1),  # severe weather and blocked strait
    2572: ("calibration", 0, 0, 0),  # project financial closure
    2689: ("calibration", 0, 0, 1),  # flights resume after strike
    2374: ("calibration", 1, 0, 0),  # burst-pipe flooding
    2358: ("calibration", 1, 1, 0),  # fighting/flood displacement
    2597: ("calibration", 1, 1, 1),  # border checkpoint attacked
    2524: ("calibration", 0, 1, 1),  # aviation alert during attack
    2582: ("calibration", 0, 0, 0),  # figurative 'war' on pricing
    2548: ("calibration", 0, 0, 0),  # insurance analysis

    2794: ("test", 1, 1, 0),         # sanctions intended to collapse economy
    2479: ("test", 0, 1, 0),         # missile attack, no stated operation
    2547: ("test", 0, 1, 0),         # coordinated drought response
    2747: ("test", 0, 1, 0),         # threatened war escalation
    2683: ("test", 1, 0, 0),         # nationwide transmission outage
    2795: ("test", 0, 1, 0),         # live war/sanctions report
    2688: ("test", 1, 0, 1),         # airport expected to close
    2491: ("test", 0, 1, 0),         # wildfire support request
    2421: ("test", 0, 0, 0),         # opinion/letters headline
    2592: ("test", 1, 1, 1),         # piracy affecting enforcement
    2480: ("test", 0, 1, 0),         # attacks without stated operation
    2598: ("test", 1, 1, 0),         # attack leaves 13,000 without power
    2686: ("test", 1, 0, 0),         # widespread power outage
    2525: ("test", 0, 1, 1),         # border aviation alert
    2368: ("test", 0, 1, 0),         # condemnation of drone attack
    2573: ("test", 1, 0, 0),         # prolonged power outage
}


def metrics(expected, predicted):
    tp = sum(a == 1 and b == 1 for a, b in zip(expected, predicted))
    fp = sum(a == 0 and b == 1 for a, b in zip(expected, predicted))
    tn = sum(a == 0 and b == 0 for a, b in zip(expected, predicted))
    fn = sum(a == 1 and b == 0 for a, b in zip(expected, predicted))
    accuracy = (tp + tn) / max(1, tp + fp + tn + fn)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "accuracy": accuracy,
            "precision": precision, "recall": recall, "f1": f1}


def best_threshold(rows, label):
    candidates = [value / 100 for value in range(0, 61)]
    best = None
    for threshold in candidates:
        expected = [row[label] for row in rows]
        predicted = [int(row["scores"][label] >= threshold) for row in rows]
        result = metrics(expected, predicted) | {"threshold": threshold}
        key = (result["f1"], result["accuracy"], result["precision"], threshold)
        if best is None or key > best[0]:
            best = (key, result)
    return best[1]


def main():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in CASES)
    selected = {
        row["id"]: dict(row)
        for row in connection.execute(
            f"SELECT id, country, headline, summary FROM country_articles WHERE id IN ({placeholders})",
            tuple(CASES),
        )
    }
    missing = sorted(set(CASES) - set(selected))
    if missing:
        raise SystemExit(f"Labelled database records are missing: {missing}")

    training_documents = [
        f'{row["headline"]} {row["summary"] or ""}'
        for row in connection.execute(
            f"SELECT id, headline, summary FROM country_articles WHERE id NOT IN ({placeholders})",
            tuple(CASES),
        )
    ]
    model = train_ppmi_embeddings(training_documents)
    rows = []
    for article_id, (split, disruption, severity, transport) in CASES.items():
        article = selected[article_id]
        text = f'{article["headline"]} {article["summary"] or ""}'
        rows.append({
            "id": article_id, "split": split, "country": article["country"],
            "headline": article["headline"], "disruption": disruption,
            "severity": severity, "transport": transport, "scores": model.scores(text),
        })

    calibration = [row for row in rows if row["split"] == "calibration"]
    test = [row for row in rows if row["split"] == "test"]
    thresholds = {label: best_threshold(calibration, label)["threshold"] for label in LABELS}
    results = {}
    for label in LABELS:
        expected = [row[label] for row in test]
        predicted = [int(row["scores"][label] >= thresholds[label]) for row in test]
        results[label] = metrics(expected, predicted) | {"threshold": thresholds[label]}

    expected_micro = [row[label] for row in test for label in LABELS]
    predicted_micro = [
        int(row["scores"][label] >= thresholds[label]) for row in test for label in LABELS
    ]
    results["micro"] = metrics(expected_micro, predicted_micro)
    results["macro_f1"] = sum(results[label]["f1"] for label in LABELS) / len(LABELS)
    results["exact_match_accuracy"] = sum(
        all(int(row["scores"][label] >= thresholds[label]) == row[label] for label in LABELS)
        for row in test
    ) / len(test)
    results["mean_coverage"] = sum(row["scores"]["coverage"] for row in test) / len(test)
    results["model"] = {
        "training_documents": model.document_count,
        "vocabulary_size": model.vocabulary_size,
        "calibration_examples": len(calibration), "test_examples": len(test),
    }

    output_dir = ROOT / "evaluation"
    output_dir.mkdir(exist_ok=True)
    with (output_dir / "ppmi_embedding_predictions.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["id", "split", "country", "headline", "coverage"]
        for label in LABELS:
            fields += [f"{label}_label", f"{label}_score", f"{label}_prediction"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            record = {"id": row["id"], "split": row["split"], "country": row["country"],
                      "headline": row["headline"], "coverage": row["scores"]["coverage"]}
            for label in LABELS:
                record[f"{label}_label"] = row[label]
                record[f"{label}_score"] = row["scores"][label]
                record[f"{label}_prediction"] = int(row["scores"][label] >= thresholds[label])
            writer.writerow(record)
    (output_dir / "ppmi_embedding_metrics.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
