"""Editable per-model token pricing and estimated usage cost."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

DEFAULT_PRICING_FILE = "pricing.json"
_MILLION = 1_000_000.0


class PricingError(ValueError):
    """The pricing document cannot be used."""


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD rates for one million tokens in each billing category."""

    input: float
    output: float
    cache_read: float
    cache_write: float


@dataclass(slots=True)
class PricingTable:
    """Bundled defaults with optional user overrides, matched by model name."""

    models: dict[str, ModelPrice]
    aliases: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, override_path: Path | None = None) -> PricingTable:
        """Load bundled defaults, then merge a user file without mutating it."""
        document = _read_document(_default_path())
        table = cls(
            models=_parse_models(document.get("models"), source="bundled pricing"),
            aliases=_parse_aliases(document.get("aliases")),
            metadata={key: document[key] for key in ("currency", "unit", "updated", "source") if key in document},
        )
        if override_path is not None and override_path.exists():
            try:
                override = _read_document(override_path)
            except (OSError, PricingError) as error:
                table.warnings.append(f"ignored {override_path}: {error}")
            else:
                try:
                    if "models" in override:
                        table.models.update(_parse_models(override.get("models"), source=str(override_path)))
                    table.aliases.update(_parse_aliases(override.get("aliases")))
                except PricingError as error:
                    table.warnings.append(f"ignored {override_path}: {error}")
        for warning in table.warnings:
            LOGGER.warning("TokenHub pricing: %s", warning)
        return table

    def price_for(self, model_name: str | None) -> ModelPrice | None:
        """Resolve a recorded model name, including provider prefixes and suffixes.

        ``openrouter/anthropic/claude-sonnet-5`` falls back to ``claude-sonnet-5``
        unless a more specific key exists, so aggregators and custom gateways
        can override only the routes they reprice.
        """
        if not model_name:
            return None
        name = _normalize(model_name)
        candidates = _suffixes(name)
        for candidate in candidates:
            for option in (candidate, candidate.replace(".", "-")):
                if option in self.models:
                    return self.models[option]
                target = self.aliases.get(option)
                if target:
                    if target in self.models:
                        return self.models[target]
                    for fallback in _suffixes(target):
                        if fallback in self.models:
                            return self.models[fallback]
        # Date-stamped or tagged variants ("-20260101", ":latest") use the base
        # entry. Longest first keeps "gpt-5.6-sol" ahead of "gpt-5.6".
        for candidate in candidates:
            for key in sorted(self.models, key=len, reverse=True):
                if candidate.startswith((key + "-", key + ":")):
                    return self.models[key]
        return None

    def estimate(
        self,
        model_name: str | None,
        *,
        input_total_tokens: int | None,
        output_total_tokens: int | None,
        cache_read_tokens: int | None = None,
        cache_write_tokens: int | None = None,
    ) -> float | None:
        """USD estimate for observed counters, or None when the model is unpriced.

        Input is inclusive of cache counters, matching the normalized schema.
        Reasoning is already part of output and is not billed twice.
        """
        price = self.price_for(model_name)
        if price is None:
            return None
        cost = 0.0
        known = False
        if input_total_tokens is not None:
            read = cache_read_tokens or 0
            write = cache_write_tokens or 0
            uncached = max(input_total_tokens - read - write, 0)
            cost += uncached * price.input
            cost += read * price.cache_read
            cost += write * price.cache_write
            known = True
        if output_total_tokens is not None:
            cost += output_total_tokens * price.output
            known = True
        return round(cost / _MILLION, 6) if known else None

    def payload(self) -> dict[str, Any]:
        """Public, read-only view of the rates TokenHub is currently using."""
        return {
            **self.metadata,
            "aliases": dict(self.aliases),
            "models": {
                name: {
                    "input": price.input,
                    "output": price.output,
                    "cache_read": price.cache_read,
                    "cache_write": price.cache_write,
                }
                for name, price in sorted(self.models.items())
            },
        }


def pricing_file(data_directory: Path) -> Path:
    """The user-editable pricing file under the TokenHub data directory."""
    return Path(data_directory) / DEFAULT_PRICING_FILE


def ensure_pricing_file(path: Path) -> Path:
    """Create the editable pricing file from bundled defaults when absent."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(_default_path().read_text(encoding="utf-8"), encoding="utf-8")
        except FileExistsError:
            pass
    return path


def _default_path() -> Any:
    return files("tokenhub").joinpath("data").joinpath("default_pricing.json")


def _read_document(path: Any) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PricingError(f"invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise PricingError("pricing document must be a JSON object")
    return document


def price_row(table: PricingTable, row: dict[str, Any]) -> tuple[float | None, int]:
    """Cost and unpriced event count for one model-grouped summary row."""
    cost = table.estimate(
        row.get("model_name"),
        input_total_tokens=row.get("input_total_tokens"),
        output_total_tokens=row.get("output_total_tokens"),
        cache_read_tokens=row.get("cache_read_tokens"),
        cache_write_tokens=row.get("cache_write_tokens"),
    )
    return cost, 0 if cost is not None else int(row.get("event_count") or 0)


def aggregate_costs(rows: list[dict[str, Any]]) -> tuple[float | None, int]:
    """Sum priced model rows; events from unpriced models stay counted."""
    costs = [row["estimated_cost_usd"] for row in rows if row.get("estimated_cost_usd") is not None]
    unpriced = sum(int(row.get("unpriced_event_count") or 0) for row in rows)
    return (round(sum(costs), 6) if costs else None), unpriced


def _parse_models(value: Any, *, source: str) -> dict[str, ModelPrice]:
    if not isinstance(value, dict):
        raise PricingError(f"{source} has no models object")
    parsed: dict[str, ModelPrice] = {}
    for raw_name, raw_price in value.items():
        name = _normalize(raw_name)
        if not isinstance(raw_price, dict):
            continue
        required: dict[str, float] = {}
        for key in ("input", "output"):
            rate = _rate(raw_price.get(key))
            if rate is None:
                break
            required[key] = rate
        else:
            cache_read = _rate(raw_price.get("cache_read"))
            cache_write = _rate(raw_price.get("cache_write"))
            parsed[name] = ModelPrice(
                input=required["input"],
                output=required["output"],
                cache_read=required["input"] if cache_read is None else cache_read,
                cache_write=required["input"] if cache_write is None else cache_write,
            )
    return parsed


def _rate(value: Any) -> float | None:
    """A non-negative finite rate, or None when absent/invalid."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _parse_aliases(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        _normalize(alias): _normalize(target)
        for alias, target in value.items()
        if isinstance(alias, str) and isinstance(target, str)
    }


def _normalize(model_name: str) -> str:
    return model_name.strip().casefold()


def _suffixes(name: str) -> list[str]:
    """Full provider-qualified name, then progressively shorter path suffixes."""
    parts = name.split("/")
    return ["/".join(parts[index:]) for index in range(len(parts))]
