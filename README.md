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
  server.py    # FastMCP instance, tools, resources, prompts
tests/
  test_server.py
```
