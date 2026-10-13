# ClimateVerse MCP

An [MCP](https://modelcontextprotocol.io) server that lets AI agents find,
understand and analyse climate datasets in the
[ClimateVerse](https://india.climateverse.net) catalog. It turns a question
into a scoped research brief, finds the right datasets, explains their
codebooks, samples the data, and delivers a branded HTML report. Built with
[FastMCP](https://gofastmcp.com).

There are two ways to run it:

- **Hosted**, at `https://mcp.climateverse.net/mcp`. Nothing to install: you
  sign in with your ClimateVerse API token and see what your account can see.
- **Locally** over stdio, from a checkout of this repository, configured with
  environment variables.

## Contents

- [Quick start (hosted)](#quick-start-hosted)
- [What it can do](#what-it-can-do)
- [Sign-in and data access](#sign-in-and-data-access)
- [Run locally](#run-locally)
- [Configuration reference](#configuration-reference)
- [Reports](#reports)
- [Hosted deployment](#hosted-deployment)
- [Development](#development)

## Quick start (hosted)

**Claude Code**

```bash
claude mcp add --transport http --scope user climateverse https://mcp.climateverse.net/mcp
```

Drop `--scope user` to add it to the current project only. Then, in Claude
Code, run `/mcp`, choose **climateverse** and **Authenticate**. A browser page
opens; paste your API token there (see [Sign-in](#sign-in-and-data-access)).

**claude.ai and Claude Desktop**

Add a custom connector with the URL `https://mcp.climateverse.net/mcp` and
connect it; the same sign-in page opens.

**Other MCP clients**

Any client that supports streamable HTTP with OAuth works: point it at
`https://mcp.climateverse.net/mcp`. Clients may register dynamically or
identify themselves with a Client ID Metadata Document URL.

Then ask something like *"What ClimateVerse datasets cover heatwaves in
Maharashtra, and how do they define a heatwave?"* or *"Profile
doi:10.71646/IEPUSS and write me a report."*

## What it can do

### Workflows

| You ask | Tool | What happens |
|---|---|---|
| "Help me make this question specific enough to analyse" | `clarify_research_question(question)` | Opens one structured form (MCP elicitation) for goal, geography, time and season, indicator definition, comparison, audience and intended use, then returns a reusable research brief. |
| "Profile this dataset" | `eda(doi)` | A briefing for one known dataset: metadata, codebook and a step-by-step EDA plan. |
| "Answer this question" | `research(question, dois)` | A briefing for a question that may span datasets: candidate DOIs, how to verify them, whether they can legitimately be combined, and a mandatory join audit. |

Every workflow ends by calling `render_report()`.

Call `clarify_research_question()` before `research()` when a consequential
choice is missing or ambiguous. Its audience and use fields default to an
internal exploratory note for readers new to the topic. The client must
support elicitation.

`research()` exists because combining datasets is where a report goes wrong
*quietly*. Its briefing requires row counts before and after every join, the
keys that did and did not match, and the filters applied, because a join that
silently halves the data still produces a confident-looking number. It names
the concrete hazards in this catalog: Indian district and state boundaries move
(Telangana separated from Andhra Pradesh in 2014), the same district appears
under different spellings across publishers, and columns with matching names
can carry incompatible definitions; a fatality-conditioned hazard count is not
a meteorological frequency count.

Codebooks are large, so `research()` bundles them only for DOIs you name, up
to three; the rest are listed for you to pull with `get_codebook(doi)`.

### All tools

| Tool | Purpose |
|---|---|
| `search_datasets(query, limit)` | Find relevant datasets with the configured search provider (AIFindr hybrid search or Dataverse keyword search). |
| `list_datasets(limit)` | List datasets in the catalog. |
| `describe_dataset(doi)` | Metadata: title, description, authors, coverage, license and file list. |
| `get_codebook(doi)` | The dataset's `codebook.md`, the primary description of its variables and files. |
| `fetch_sample(doi, url, max_bytes)` | A bounded sample of a data file documented in the codebook. Refuses non-public addresses. |
| `clarify_research_question(question)` | Structured scoping form; returns a research brief. |
| `eda(doi)` | EDA briefing for one dataset. |
| `research(question, dois)` | Research briefing across datasets. |
| `render_report(title, body_html, ...)` | Wraps report content in the branded ClimateVerse shell. |
| `ping()` | Health check. |

## Sign-in and data access

### Signing in to the hosted server

The hosted server signs you in with your **ClimateVerse API token**; there is
no separate account.

1. When your client connects, a *Sign in with ClimateVerse* page opens. It
   names the client asking for access.
2. Get your token from your ClimateVerse account page:
   **your name → API Token** on
   [india.climateverse.net](https://india.climateverse.net/dataverseuser.xhtml?selectTab=apiTokenTab).
   Create one if you have none.
3. Paste it and choose **Sign in**. The server checks the token with
   ClimateVerse and sends you back to your client.

Every tool call then runs as you: you see published datasets plus any drafts or
restricted files your account can access.

- **Expiry.** Access tokens last an hour and are refreshed automatically. Each
  refresh re-checks your ClimateVerse token, so if it expires or you revoke it,
  your client asks you to sign in again.
- **Switching accounts.** In Claude Code, run `/mcp`, choose **climateverse**,
  clear its authentication, then authenticate again.
- **Privacy.** Your token is stored encrypted at rest, used only for calls to
  ClimateVerse, and never logged or sent anywhere else. Bearer tokens are
  stored only as hashes.

### Sending a token in a header

A client that can set custom headers may send a Dataverse API token in the
`X-Dataverse-Key` header; it takes precedence over the signed-in one. With
Claude Code, keep the token in an environment variable and reference it from
`.mcp.json`, which expands `${VAR}` when the server starts, so the token never
sits in the config file:

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

### Which key a call uses

For every request the server uses the first key it finds:

1. the `X-Dataverse-Key` header;
2. the token you signed in with (hosted);
3. `CLIMATEVERSE_API_KEY` from the server's environment (local);
4. none: anonymous, which sees published datasets only.

A key ClimateVerse rejects is reported as such, with what to do next.

## Run locally

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
git clone https://github.com/tigerkrittaphas/climateverse_mcp.git
cd climateverse_mcp
uv sync
cp .env.example .env   # then fill in what you need
```

The local server speaks stdio and reads its settings from the environment or
`.env` (see [Configuration](#local-stdio)). Without `CLIMATEVERSE_API_KEY` it
reads published datasets anonymously.

**Claude Code**

```bash
claude mcp add climateverse \
  --env CLIMATEVERSE_API_KEY=your-api-key \
  --env CLIMATEVERSE_SEARCH_PROVIDER=dataverse \
  -- uv run --directory /path/to/climateverse_mcp climateverse-mcp
```

**Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "climateverse": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/climateverse_mcp", "climateverse-mcp"],
      "env": {
        "CLIMATEVERSE_API_KEY": "your-api-key",
        "CLIMATEVERSE_SEARCH_PROVIDER": "dataverse"
      }
    }
  }
}
```

Keys passed in the client's `env` block stay in your local config and never go
through the model. To use AIFindr search instead, set
`CLIMATEVERSE_SEARCH_PROVIDER=aifindr` (the default) and the `AIFINDR_*`
variables below.

**Directly**

```bash
uv run climateverse-mcp                            # stdio
uv run fastmcp dev src/climateverse_mcp/server.py  # with the MCP Inspector
```

## Configuration reference

### Local (stdio)

| Variable | Default | Description |
|---|---|---|
| `CLIMATEVERSE_API_KEY` | none | Your ClimateVerse API token. Without it, only published datasets are visible. |
| `CLIMATEVERSE_API_BASE_URL` | `https://india.climateverse.net/` | The ClimateVerse (Dataverse) catalog to read. |
| `CLIMATEVERSE_SEARCH_PROVIDER` | `aifindr` | `aifindr` (hybrid) or `dataverse` (keyword). There is no silent fallback: if AIFindr is selected but not configured, search fails with a clear error. |
| `AIFINDR_BASE_URL` | none | AIFindr backend origin. Required for AIFindr search. |
| `AIFINDR_API_KEY` | none | Private key with `source:get` on the project; the server adds `Bearer`. Required for AIFindr search. |
| `AIFINDR_ORG_ID` | none | Organization containing the project. Required for AIFindr search. |
| `AIFINDR_PROJECT_ID` | none | Project whose knowledge is searched. Required for AIFindr search. |
| `AIFINDR_SEARCH_ALPHA` | `0.7` | Hybrid weight from 0 (keyword) to 1 (semantic). |

### Hosted (HTTP)

Set by the ECS task definition in `climateverse-infra`, in addition to the
variables above:

| Variable | Description |
|---|---|
| `CLIMATEVERSE_TRANSPORT` | `http` to serve over streamable HTTP (default `stdio`). |
| `CLIMATEVERSE_HTTP_HOST` / `_PORT` | Bind address; the image uses `0.0.0.0:8000`. |
| `CLIMATEVERSE_PUBLIC_BASE_URL` | Public origin, e.g. `https://mcp.climateverse.net`. Used in OAuth metadata and report links. |
| `CLIMATEVERSE_API_BASE_URL` | The catalog users' sign-in tokens are checked against and tools read. |
| `CLIMATEVERSE_OAUTH_STORAGE_KEY` | Fernet key encrypting OAuth state and users' API tokens at rest. |
| `CLIMATEVERSE_OAUTH_TABLE` | DynamoDB table for OAuth state. |
| `CLIMATEVERSE_AWS_REGION` | Region of that table (default `ap-south-1`). |
| `CLIMATEVERSE_REPORTS_BUCKET` | S3 bucket for rendered reports. |
| `CLIMATEVERSE_ALLOW_UNAUTHENTICATED` | `true` to serve HTTP without sign-in. Local testing only. |

HTTP mode refuses to start when OAuth is partly or not configured, unless
`CLIMATEVERSE_ALLOW_UNAUTHENTICATED=true` is set explicitly.

## Reports

Reports are produced by `render_report()`, not hand-rolled by the agent. The
server wraps the agent's body content in the ClimateVerse logo, masthead,
colour system and print styles; the agent writes only body HTML against the
`cv-*` classes listed in the `eda()` briefing.

That split keeps every report visually identical, keeps `brand/tokens.css` the
single source of truth, and keeps about 10k tokens of CSS and base64 logo out
of the model's context. See
[`src/climateverse_mcp/brand/README.md`](src/climateverse_mcp/brand/README.md)
for the palette, its validation and the component vocabulary.

- **Locally**, the report is written to the `.html` path you give (`out_path`).
- **Hosted**, it is stored in S3 and a shareable link under
  `https://mcp.climateverse.net/reports/` is returned. Reports are served with a
  sandboxing Content-Security-Policy.

## Hosted deployment

The AWS side (ECS service, ALB routing, DynamoDB OAuth store, S3 reports
bucket, runtime secret and GitHub deploy role) lives in
[`climateverse-infra`](https://github.com/Harvard-Data-Science-Initiative/climateverse-infra)
as `mcp.tf`, enabled on the `global` workspace.

Pushing to `main` runs `.github/workflows/deploy.yml`: tests, then build, push
to ECR as `prod-global`, and roll the ECS service. Infrastructure changes are
applied separately with Terraform. When a change needs both, check which order
keeps the running image and task definition compatible: deploy the image
first when the new code tolerates the old settings.

How sign-in works internally: Dataverse is not an OAuth provider, so the
server is its own OAuth authorization server
(`DataverseTokenProvider` in `auth.py`). Clients register dynamically or use a
Client ID Metadata Document; `/authorize` sends the browser to `/login`, where
the token is checked with `GET /api/users/:me`. Client registrations, issued
tokens and users' API tokens are kept in DynamoDB behind Fernet encryption, so
sessions survive restarts and deploys. Allowed client redirect URIs are
loopback addresses and claude.ai's callback.

To try the HTTP server locally without sign-in:

```bash
docker build -t climateverse-mcp .
docker run --rm -p 8000:8000 --env-file .env \
  -e CLIMATEVERSE_ALLOW_UNAUTHENTICATED=true climateverse-mcp
curl localhost:8000/health
```

## Development

```bash
uv sync --all-extras   # includes the AWS extras used by the hosted server
uv run pytest
```

Tests are hermetic: Dataverse, AIFindr and S3 are faked, and the sign-in flow
runs end to end against an in-memory store.

`evaluation/` compares AIFindr hybrid search with Dataverse keyword search
under the same client, model and prompts. See
[`evaluation/README.md`](evaluation/README.md).

### Project structure

```
src/climateverse_mcp/
  server.py        # FastMCP instance, tools, resources, prompts, HTTP routes
  api.py           # ClimateVerse (Dataverse) client and per-request key resolution
  aifindr.py       # AIFindr hybrid search
  auth.py          # OAuth sign-in with a ClimateVerse API token (hosted)
  settings.py      # configuration from environment / .env
  instruction.py   # server instructions, EDA and research briefings, report contract
  report.py        # assembles the branded report shell around agent content
  report_store.py  # S3 storage for hosted reports
  brand/           # report identity; see brand/README.md
tests/
  test_server.py   # tools, search providers, reports, key resolution
  test_auth.py     # sign-in flow, tokens, refresh, client metadata documents
evaluation/        # search provider comparison harness
Dockerfile         # hosted image
```
