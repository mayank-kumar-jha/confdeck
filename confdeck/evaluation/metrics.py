"""Time-weighted, labelled metrics and self-contained offline reports."""
import csv
import html
import json
import math
from pathlib import Path

import numpy as np

from .protocol import QUIET


def angular(a, b, convention=None):
    if convention and convention.elevation_reference == "vertical":
        a, b = (a[0], 90-a[1]), (b[0], 90-b[1])
    az, el = np.radians([a, b]).T
    cosine = np.sin(el[0]) * np.sin(el[1]) + np.cos(el[0]) * np.cos(el[1]) * np.cos(az[0] - az[1])
    return math.degrees(math.acos(float(np.clip(cosine, -1, 1))))


def weighted_quantile(values, weights, q):
    if not values:
        return None
    order = np.argsort(values)
    cumulative = np.cumsum(np.asarray(weights)[order])
    return float(np.asarray(values)[order][min(len(order)-1, np.searchsorted(cumulative, q*cumulative[-1]))])


def metrics(session, result, settings):
    ids = [z.id for z in settings.zones] + [None]
    centers = {z.id: (z.azimuth, z.elevation) for z in settings.zones}
    matrix = np.zeros((len(ids), len(ids)))
    speech = correct = wrong = missing = quiet = echo = echo_active = opposite = 0.0
    angles, weights, latencies = [], [], []
    false_switches = echo_trials = echo_triggered = 0
    points = result["points"]
    labels = session.get("labels") or [r for r in session["rows"] if r.get("kind") == "label"]
    for label in labels:
        start, end, truth, condition = label["start"], label["end"], label["seat"], label["condition"]
        if not start < end or truth not in ids:
            raise ValueError("Invalid ground truth interval")
        if condition == "overlap":
            continue  # Ambiguous truth is explicitly excluded from single-speaker accuracy.
        first_correct = None
        for i, point in enumerate(points[:-1]):
            dt = min(end, points[i+1]["t"]) - max(start, point["t"])
            if dt <= 0:
                continue
            pred = point["seat"] if point["seat"] in ids else None
            matrix[ids.index(truth), ids.index(pred)] += dt
            if truth is not None:
                speech += dt
                correct += dt * (pred == truth)
                missing += dt * (pred is None)
                wrong += dt * (pred is not None and pred != truth)
                if pred == truth and first_correct is None:
                    first_correct = max(start, point["t"]) - start
                if pred is not None:
                    angle = angular(centers[truth], centers[pred], settings.inference.convention)
                    angles.append(angle)
                    weights.append(dt)
                    opposite += dt * (angle > 120)
            elif condition in QUIET:
                quiet += dt
                if condition == "echo":
                    echo += dt
                    echo_active += dt * (pred is not None)
        if condition == "echo":
            echo_trials += 1
            echo_triggered += any(start <= c["time"] < end and c["zone"] != "wide" for c in result["commands"])
        if truth is not None:
            latencies.append(first_correct)
        false_switches += sum(start <= c["time"] < end and c["zone"] != "wide"
                              for c in result["commands"]) if truth is None and condition in QUIET else 0
    def ratio(n, d):
        return n / d if d else None
    successful = [x for x in latencies if x is not None]
    return {"engine": result["engine"], "speech_seconds": speech, "correct_rate": ratio(correct, speech),
            "wrong_rate": ratio(wrong, speech), "no_decision_rate": ratio(missing, speech),
            "confusion_labels": ids, "confusion_seconds": matrix.tolist(),
            "per_seat_accuracy": {z: ratio(matrix[i, i], matrix[i].sum()) for i, z in enumerate(ids[:-1])},
            "angular_median": weighted_quantile(angles, weights, .5), "angular_p90": weighted_quantile(angles, weights, .9),
            "opposite_rate": ratio(opposite, speech), "switch_latency_median": float(np.median(successful)) if successful else None,
            "switch_latencies": latencies, "missed_trials": latencies.count(None),
            "spurious_switches_per_min": ratio(false_switches * 60, quiet),
            "echo_false_trigger_rate": ratio(echo_active, echo), "echo_trigger_trial_rate": ratio(echo_triggered, echo_trials), "overlap_policy": "excluded; preserve previous seat",
            "labelled_seconds": float(matrix.sum())}


def export(report, result, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    with output.with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["metric", "value"])
        writer.writerows((k, json.dumps(v)) for k, v in report.items())
    matrix = report["confusion_seconds"]
    peak = max([max(row) for row in matrix] + [1])
    labels = [str(x or "none") for x in report["confusion_labels"]]
    table = '<tr><th>Truth / prediction</th>' + ''.join(f'<th>{html.escape(x)}</th>' for x in labels) + '</tr>'
    for name, row in zip(labels, matrix):
        table += '<tr><th>' + html.escape(name) + '</th>' + ''.join(f'<td style="background:rgba(255,140,40,{v/peak:.2f})">{v:.2f}</td>' for v in row) + '</tr>'
    duration = max(result["end"], 1)
    lines = []
    for p, q in zip(result["points"], result["points"][1:]):
        y = 20 + labels.index(str(p['seat'] or 'none')) * 25
        lines.append(f'<path d="M {p["t"]/duration*900:.2f} {y} H {q["t"]/duration*900:.2f}"/>')
    body = '<!doctype html><meta charset="utf-8"><title>Accuracy report</title><style>body{font:16px system-ui;margin:2rem}td,th{padding:.5rem}svg{width:100%;border:1px solid #888}</style>'
    body += '<h1>Offline accuracy report</h1><p>' + html.escape(report.get("provenance", "Labelled test session")) + '</p><table>' + table + '</table>'
    body += f'<p>Timeline, 0–{duration:.1f}s; rows: {html.escape(", ".join(labels))}</p><svg viewBox="0 0 900 {len(labels)*25+20}" stroke="#d75b00" stroke-width="3">' + ''.join(lines) + '</svg>'
    body += '<pre>' + html.escape(json.dumps(report, indent=2)) + '</pre>'
    output.with_suffix(".html").write_text(body, encoding="utf-8")
