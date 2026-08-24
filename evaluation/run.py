"""Run the frozen ClimateVerse MCP queries through isolated Claude Code sessions."""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUERY_FILE = Path(__file__).with_name("queries.json")
DEFAULT_RUNS_DIR = Path(__file__).with_name("runs")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_queries(selected: set[str]) -> list[dict]:
    queries = json.loads(QUERY_FILE.read_text())
    known = {item["id"] for item in queries}
    unknown = selected - known
    if unknown:
        raise SystemExit(f"Unknown query IDs: {', '.join(sorted(unknown))}")
    return [item for item in queries if not selected or item["id"] in selected]


def _mcp_environment(provider: str) -> dict[str, str]:
    names = ["CLIMATEVERSE_API_KEY", "CLIMATEVERSE_API_BASE_URL"]
    if provider == "aifindr":
        names.extend(
            [
                "AIFINDR_BASE_URL",
                "AIFINDR_API_KEY",
                "AIFINDR_ORG_ID",
                "AIFINDR_PROJECT_ID",
                "AIFINDR_SEARCH_ALPHA",
            ]
        )
    values = {name: os.environ[name] for name in names if os.environ.get(name)}
    values["CLIMATEVERSE_SEARCH_PROVIDER"] = provider

    required = ["CLIMATEVERSE_API_KEY"]
    if provider == "aifindr":
        required.extend(
            [
                "AIFINDR_BASE_URL",
                "AIFINDR_API_KEY",
                "AIFINDR_ORG_ID",
                "AIFINDR_PROJECT_ID",
            ]
        )
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")
    return values


def _pump(stream, name: str, output: queue.Queue) -> None:
    for line in iter(stream.readline, ""):
        output.put((name, _now(), line.rstrip("\n")))
    output.put((name, _now(), None))


def _run_query(
    query: dict,
    run_dir: Path,
    provider: str,
    model: str,
    effort: str,
    timeout_seconds: float,
    max_budget_usd: float,
    mcp_config: Path,
) -> dict:
    query_dir = run_dir / query["id"]
    query_dir.mkdir(parents=True)
    events_path = query_dir / "events.jsonl"
    stderr_path = query_dir / "stderr.log"
    session_id = str(uuid.uuid4())
    command = [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model",
        model,
        "--effort",
        effort,
        "--max-budget-usd",
        str(max_budget_usd),
        "--mcp-config",
        str(mcp_config),
        "--strict-mcp-config",
        "--no-session-persistence",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools=mcp__climateverse__*,Bash,Read,Write,WebFetch,WebSearch",
        "--tools",
        "default",
        "--session-id",
        session_id,
        query["prompt"],
    ]
    started_at = _now()
    started_monotonic = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    output: queue.Queue = queue.Queue()
    threads = [
        threading.Thread(target=_pump, args=(process.stdout, "stdout", output)),
        threading.Thread(target=_pump, args=(process.stderr, "stderr", output)),
    ]
    for thread in threads:
        thread.start()

    open_streams = 2
    timed_out = False
    with events_path.open("w") as events, stderr_path.open("w") as errors:
        while open_streams:
            if time.monotonic() - started_monotonic > timeout_seconds:
                timed_out = True
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                timeout_seconds = float("inf")
            try:
                stream, captured_at, line = output.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                open_streams -= 1
                continue
            if stream == "stderr":
                errors.write(f"[{captured_at}] {line}\n")
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {"type": "unparsed_stdout", "text": line}
            events.write(json.dumps({"captured_at": captured_at, "event": event}) + "\n")

    return_code = process.wait()
    for thread in threads:
        thread.join()
    ended_at = _now()
    metadata = {
        "query_id": query["id"],
        "category": query["category"],
        "prompt": query["prompt"],
        "provider": provider,
        "model": model,
        "effort": effort,
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": round(time.monotonic() - started_monotonic, 3),
        "return_code": return_code,
        "timed_out": timed_out,
        "max_budget_usd": max_budget_usd,
        "review_questions": query["review_questions"],
    }
    (query_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="Run label, e.g. aifindr-2026-08-24")
    parser.add_argument("--provider", choices=("aifindr", "dataverse"), default="aifindr")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--effort", choices=("low", "medium", "high", "max"), default="high")
    parser.add_argument("--timeout-seconds", type=float, default=240)
    parser.add_argument("--max-budget-usd", type=float, default=2.0)
    parser.add_argument("--query", action="append", default=[], help="Query ID; repeat to select several")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    args = parser.parse_args()

    queries = _load_queries(set(args.query))
    environment = _mcp_environment(args.provider)
    run_dir = args.runs_dir / args.label
    if run_dir.exists() and any(run_dir.iterdir()):
        raise SystemExit(f"Run directory is not empty: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "mcpServers": {
            "climateverse": {
                "command": "uv",
                "args": ["run", "--directory", str(ROOT), "climateverse-mcp"],
                "env": environment,
            }
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(config, handle)
        config_path = Path(handle.name)
    config_path.chmod(0o600)

    results = []
    try:
        for query in queries:
            print(f"Running {query['id']} with {args.provider} search...", flush=True)
            results.append(
                _run_query(
                    query,
                    run_dir,
                    args.provider,
                    args.model,
                    args.effort,
                    args.timeout_seconds,
                    args.max_budget_usd,
                    config_path,
                )
            )
    finally:
        config_path.unlink(missing_ok=True)

    manifest = {
        "label": args.label,
        "provider": args.provider,
        "model": args.model,
        "effort": args.effort,
        "queries": [result["query_id"] for result in results],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Saved {len(results)} runs to {run_dir}")


if __name__ == "__main__":
    main()
