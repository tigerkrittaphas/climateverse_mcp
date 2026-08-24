# ClimateVerse MCP evaluation

This harness compares AIFindr hybrid search with the original Dataverse keyword
search under the same Claude Code client, model, effort, prompts, and MCP code.
It captures execution metrics locally; it does not use an automated evaluator.
Answer quality still requires manual review because evaluators and proxies can
reward a fluent but unsupported answer.

## Run the comparison

Export the credentials without committing them:

```bash
export CLIMATEVERSE_API_KEY=...
export AIFINDR_BASE_URL=...
export AIFINDR_API_KEY=...
export AIFINDR_ORG_ID=...
export AIFINDR_PROJECT_ID=...
```

Then run both providers. The first command uses AIFindr, which is the MCP's
default; the second selects Dataverse explicitly.

```bash
python -m evaluation.run --label aifindr --provider aifindr --model claude-sonnet-5
python -m evaluation.run --label dataverse --provider dataverse --model claude-sonnet-5
python -m evaluation.metrics evaluation/runs/aifindr
python -m evaluation.metrics evaluation/runs/dataverse
python -m evaluation.compare evaluation/runs/dataverse evaluation/runs/aifindr
```

Use `--query q1 --query q2` for a smaller smoke test. Each query gets a fresh
Claude session and a strict temporary MCP configuration so global MCP servers
cannot affect the result. The temporary file contains the credentials, is
created with user-only permissions, and is deleted after the run. Secrets are
never written to the run artifacts.

The default is pinned to `claude-sonnet-5`; do not use the moving `sonnet`
alias for comparison runs. The summary records `resolved_models` from Claude's
result event so model drift is visible.

## Metrics

1. `tool_actions`: unique Claude tool-use block IDs, including built-in tools.
2. `mcp_tool_actions`: actions whose name starts with `mcp__climateverse__`.
3. `time_to_first_activity_seconds`: process start to the first assistant event.
4. `time_to_first_text_seconds`: process start to the first streamed text token.
5. `time_to_final_text_seconds`: process start to the last streamed answer text.
6. `writing_duration_seconds`: first answer text to last answer text.
7. `wall_time_seconds`: process start until Claude exits, including MCP startup.
8. `output_tokens`: aggregate provider output tokens from Claude's result event.

Running the summarizer also extracts each final response to `answer.md`, next to
its raw events and metadata, so reviewers can inspect the answer directly.
Runs are marked as errors unless the configured Climateverse MCP explicitly
reports `connected`, even if Claude exits successfully with an explanatory response.

Raw events, stderr, metadata, and summaries stay under `evaluation/runs/`, which
is gitignored because responses may quote catalog content. Historical Desktop
totals are deliberately excluded: comparisons must use paired CLI runs because
client overhead and tool availability can change the numbers.

## Manual review

Review every answer using the query-specific questions in `queries.json`.
Check relevance, factual grounding, DOI/source traceability, instruction
following, calibrated uncertainty, and whether failures stop at the correct
boundary. Record disagreements and qualitative regressions next to the numeric
summary before deciding whether one provider is better.
