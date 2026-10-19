"""Bundled pricing, user overrides, and per-model cost estimation."""

import json
from pathlib import Path

import pytest
from tokenhub.pricing import ModelPrice, PricingTable, ensure_pricing_file


def test_bundled_pricing_covers_known_models_and_estimates_cache() -> None:
    table = PricingTable.load()
    price = table.price_for("claude-sonnet-5")
    assert price.input == 2.0
    assert price.output == 10.0
    assert price.cache_read == 0.2
    assert price.cache_write == 2.5
    # 30 uncached input at $2/M, 70 cache reads at $0.20/M, 25 output at $10/M.
    assert table.estimate(
        "claude-sonnet-5", input_total_tokens=100, output_total_tokens=25, cache_read_tokens=70
    ) == 0.000324


def test_unpriced_and_unknown_models_never_become_zero() -> None:
    table = PricingTable.load()
    assert table.price_for("gpt-6-sol") is None
    assert table.estimate("gpt-6-sol", input_total_tokens=100, output_total_tokens=25) is None
    assert table.estimate(None, input_total_tokens=100, output_total_tokens=25) is None


def test_provider_prefixes_aliases_and_dated_suffixes_resolve() -> None:
    table = PricingTable.load()
    assert table.price_for("copilot/gpt-5") == table.price_for("gpt-5")
    assert table.price_for("openrouter/anthropic/claude-sonnet-5") == table.price_for("claude-sonnet-5")
    assert table.price_for("deepseek/deepseek-v4-flash") == table.price_for("deepseek-v4-flash")
    assert table.price_for("claude-haiku-4.5") == table.price_for("claude-haiku-4-5")
    assert table.price_for("claude-sonnet-5-20260101") == table.price_for("claude-sonnet-5")
    assert table.price_for("qwen3:latest") is None


def test_user_file_overrides_merge_with_defaults(tmp_path: Path) -> None:
    override = tmp_path / "pricing.json"
    override.write_text(json.dumps({
        "models": {
            "claude-sonnet-5": {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.0},
            "local-model": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        },
        "aliases": {"my-sonnet": "claude-sonnet-5"},
    }))
    table = PricingTable.load(override)
    assert table.price_for("claude-sonnet-5").input == 1.0
    assert table.price_for("my-sonnet").output == 5.0
    assert table.price_for("gpt-5") == PricingTable.load().price_for("gpt-5")
    assert table.estimate("local-model", input_total_tokens=1_000_000, output_total_tokens=0) == 0.0


def test_provider_qualified_override_only_changes_that_route(tmp_path: Path) -> None:
    override = tmp_path / "pricing.json"
    override.write_text(json.dumps({
        "models": {
            "openrouter/anthropic/claude-sonnet-5": {
                "input": 2.2, "output": 11.0, "cache_read": 0.22, "cache_write": 2.75,
            },
        },
    }))
    table = PricingTable.load(override)
    assert table.price_for("openrouter/anthropic/claude-sonnet-5").input == 2.2
    assert table.price_for("anthropic/claude-sonnet-5").input == 2.0
    assert table.price_for("claude-sonnet-5").input == 2.0


def test_invalid_user_file_keeps_bundled_defaults(tmp_path: Path) -> None:
    override = tmp_path / "pricing.json"
    override.write_text("{not json")
    table = PricingTable.load(override)
    assert table.price_for("gpt-5") == PricingTable.load().price_for("gpt-5")
    assert table.warnings


def test_ensure_pricing_file_is_editable_and_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "data" / "pricing.json"
    created = ensure_pricing_file(path)
    assert created == path
    document = json.loads(path.read_text())
    assert "claude-sonnet-5" in document["models"]
    path.write_text(json.dumps({"models": {}}))
    ensure_pricing_file(path)
    assert json.loads(path.read_text()) == {"models": {}}


def test_model_price_rejects_negative_defaults() -> None:
    from tokenhub.pricing import _parse_models

    models = _parse_models({"bad": {"input": -1, "output": 1, "cache_read": 0, "cache_write": 0}}, source="test")
    assert models == {}
    assert _parse_models({"ok": {"input": 1, "output": 2}}, source="test")["ok"] == ModelPrice(1, 2, 1, 1)


@pytest.mark.parametrize("missing", ["input", "output"])
def test_estimate_needs_at_least_one_known_side(missing: str) -> None:
    table = PricingTable.load()
    counters = {"input_total_tokens": 10, "output_total_tokens": 10}
    counters[f"{missing}_total_tokens"] = None
    assert table.estimate("gpt-5", **counters) is not None
