# climateverse-mcp

MCP server for ClimateVerse, built with [FastMCP](https://gofastmcp.com).

## Setup

```bash
uv sync
```

## Configuration

The server calls ClimateVerse for dataset details. Dataset discovery uses
AIFindr hybrid search by default, with Dataverse keyword search available as an
explicit comparison mode. Credentials are read from the environment (or a
local `.env` file; see `.env.example`):

| Variable | Required | Description |
|---|---|---|
| `CLIMATEVERSE_API_KEY` | no | Your ClimateVerse API key. Without one, only published datasets are visible |
| `CLIMATEVERSE_API_BASE_URL` | no | API base URL override |
| `CLIMATEVERSE_SEARCH_PROVIDER` | no | `aifindr` (default) or `dataverse`; there is no silent fallback |
| `AIFINDR_API_KEY` | yes for search | Raw private key; the server adds `Bearer` |
| `AIFINDR_BASE_URL` | yes for AIFindr search | AIFindr backend origin |
| `AIFINDR_ORG_ID` | yes for search | Organization containing the project |
| `AIFINDR_PROJECT_ID` | yes for search | Project whose knowledge is searched |
| `AIFINDR_SEARCH_ALPHA` | no | Hybrid weight from 0 (keyword) to 1 (semantic); default 0.7 |

MCP clients pass the key through the `env` block of the server entry — the
key stays in the user's local config and is never sent through the model.
The AIFindr key needs `source:get` access to the configured project. When the
default provider is not fully configured, search fails with a clear error. Set
`CLIMATEVERSE_SEARCH_PROVIDER=dataverse` to use the original catalog search.

## Run

```bash
# stdio (default — for Claude Desktop / Claude Code)
uv run climateverse-mcp

# dev mode with the MCP Inspector
uv run fastmcp dev src/climateverse_mcp/server.py
```

## Hosted deployment

The same server runs over streamable HTTP behind OAuth at
`https://mcp.climateverse.net/mcp`. The AWS side (ECS service, Cognito user
pool, DynamoDB OAuth store, S3 reports bucket) lives in `climateverse-infra`
as `mcp.tf`, enabled on the `global` workspace. Pushing to `main` runs
`.github/workflows/deploy.yml`: tests, then build, push to ECR as
`prod-global`, and roll the service.

```bash
claude mcp add --transport http climateverse https://mcp.climateverse.net/mcp
```

Users sign in through the Cognito user pool (invite-only). Without a Dataverse
key the server reads anonymously and sees published datasets only. To use your
own Dataverse permissions (drafts, restricted files), send your Dataverse API
token in the `X-Dataverse-Key` header. With Claude Code, keep the token in an
environment variable and reference it from `.mcp.json`, which expands `${VAR}`
when the server starts, so the token never sits in the config file:

```json
{
  "mcpServers": {
    "climateverse": {
      "type": "http",
      "url": "https://mcp.climateverse.net/mcp",
      "headers": { "X-Dataverse-Key": "${DATAVERSE_API_KEY}" }
    }
  }
}
```

The header is used only for calls to the configured Dataverse and is never
logged or forwarded elsewhere; a key Dataverse rejects is reported as such.
Clients that cannot set custom headers (such as claude.ai connectors) get the
anonymous, published-only view.
On the hosted server `render_report` stores the report and returns a
shareable link under `/reports/` instead of writing to disk.

Hosted mode is configured by environment variables, all set by the ECS task
definition:

| Variable | Description |
|---|---|
| `CLIMATEVERSE_TRANSPORT` | `http` to serve over HTTP (default `stdio`) |
| `CLIMATEVERSE_HTTP_HOST` / `_PORT` | Bind address (the image uses `0.0.0.0:8000`) |
| `CLIMATEVERSE_PUBLIC_BASE_URL` | Public origin, e.g. `https://mcp.climateverse.net` |
| `CLIMATEVERSE_COGNITO_USER_POOL_ID`, `_REGION`, `_CLIENT_ID`, `_CLIENT_SECRET` | Cognito app client |
| `CLIMATEVERSE_OAUTH_JWT_SIGNING_KEY` | Signs the tokens issued to MCP clients |
| `CLIMATEVERSE_OAUTH_STORAGE_KEY` | Fernet key encrypting OAuth state at rest |
| `CLIMATEVERSE_OAUTH_TABLE` | DynamoDB table for OAuth state |
| `CLIMATEVERSE_REPORTS_BUCKET` | S3 bucket for rendered reports |

HTTP mode refuses to start with OAuth partly or not configured. For a quick
local check without auth:

```bash
docker build -t climateverse-mcp .
docker run --rm -p 8000:8000 --env-file .env \
  -e CLIMATEVERSE_ALLOW_UNAUTHENTICATED=true climateverse-mcp
curl localhost:8000/health
```

## Test

```bash
uv run pytest
```

## Use with Claude Code

```bash
claude mcp add climateverse \
  --env CLIMATEVERSE_API_KEY=your-api-key-here \
  --env CLIMATEVERSE_SEARCH_PROVIDER=aifindr \
  --env AIFINDR_BASE_URL=https://your-aifindr-api.example.com \
  --env AIFINDR_API_KEY=key_your-private-api-key \
  --env AIFINDR_ORG_ID=org_your-organization-id \
  --env AIFINDR_PROJECT_ID=prj_your-project-id \
  -- uv run --directory /path/to/climateverse_mcp climateverse-mcp
```

Or in Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "climateverse": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/climateverse_mcp", "climateverse-mcp"],
      "env": {
        "CLIMATEVERSE_API_KEY": "your-api-key-here",
        "CLIMATEVERSE_SEARCH_PROVIDER": "aifindr",
        "AIFINDR_BASE_URL": "https://your-aifindr-api.example.com",
        "AIFINDR_API_KEY": "key_your-private-api-key",
        "AIFINDR_ORG_ID": "org_your-organization-id",
        "AIFINDR_PROJECT_ID": "prj_your-project-id"
      }
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
