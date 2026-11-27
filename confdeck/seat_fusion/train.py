"""Train a shadow seat model from labeled calibration sessions only."""
import argparse
import json
from pathlib import Path

from confdeck.evaluation.session import load
from confdeck.seat_fusion.model import train
from confdeck.tracking import TrackingSettings


def main():
    parser = argparse.ArgumentParser(description="Train an experimental per-room shadow fusion model")
    parser.add_argument("--session", action="append", nargs="+", required=True,
                        help="Chunks for one calibration session; repeat for separate sessions")
    parser.add_argument("--settings", required=True, help="Tracking settings JSON")
    parser.add_argument("--links", help="Optional JSON seat links used during calibration")
    parser.add_argument("--output", required=True, help="New model JSON file")
    args = parser.parse_args()
    raw = json.loads(Path(args.settings).read_text(encoding="utf-8"))
    settings = TrackingSettings.model_validate(raw.get("tracking", raw))
    links = json.loads(Path(args.links).read_text(encoding="utf-8")) if args.links else None
    model = train([load(paths) for paths in args.session], settings, links=links)
    with Path(args.output).open("x", encoding="utf-8") as target:
        json.dump(model, target, indent=2, allow_nan=False)
    print("Trained experimental model; evaluate it on different labeled test sessions before use.")


if __name__ == "__main__":
    main()
