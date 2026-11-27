import argparse
import json
from pathlib import Path

from confdeck.tracking import TrackingSettings

from .metrics import export, metrics
from .replay import replay
from .session import load
from .synthetic import generate


def main():
    parser = argparse.ArgumentParser(description="Offline Accuracy Lab (cameras always mocked)")
    parser.add_argument("--session", nargs="+")
    parser.add_argument("--settings")
    parser.add_argument("--engine", choices=["legacy", "new", "compare"], default="legacy")
    parser.add_argument("--vision", choices=["off", "advisory", "active"], default="off")
    parser.add_argument("--output", default="docs/reports/legacy-baseline")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    session, settings = generate(args.seed)
    if args.session:
        if not args.settings:
            parser.error("Recorded sessions require --settings")
        session = load(args.session)
        settings = TrackingSettings.model_validate_json(Path(args.settings).read_text())
    reports = {}
    for engine in (["legacy", "new"] if args.engine == "compare" else [args.engine]):
        result = replay(session, settings, engine, args.vision)
        report = metrics(session, result, settings)
        report["provenance"] = "Synthetic fixture; not a measurement of room accuracy" if not args.session else session["id"]
        report["settings"] = settings.model_dump()
        report["session_id"] = session["id"]
        reports[engine] = report
        export(report, result, args.output + ("-"+engine if args.engine == "compare" else ""))
    if args.engine == "compare":
        keys = ("correct_rate", "wrong_rate", "no_decision_rate", "opposite_rate", "spurious_switches_per_min", "switch_latency_median")
        deltas = {k: reports["new"][k]-reports["legacy"][k] if all(reports[e][k] is not None for e in reports) else None for k in keys}
        Path(args.output+"-comparison.json").write_text(json.dumps({"reports": reports, "new_minus_legacy": deltas}, indent=2), encoding="utf-8")
    print(json.dumps({e: {k: r[k] for k in ("correct_rate", "wrong_rate", "spurious_switches_per_min")} for e, r in reports.items()}))


if __name__ == "__main__":
    main()
