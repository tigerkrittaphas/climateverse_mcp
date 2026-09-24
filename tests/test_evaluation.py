"""Tests for the local evaluation harness."""

import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).parents[1]))
from evaluation import compare, metrics  # noqa: E402


def _write_run(
    tmp_path,
    resolved_model="claude-sonnet-5",
    mcp_status="connected",
):
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
    mcp_servers = (
        [{"name": "climateverse", "status": mcp_status}]
        if mcp_status is not None
        else []
    )
    records = [
        {
            "captured_at": "2026-08-24T10:00:00.5+00:00",
            "event": {
                "type": "system",
                "subtype": "init",
                "mcp_servers": mcp_servers,
            },
        },
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
            "event": {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "permission_denials": [],
                "modelUsage": {resolved_model: {}},
                "total_cost_usd": 0.1,
            },
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
    assert summary["resolved_models"] == ["claude-sonnet-5"]
    assert summary["mcp_servers"] == {"climateverse": "connected"}
    assert summary["time_to_first_activity_seconds"] == 1
    assert summary["time_to_first_text_seconds"] == 3
    assert summary["time_to_final_text_seconds"] == 3
    assert summary["cost_usd"] == 0.1
    assert summary["status"] == "ok"
    assert summary["permission_denials"] == 0


def test_summarize_query_rejects_failed_mcp_initialization(tmp_path):
    query_dir = _write_run(tmp_path, mcp_status="failed")
    summary = metrics.summarize_query(query_dir)

    assert summary["status"] == "error"
    assert summary["mcp_servers"] == {"climateverse": "failed"}


def test_summarize_query_rejects_missing_mcp_initialization(tmp_path):
    query_dir = _write_run(tmp_path, mcp_status=None)
    summary = metrics.summarize_query(query_dir)

    assert summary["status"] == "error"
    assert summary["mcp_servers"] == {}


def test_summarize_run_aggregates_queries(tmp_path):
    query_dir = _write_run(tmp_path)
    summary = metrics.summarize_run(query_dir.parent)

    assert summary["query_count"] == 1
    assert summary["totals"]["tool_actions"] == 1
    assert summary["totals"]["output_tokens"] == 20


def test_compare_rejects_different_resolved_models(tmp_path):
    baseline = _write_run(tmp_path / "baseline")
    candidate = _write_run(tmp_path / "candidate", resolved_model="claude-sonnet-4-6")

    with pytest.raises(ValueError, match="same resolved_models"):
        compare.compare_runs(baseline.parent, candidate.parent)
