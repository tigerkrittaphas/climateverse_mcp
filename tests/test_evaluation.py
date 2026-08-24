"""Tests for the local evaluation metric extractor."""

import json
import importlib.util
from pathlib import Path


METRICS_PATH = Path(__file__).parents[1] / "evaluation" / "metrics.py"
SPEC = importlib.util.spec_from_file_location("climateverse_evaluation_metrics", METRICS_PATH)
metrics = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(metrics)


def _write_run(tmp_path):
    query_dir = tmp_path / "sample" / "q1"
    query_dir.mkdir(parents=True)
    metadata = {
        "query_id": "q1",
        "category": "discovery",
        "provider": "aifindr",
        "model": "sonnet",
        "effort": "high",
        "started_at": "2026-08-24T10:00:00+00:00",
        "duration_seconds": 4.5,
        "return_code": 0,
        "timed_out": False,
    }
    (query_dir / "metadata.json").write_text(json.dumps(metadata))
    records = [
        {
            "captured_at": "2026-08-24T10:00:01+00:00",
            "event": {
                "type": "assistant",
                "message": {
                    "id": "msg_1",
                    "usage": {"output_tokens": 12},
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_1",
                            "name": "mcp__climateverse__search_datasets",
                        }
                    ],
                },
            },
        },
        {
            "captured_at": "2026-08-24T10:00:02+00:00",
            "event": {
                "type": "assistant",
                "message": {
                    "id": "msg_1",
                    "usage": {"output_tokens": 12},
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_1",
                            "name": "mcp__climateverse__search_datasets",
                        }
                    ],
                },
            },
        },
        {
            "captured_at": "2026-08-24T10:00:03+00:00",
            "event": {
                "type": "assistant",
                "message": {
                    "id": "msg_2",
                    "usage": {"output_tokens": 8},
                    "content": [{"type": "text", "text": "Answer"}],
                },
            },
        },
        {
            "captured_at": "2026-08-24T10:00:04+00:00",
            "event": {"type": "result", "total_cost_usd": 0.1},
        },
    ]
    (query_dir / "events.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records)
    )
    return query_dir


def test_summarize_query_deduplicates_messages_and_tool_uses(tmp_path):
    query_dir = _write_run(tmp_path)
    summary = metrics.summarize_query(query_dir)

    assert summary["tool_actions"] == 1
    assert summary["mcp_tool_actions"] == 1
    assert summary["output_tokens"] == 20
    assert summary["resolved_models"] == []
    assert summary["time_to_first_activity_seconds"] == 1
    assert summary["time_to_first_text_seconds"] == 3
    assert summary["time_to_final_text_seconds"] == 3
    assert summary["cost_usd"] == 0.1


def test_summarize_run_aggregates_queries(tmp_path):
    query_dir = _write_run(tmp_path)
    summary = metrics.summarize_run(query_dir.parent)

    assert summary["query_count"] == 1
    assert summary["totals"]["tool_actions"] == 1
    assert summary["totals"]["output_tokens"] == 20
