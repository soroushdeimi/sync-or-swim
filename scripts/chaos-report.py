#!/usr/bin/env python3
"""Generates docs/results.md from build/chaos-results.jsonl."""

import json
import re
from pathlib import Path
import subprocess
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_JSONL = REPO_ROOT / "build" / "chaos-results.jsonl"
OUTPUT_MD = REPO_ROOT / "docs" / "results.md"


def get_git_commit():
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
        ).strip()
        return out
    except Exception:
        return "unknown"


def main():
    if not RESULTS_JSONL.exists():
        print(f"Results file {RESULTS_JSONL} not found.")
        return

    seen = {}
    with open(RESULTS_JSONL) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            scenario = entry.get("scenario")
            if scenario:
                seen[scenario] = entry

    # Sort scenarios predictably by scenario key
    def order(entry):
        # "2. name" before "10a. name": number first, then the letter suffix
        m = re.match(r"(\d+)([a-z]*)\.", str(entry.get("scenario", "")))
        return (int(m.group(1)), m.group(2)) if m else (10**6, str(entry.get("scenario", "")))

    results = sorted(seen.values(), key=order)
    commit = get_git_commit()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "# Chaos Test Results",
        "",
        f"- **Date:** {date_str}",
        f"- **Commit:** `{commit}`",
        "",
        "| Scenario | Switch (s) | Lost Pings | Failback (s) | Seq OK | Notes |",
        "|:---|:---:|:---:|:---:|:---:|:---|",
    ]

    for r in results:
        scenario = r.get("scenario", "")
        sw = f"{r['switch_s']:.3f}" if isinstance(r.get("switch_s"), (int, float)) else "-"
        lost = str(r.get("lost_pings", "-")) if r.get("lost_pings") is not None else "-"
        fb = f"{r['failback_s']:.3f}" if isinstance(r.get("failback_s"), (int, float)) else "-"
        seq = "PASS" if r.get("seq_ok") is True else ("FAIL" if r.get("seq_ok") is False else "-")
        notes = r.get("notes", "")
        lines.append(f"| {scenario} | {sw} | {lost} | {fb} | {seq} | {notes} |")

    lines.append("")
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text("\n".join(lines))
    print(f"Wrote report to {OUTPUT_MD}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
