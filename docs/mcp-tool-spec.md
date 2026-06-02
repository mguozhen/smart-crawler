# smart-crawler MCP Tool Spec

This document defines the public Agent-first MCP surface. Keep Agent prompts and
marketplace copy focused on the three primary tools below.

## Agent Strategy

1. Call `query_warehouse(intent, limit)` first for any product, competitor,
   category, price, or coverage question.
2. Call `scrape_url(url)` only when the agent needs fresh page content or a URL
   is not represented well in the warehouse.
3. Call `crawl_site(url)` only to estimate a full crawl. It defaults to
   `dry_run=true`; real crawl execution requires `crawler:crawl`.

Historical MCP tools remain available for compatibility, but they are marked
`[LEGACY]` or `[ADVANCED]` in tool descriptions and should not be selected first.

## Primary Tools

### `query_warehouse(intent, limit=20)`

- Purpose: natural-language warehouse query against existing smart-crawler data.
- Agent default: pass only `intent`.
- Scope: `crawler:read`.
- Credits: always `0`.
- Cache: 5-minute agent memory per API key and normalized arguments.
- Use when: user asks to find, compare, check coverage, search products, or
  inspect known competitor data.

Example:

```json
{"intent": "vidaxl patio storage top discounts", "limit": 20}
```

### `scrape_url(url, formats=null, force_live=false, mode="standard")`

- Purpose: scrape one URL with warehouse-first behavior.
- Agent default: pass only `url`.
- Scope: `crawler:scrape`.
- Credits: warehouse hit `0`; agent memory hit `0`; live scrape normally costs
  credits; `mode="advanced"` uses browser_pool rendering and costs more.
- Cache: enabled for standard calls unless `force_live=true` or
  `mode="advanced"`.
- Advanced parameters: `formats`, `force_live`, and `mode` exist for
  compatibility; ordinary agents should omit them.
- Advanced mode: `mode="advanced"` borrows a short-lived browser_pool
  `local_playwright` session, renders the page, returns `source="advanced"`,
  and currently costs `3` credits when successful. Use it only after warehouse
  and standard scrape are not enough.

Example:

```json
{"url": "https://www.songmics.com/"}
```

### `crawl_site(url, limit=1000, dry_run=true)`

- Purpose: validate or trigger an asynchronous full-site crawl.
- Agent default: pass only `url`.
- Scope: `crawler:read` for dry run, `crawler:crawl` for execution.
- Credits: dry run `0`; real crawl cost is estimated from `limit`.
- Default behavior: `dry_run=true`, no job is queued.
- Use when: user explicitly asks whether a whole site can be crawled or asks for
  full-site crawl cost. Do not call it before `query_warehouse`.

Example:

```json
{"url": "https://www.songmics.com/"}
```

## Usage Contract

Primary tools must return a `usage` object with these keys:

```json
{
  "credits_used": 0,
  "balance": 1999,
  "cache_hit": false,
  "source": "warehouse",
  "records": 5,
  "duration_ms": 12,
  "cost_if_retry": 3
}
```

Notes:

- `balance` may be `null` only when no API key context is available.
- `cache_hit=true` with `source="agent_memory"` means the result came from the
  5-minute cross-call memory and used `0` credits.
- Warehouse queries and warehouse hits use `0` credits.
- Live scrape failures should include `cost_if_retry` so agents can explain the
  cost of retrying advanced or fresh paths.

## Error Contract

Failures should include a `warnings` array. Each warning should include:

```json
{
  "code": "unsupported_url",
  "message": "The URL is not in the configured source list and live scrape failed.",
  "next_step": "Call query_warehouse for cached alternatives.",
  "cost_if_retry": 3
}
```

Scope failures return:

```json
{
  "success": false,
  "error": "insufficient_scope",
  "required_scope": "crawler:crawl",
  "granted_scopes": ["crawler:read", "crawler:scrape"]
}
```

## Compatibility Tools

- `[LEGACY] query_crawler_warehouse`: old warehouse query shape with
  `query/site/brand`.
- `[ADVANCED] map_site`: inspect known URLs before large crawl planning.
- `[ADVANCED] extract_structured_data`: schema extraction for multiple URLs.
- `[ADVANCED] get_crawl_job`: poll a crawl created by `crawl_site`.
- Other MCP tools for product intelligence, VOC, Amazon, Reddit, and influencer
  discovery remain available but are not the Agent-first public surface.
