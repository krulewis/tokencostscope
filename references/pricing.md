# Claude API Pricing Reference

last_updated: 2026-03-04
staleness_warning_days: 90

Prices are per 1,000,000 tokens (per-million). Verify at https://anthropic.com/pricing before relying on estimates.

## Models

### claude-sonnet-4-6 (default for most pipeline steps)
- input:       $3.00
- cache_read:  $0.30
- cache_write: $3.75
- output:      $15.00

### claude-opus-4-6 (Architect, Staff Review, L-size Implementation)
- input:       $5.00
- cache_read:  $0.50
- cache_write: $6.25
- output:      $25.00

### claude-haiku-4-5 (QA, mechanical tasks)
- input:       $1.00
- cache_read:  $0.10
- cache_write: $1.25
- output:      $5.00

## Pipeline Step → Model Mapping

| Pipeline Step         | Model   |
|-----------------------|---------|
| Research Agent        | Sonnet  |
| Architect Agent       | Opus    |
| Engineer Initial Plan | Sonnet  |
| Staff Review          | Opus    |
| Engineer Final Plan   | Sonnet  |
| Test Writing          | Sonnet  |
| Implementation        | Sonnet (Opus for L-size changes) |
| Playwright QA         | Haiku   |

## Cache Hit Rate Assumptions by Band

| Band        | Cache Hit Rate |
|-------------|----------------|
| Optimistic  | 60%            |
| Expected    | 50%            |
| Pessimistic | 30%            |

Note: Cache hit rate applies to input tokens only. Output tokens are never cached.
