# Claude Desktop .dxt Packaging Notes

The first public release should ship a Claude Desktop `.dxt` package alongside
the HTTP MCP endpoint and the `@smart-crawler/mcp` install helper.

## Target behavior

- User installs the package.
- Package asks for a `SMARTCRAWLER_API_KEY`.
- Package registers `https://smartcrawler.io/mcp`.
- Tool descriptions emphasize `query_warehouse(intent)` before `scrape_url(url)`.
- Package README points to the 50-task benchmark summary and cost-aware usage
  contract.

## Generate manifest draft

```bash
npx -y @smart-crawler/mcp dxt --env-var SMARTCRAWLER_API_KEY --json
```

For local verification:

```bash
npx -y @smart-crawler/mcp dxt --local --json
```

## Manifest draft

```json
{
  "name": "smart-crawler",
  "display_name": "smart-crawler",
  "version": "0.1.0",
  "description": "Agent-first ecommerce crawler with warehouse-first search, memory, and cost-aware MCP tools.",
  "server": {
    "type": "http",
    "url": "https://smartcrawler.io/mcp",
    "headers": {
      "Authorization": "Bearer ${SMARTCRAWLER_API_KEY}"
    }
  },
  "tools": {
    "primary": [
      "query_warehouse",
      "scrape_url",
      "crawl_site"
    ]
  }
}
```

## Package contents

- `manifest.json` generated from `npx -y @smart-crawler/mcp dxt --json`.
- `README.md` with:
  - Primary tool strategy: `query_warehouse` → `scrape_url` → `crawl_site`.
  - Cost rules: warehouse `0`, memory hit `0`, advanced scrape `3`, dry-run crawl
    `0`.
  - Required key scope: `crawler:read` + `crawler:scrape`; `crawler:crawl` only
    for real full-site crawl.
- Optional gallery image or benchmark table for marketplace listing.

## Release checklist

- Rotate any historical API/proxy credentials before public distribution.
- Verify `query_warehouse`, `scrape_url`, and `crawl_site dry_run=true` in Claude.
- Verify `scrape_url(mode="advanced")` only when Playwright/browser_pool is
  enabled on the backing server.
- Include the 50-task benchmark summary in the package gallery/readme.
- Run:

```bash
cd packages/mcp
npm test
node --check bin/smart-crawler-mcp.js
```
