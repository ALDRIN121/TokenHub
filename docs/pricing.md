# Pricing

TokenHub shows an estimated USD cost next to each model and in the usage
summary. Costs are estimates from token counters only: they exclude provider
fees, discounts, credits, taxes and any per-request charges.

## Where rates live

The first time TokenHub starts, it writes the bundled rates to:

```
~/.tokenhub/pricing.json
```

Edit that file to change a rate, add a negotiated or local model, or remove a
model's pricing. TokenHub reloads it automatically when the file changes.
Set `TOKENHUB_PRICING_FILE` to use a different path. `tokenhub pricing` prints
the active path and the number of priced models.

The bundled file is also shipped inside the package at
`tokenhub/data/default_pricing.json`; it is never modified by an upgrade once
the editable copy exists.

## Format

Rates are **USD per one million tokens**:

```json
{
  "version": 1,
  "currency": "USD",
  "unit": "per_million_tokens",
  "aliases": {
    "claude-haiku-4.5": "claude-haiku-4-5"
  },
  "models": {
    "claude-sonnet-5": {
      "input": 2.0,
      "output": 10.0,
      "cache_read": 0.2,
      "cache_write": 2.5
    }
  }
}
```

- `input` and `output` are required and must be non-negative numbers.
- `cache_read` and `cache_write` default to `input` when omitted.
- Keys are matched case-insensitively and against the last path segment, so
  `copilot/gpt-5` and `gpt-5` resolve to the same entry.
- Dated or tagged names such as `claude-sonnet-5-20260101` fall back to the
  base entry; the longest matching name wins.
- `aliases` map a recorded name to a priced one; they are merged over the
  bundled aliases.

## Custom providers and routers

Coding agents let you point at a different endpoint — OpenRouter, a company
gateway, LM Studio, or a local Ollama server. TokenHub matches the recorded
model name from the most specific path to the bare name:

```
openrouter/anthropic/claude-sonnet-5  →  anthropic/claude-sonnet-5  →  claude-sonnet-5
```

The bare entry is used unless you add a more specific one. That lets you
reprice just one route without changing every other agent:

```json
{
  "models": {
    "openrouter/anthropic/claude-sonnet-5": {
      "input": 2.2, "output": 11.0, "cache_read": 0.22, "cache_write": 2.75
    }
  }
}
```

- OpenRouter model IDs, provider-prefixed Hermes models
  (`deepseek/deepseek-v4-flash`), and date-stamped names resolve automatically.
- Local models can be set to `0` to record no API spend. If you leave them
  out they remain **Unpriced model**.
- If two gateways report the same bare model name, TokenHub cannot tell them
  apart; give the route a prefixed name in the gateway or use the same rate.
- Estimates cover token rates only. OpenRouter credit fees, gateway markups,
  subscriptions, and provider credits are not included; put the effective
  per-token rate in the override if you need it.

## How cost is calculated

Input counters include cached input, matching TokenHub's normalized schema.

```
uncached_input = max(input_total - cache_read - cache_write, 0)
cost = uncached_input × input
     + cache_read    × cache_read
     + cache_write   × cache_write
     + output        × output
```

Reasoning tokens are part of `output` and are not billed twice. Costs are
rounded to six decimal places per model group before they are summed.

Models without a rate, and records with no recorded model, show as
**Unpriced model**. Their tokens remain in the token totals but are excluded
from the cost estimate; the dashboard reports how many records are unpriced.

## Defaults

The bundled defaults cover the models TokenHub currently supports and observed
in local usage, converted from the LiteLLM `model_prices` snapshot
(2026-07-16, MIT) to per-million rates. `gpt-6-*`, `gemini-3.8-flash`,
`deepseek-v4.1-*`, `GLM-5.3`, `union-alpha` and other models without a
published rate are intentionally unpriced rather than guessed; add their rates
to your file when you know them.
