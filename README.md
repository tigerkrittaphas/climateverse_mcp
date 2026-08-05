# climateverse-mcp

MCP server for ClimateVerse, built with [FastMCP](https://gofastmcp.com).

## Setup

```bash
uv sync
```

## Configuration

The server calls the ClimateVerse API and needs an API key, read from the
environment (or a local `.env` file — see `.env.example`):

| Variable | Required | Description |
|---|---|---|
| `CLIMATEVERSE_API_KEY` | yes | Your ClimateVerse API key |
| `CLIMATEVERSE_API_BASE_URL` | no | API base URL override |

MCP clients pass the key through the `env` block of the server entry — the
key stays in the user's local config and is never sent through the model.

## Run

```bash
# stdio (default — for Claude Desktop / Claude Code)
uv run climateverse-mcp

# dev mode with the MCP Inspector
uv run fastmcp dev src/climateverse_mcp/server.py
```

## Test

```bash
uv run pytest
```

## Use with Claude Code

```bash
claude mcp add climateverse \
  --env CLIMATEVERSE_API_KEY=your-api-key-here \
  -- uv run --directory /path/to/climateverse_mcp climateverse-mcp
```

Or in Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "climateverse": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/climateverse_mcp", "climateverse-mcp"],
      "env": { "CLIMATEVERSE_API_KEY": "your-api-key-here" }
    }
  }
}
```

## Structure

```
src/climateverse_mcp/
  server.py      # FastMCP instance, tools, resources, prompts
  instruction.py # server instructions, EDA briefing, report template contract
  report.py      # assembles the branded report shell around agent content
  brand/         # report identity — see brand/README.md
tests/
  test_server.py
```

## Two ways in

| Ask | Tool | What it does |
|---|---|---|
| "Help me make this question specific enough to analyze" | `clarify_research_question(question)` | Uses MCP elicitation to ask about the goal, geography, time/season, indicator definition, comparison, audience, and intended use, then returns a reusable research brief. |
| "Profile this dataset" | `eda(doi)` | Briefing for one known dataset: metadata + codebook + step-by-step EDA. |
| "Answer this question" | `research(question, dois)` | Briefing for a question that may span datasets: candidate DOIs for the question, how to verify them, whether they can legitimately be combined, and a mandatory join audit. |

Both end the same way — by calling `render_report()`.

Call `clarify_research_question()` before `research()` when a consequential
choice is missing or ambiguous. The tool opens one structured form rather than
making the user answer a long sequence of chat questions. Its audience and use
fields default to an internal exploratory working note for readers new to the
topic, but the user can override them. The MCP client must support elicitation.

`research()` exists because combining datasets is where a report goes wrong
*quietly*. Its briefing requires row counts before and after every join, the
keys that did and did not match, and the filters applied, because a join that
silently halves the data still produces a confident-looking number. It also
names the concrete hazards in this catalog: Indian district and state
boundaries move (Telangana separated from Andhra Pradesh in 2014), the same
district appears under different spellings across publishers, and columns with
matching names can carry incompatible definitions — a fatality-conditioned
hazard count is not a meteorological frequency count.

Codebooks are large, so `research()` bundles them only for DOIs you name, up to
three; the rest are listed for you to pull with `get_codebook(doi)`.

## Report branding

Reports are delivered through `render_report()`, not hand-rolled by the calling
agent. The server injects the ClimateVerse logo, masthead, colour system and
print styles; the agent writes only body content against the `cv-*` classes
listed in the `eda()` briefing.

That split is deliberate. It keeps every report visually identical, keeps
`brand/tokens.css` the single source of truth, and keeps ~10k tokens of CSS and
base64 logo out of the model's context. See `src/climateverse_mcp/brand/README.md`
for the palette, its validation, and the component vocabulary.
