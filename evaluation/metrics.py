"""Summarize Claude Code stream-json runs without sending data to an evaluator."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


def _seconds(start: str, end: str | None) -> float | None:
    if not end:
        return None
    return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 3)


def _read_records(path: Path) -> list[dict]:
    records = []
    for line in path.read_text().splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def summarize_query(query_dir: Path) -> dict:
    metadata = json.loads((query_dir / "metadata.json").read_text())
    records = _read_records(query_dir / "events.jsonl")
    tool_uses: dict[str, str] = {}
    output_tokens: dict[str, int] = {}
    first_activity_at = None
    first_text_at = None
    final_text_at = None
    result_event = None

    for record in records:
        captured_at = record["captured_at"]
        event = record["event"]
        event_type = event.get("type")
        if event_type == "result":
            result_event = event
        if event_type == "stream_event":
            stream = event.get("event") or {}
            delta = stream.get("delta") or {}
            if stream.get("type") == "content_block_start" and first_activity_at is None:
                first_activity_at = captured_at
            if delta.get("type") == "text_delta":
                first_text_at = first_text_at or captured_at
                final_text_at = captured_at

        message = event.get("message") if event_type == "assistant" else None
        if not isinstance(message, dict):
            continue
        first_activity_at = first_activity_at or captured_at
        message_id = message.get("id")
        usage = message.get("usage") or {}
        if message_id and isinstance(usage.get("output_tokens"), int):
            output_tokens[message_id] = max(
                output_tokens.get(message_id, 0), usage["output_tokens"]
            )
        for block in message.get("content") or []:
            if block.get("type") == "tool_use" and block.get("id"):
                tool_uses[block["id"]] = block.get("name", "unknown")
            if block.get("type") == "text" and block.get("text"):
                first_text_at = first_text_at or captured_at
                final_text_at = captured_at

    tool_counts = Counter(tool_uses.values())
    result_usage = (result_event or {}).get("usage") or {}
    resolved_models = sorted(((result_event or {}).get("modelUsage") or {}).keys())
    result_failed = bool((result_event or {}).get("is_error"))
    result_subtype = (result_event or {}).get("subtype")
    if result_subtype not in (None, "success"):
        result_failed = True
    final_answer = (result_event or {}).get("result")
    if isinstance(final_answer, str) and final_answer.strip():
        (query_dir / "answer.md").write_text(final_answer.rstrip() + "\n")
    return {
        "query_id": metadata["query_id"],
        "category": metadata["category"],
        "provider": metadata["provider"],
        "model": metadata["model"],
        "resolved_models": resolved_models,
        "effort": metadata["effort"],
        "status": "timeout"
        if metadata["timed_out"]
        else (
            "ok"
            if metadata["return_code"] == 0 and result_event and not result_failed
            else "error"
        ),
        "tool_actions": len(tool_uses),
        "mcp_tool_actions": sum(
            count for name, count in tool_counts.items() if name.startswith("mcp__climateverse__")
        ),
        "tool_counts": dict(sorted(tool_counts.items())),
        "output_tokens": result_usage.get("output_tokens") or sum(output_tokens.values()),
        "time_to_first_activity_seconds": _seconds(metadata["started_at"], first_activity_at),
        "time_to_first_text_seconds": _seconds(metadata["started_at"], first_text_at),
        "time_to_final_text_seconds": _seconds(metadata["started_at"], final_text_at),
        "writing_duration_seconds": _seconds(first_text_at, final_text_at)
        if first_text_at
        else None,
        "wall_time_seconds": metadata["duration_seconds"],
        "provider_time_seconds": round((result_event or {}).get("duration_ms", 0) / 1000, 3)
        if result_event
        else None,
        "turns": (result_event or {}).get("num_turns"),
        "permission_denials": len((result_event or {}).get("permission_denials") or []),
        "cost_usd": (result_event or {}).get("total_cost_usd"),
    }


def summarize_run(run_dir: Path) -> dict:
    queries = [
        summarize_query(path)
        for path in sorted(run_dir.iterdir())
        if path.is_dir() and (path / "metadata.json").exists()
    ]
    return {
        "label": run_dir.name,
        "query_count": len(queries),
        "totals": {
            "tool_actions": sum(item["tool_actions"] for item in queries),
            "mcp_tool_actions": sum(item["mcp_tool_actions"] for item in queries),
            "output_tokens": sum(item["output_tokens"] or 0 for item in queries),
            "time_to_final_text_seconds": round(
                sum(item["time_to_final_text_seconds"] or 0 for item in queries), 3
            ),
            "writing_duration_seconds": round(
                sum(item["writing_duration_seconds"] or 0 for item in queries), 3
            ),
            "wall_time_seconds": round(sum(item["wall_time_seconds"] for item in queries), 3),
            "cost_usd": round(sum(item["cost_usd"] or 0 for item in queries), 4),
        },
        "queries": queries,
    }


def _write_csv(summary: dict, path: Path) -> None:
    fields = [key for key in summary["queries"][0] if key != "tool_counts"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: item[key] for key in fields} for item in summary["queries"])


def _write_markdown(summary: dict, path: Path) -> None:
    rows = [
        f"# Evaluation: {summary['label']}",
        "",
        "| Query | Status | Actions | MCP calls | Final text | Output tokens |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in summary["queries"]:
        rows.append(
            f"| {item['query_id']} | {item['status']} | {item['tool_actions']} | "
            f"{item['mcp_tool_actions']} | {item['time_to_final_text_seconds']}s | "
            f"{item['output_tokens']} |"
        )
    totals = summary["totals"]
    rows.append(
        f"| **Total** |  | **{totals['tool_actions']}** | "
        f"**{totals['mcp_tool_actions']}** | "
        f"**{totals['time_to_final_text_seconds']}s** | **{totals['output_tokens']}** |"
    )
    rows.extend(
        [
            "",
            "Automated metrics describe execution, not answer quality. Review each answer "
            "against the questions in `queries.json` before drawing conclusions.",
        ]
    )
    path.write_text("\n".join(rows) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    summary = summarize_run(args.run_dir)
    if not summary["queries"]:
        raise SystemExit(f"No query runs found in {args.run_dir}")
    (args.run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    _write_csv(summary, args.run_dir / "summary.csv")
    _write_markdown(summary, args.run_dir / "summary.md")
    print(json.dumps(summary["totals"], indent=2))


if __name__ == "__main__":
    main()
