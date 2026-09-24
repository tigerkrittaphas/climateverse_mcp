"""Compare two summarized ClimateVerse MCP runs under the same conditions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.metrics import summarize_run


METRICS = (
    "tool_actions",
    "mcp_tool_actions",
    "time_to_final_text_seconds",
    "writing_duration_seconds",
    "output_tokens",
)


def _change(baseline: float, candidate: float) -> float | None:
    if not baseline:
        return None
    return round((candidate - baseline) / baseline * 100, 1)


def compare_runs(baseline_dir: Path, candidate_dir: Path) -> dict:
    baseline = summarize_run(baseline_dir)
    candidate = summarize_run(candidate_dir)
    baseline_ids = [item["query_id"] for item in baseline["queries"]]
    candidate_ids = [item["query_id"] for item in candidate["queries"]]
    if baseline_ids != candidate_ids:
        raise ValueError("Runs must contain the same query IDs in the same order.")

    for field in ("model", "resolved_models", "effort"):
        baseline_values = {json.dumps(item[field], sort_keys=True) for item in baseline["queries"]}
        candidate_values = {json.dumps(item[field], sort_keys=True) for item in candidate["queries"]}
        if len(baseline_values) != 1 or baseline_values != candidate_values:
            raise ValueError(f"Runs must use the same {field} for every query.")

    totals = {}
    for metric in METRICS:
        old = baseline["totals"][metric]
        new = candidate["totals"][metric]
        totals[metric] = {
            "baseline": old,
            "candidate": new,
            "change_percent": _change(old, new),
        }

    baseline_queries = {item["query_id"]: item for item in baseline["queries"]}
    query_rows = []
    for item in candidate["queries"]:
        old = baseline_queries[item["query_id"]]
        query_rows.append(
            {
                "query_id": item["query_id"],
                "baseline_actions": old["tool_actions"],
                "candidate_actions": item["tool_actions"],
                "baseline_final_text_seconds": old["time_to_final_text_seconds"],
                "candidate_final_text_seconds": item["time_to_final_text_seconds"],
                "baseline_output_tokens": old["output_tokens"],
                "candidate_output_tokens": item["output_tokens"],
            }
        )
    return {
        "baseline": baseline["label"],
        "candidate": candidate["label"],
        "model": baseline["queries"][0]["model"],
        "resolved_models": baseline["queries"][0]["resolved_models"],
        "effort": baseline["queries"][0]["effort"],
        "query_count": len(query_rows),
        "totals": totals,
        "queries": query_rows,
    }


def _markdown(comparison: dict) -> str:
    lines = [
        f"# {comparison['candidate']} vs {comparison['baseline']}",
        "",
        f"Model: `{comparison['model']}` | resolved: "
        f"`{', '.join(comparison['resolved_models'])}` | effort: "
        f"`{comparison['effort']}`",
        "",
        "Negative changes mean the candidate used less time, tokens, or tool work.",
        "",
        "| Metric | Baseline | Candidate | Change |",
        "|---|---:|---:|---:|",
    ]
    for metric, values in comparison["totals"].items():
        change = values["change_percent"]
        rendered_change = "n/a" if change is None else f"{change:+.1f}%"
        lines.append(
            f"| {metric} | {values['baseline']} | {values['candidate']} | "
            f"{rendered_change} |"
        )
    lines.extend(
        [
            "",
            "This table is operational evidence only. Read both `answer.md` files "
            "for every query and apply the manual review questions before deciding "
            "whether the candidate improved quality.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_dir", type=Path)
    parser.add_argument("candidate_dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    comparison = compare_runs(args.baseline_dir, args.candidate_dir)
    output_dir = args.output_dir or args.candidate_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison.json").write_text(json.dumps(comparison, indent=2) + "\n")
    (output_dir / "comparison.md").write_text(_markdown(comparison))
    print(json.dumps(comparison["totals"], indent=2))


if __name__ == "__main__":
    main()
