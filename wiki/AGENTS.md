# Life OS LLM Wiki Schema

This wiki is maintained by the Life OS agent.

The user usually does not write the wiki directly. The agent updates it from raw sources, captures, research, conversations, and user corrections.

## Layers

1. raw/ = immutable source truth
2. wiki/ = compiled understanding
3. memory/review = candidate durable memories
4. memory/curated = accepted durable memories
5. OpenViking = indexed retrieval and deep recall

## Required page style

Every page should be concise and useful.

Use this structure when possible:

---
status: draft | active | superseded
last_updated: YYYY-MM-DD
confidence: low | medium | high
primary_sources:
  - path
---

# Title

## Current Understanding

## Evidence

## Open Questions

## Links

## Change Log

## Source Rules

- Never claim a life fact without a source.
- Prefer recent confirmed evidence over older inferred evidence.
- If a new source contradicts an old page, update wiki/contradictions.md.
- If a claim is stale, mark it as stale or superseded.
- If a fact is sensitive, summarize minimally and link to the local source path.

## File Naming

- Domain pages live in wiki/domains/
- People and recurring projects live in wiki/entities/
- Repeated behavior lives in wiki/patterns/
- Source summaries live in wiki/sources/
- Reports derived from wiki live in wiki/reports/

## Operations

### Ingest

When new raw sources arrive:
1. Read source.
2. Extract key facts, events, decisions, commitments, and patterns.
3. Update existing wiki pages before creating new pages.
4. Add cross-links.
5. Add source notes.
6. Update manifests/wiki-changelog.md.

### Query

When answering a Life OS question:
1. Read wiki/index.md.
2. Read relevant wiki pages.
3. Search OpenViking for supporting context.
4. Answer from compiled wiki first, then supporting sources.
5. If useful, file new synthesis back into the wiki.

### Lint

On weekly lint:
1. Find contradictions.
2. Find stale claims.
3. Find orphan pages.
4. Find missing pages.
5. Find vague claims with no source.
6. Create a wiki health report.
