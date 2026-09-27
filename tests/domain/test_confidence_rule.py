"""Edge cases for the evidence→confidence rule and its wire-stable enum.

These tests harden :func:`confidence_from_evidence` beyond the happy path pinned
in ``tests/domain/test_models.py``: every documented signal in isolation, every
container that can legally carry codes, unknown and near-miss spellings,
duplicates, ordering, and the string-enum contract that makes
``"high"``/``"medium"``/``"low"`` the only JSON values the wire can carry.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterable

import pytest
from tokenhub.domain.models import (
    EVIDENCE_SIGNAL_CODES,
    Confidence,
    confidence_from_evidence,
)

_SIGNAL_CODES: tuple[str, ...] = tuple(sorted(EVIDENCE_SIGNAL_CODES))


@pytest.mark.parametrize("code", _SIGNAL_CODES)
def test_every_documented_signal_alone_is_medium(code: str) -> None:
    """Fails if any documented signal is missing from the counting rule."""
    assert confidence_from_evidence((code,)) is Confidence.MEDIUM


@pytest.mark.parametrize("codes", list(itertools.combinations(_SIGNAL_CODES, 2)))
def test_any_two_documented_signals_are_high(codes: tuple[str, str]) -> None:
    """Fails if any pair of independent signals stays below high."""
    assert confidence_from_evidence(codes) is Confidence.HIGH


def test_all_documented_signals_together_are_high() -> None:
    """Fails if a full evidence set is somehow lower than a pair."""
    assert confidence_from_evidence(_SIGNAL_CODES) is Confidence.HIGH


@pytest.mark.parametrize(
    "codes",
    [
        pytest.param((), id="empty_tuple"),
        pytest.param([], id="empty_list"),
        pytest.param(set(), id="empty_set"),
        pytest.param(frozenset(), id="empty_frozenset"),
        pytest.param((code for code in ()), id="empty_generator"),
    ],
)
def test_empty_code_collections_are_low(codes: Iterable[str]) -> None:
    """Fails if an empty container behaves differently from an empty tuple."""
    assert confidence_from_evidence(codes) is Confidence.LOW


@pytest.mark.parametrize(
    "codes",
    [
        ("future_signal",),
        ("another_new_signal",),
        ("known_root_exist",),
        ("KNOWN_ROOT_EXISTS",),
        ("executable_on_paths",),
        ("future_signal", "another_new_signal"),
        ("future_signal", "another_new_signal", "third_signal"),
    ],
)
def test_unknown_codes_alone_stay_low(codes: tuple[str, ...]) -> None:
    """Fails if a code outside ``EVIDENCE_SIGNAL_CODES`` ever raises confidence."""
    assert confidence_from_evidence(codes) is Confidence.LOW


@pytest.mark.parametrize("known", _SIGNAL_CODES)
def test_one_known_code_beside_unknown_codes_stays_medium(known: str) -> None:
    """Fails if unknown codes are counted alongside the single real signal."""
    assert confidence_from_evidence(("future_signal", known, "another_new_signal")) is (
        Confidence.MEDIUM
    )


@pytest.mark.parametrize(
    "spelling",
    [
        "Known_Root_Exists",
        "known_root_exists ",
        " known_root_exists",
        "known-root-exists",
        "known_root_exists\n",
        "",
        " ",
        "known_root_exists\u200b",
    ],
)
def test_near_miss_spellings_never_count(spelling: str) -> None:
    """Fails if case, padding, or unicode lookalikes are treated as signals."""
    assert confidence_from_evidence((spelling,)) is Confidence.LOW


def test_duplicates_of_one_code_plus_a_distinct_code_are_high() -> None:
    """Fails if repetition is mistaken for an additional independent signal."""
    assert confidence_from_evidence(
        ("known_root_exists", "known_root_exists", "configuration_found")
    ) is Confidence.HIGH
    assert confidence_from_evidence(
        ("known_root_exists",) * 5 + ("configuration_found",) * 4
    ) is Confidence.HIGH


@pytest.mark.parametrize(
    "ordering",
    list(itertools.permutations(("known_root_exists", "configuration_found", "future_signal"))),
)
def test_result_is_order_independent(ordering: tuple[str, str, str]) -> None:
    """Fails if the rule depends on the order the connector recorded evidence."""
    assert confidence_from_evidence(ordering) is Confidence.HIGH


def test_result_is_idempotent() -> None:
    """Fails if the same evidence can produce two different levels."""
    codes = ("known_root_exists", "future_signal")
    first = confidence_from_evidence(codes)
    second = confidence_from_evidence(codes)
    assert first is second is Confidence.MEDIUM


def test_result_is_repeatable_for_equivalent_iterables() -> None:
    """A tuple, list, and set of the same codes agree."""
    assert confidence_from_evidence(("known_root_exists", "configuration_found")) is (
        confidence_from_evidence(["configuration_found", "known_root_exists"])
    ) is confidence_from_evidence({"known_root_exists", "configuration_found"}) is (
        Confidence.HIGH
    )


def test_a_bare_string_is_iterated_character_by_character() -> None:
    """Pins a latent footgun: ``str`` is an ``Iterable[str]`` of its characters.

    A caller that passes ``"known_root_exists"`` instead of
    ``("known_root_exists",)`` gets ``LOW`` rather than ``MEDIUM``. The current
    connectors always pass tuples, so this is characterization, not a defect.
    """
    assert confidence_from_evidence("known_root_exists") is Confidence.LOW


def test_confidence_enum_values_are_exactly_the_wire_strings() -> None:
    """Fails if the enum grows a level or renames one the wire depends on."""
    assert [member.value for member in Confidence] == ["high", "medium", "low"]
    assert {member.value for member in Confidence} == {"high", "medium", "low"}
    assert len(Confidence) == 3


def test_confidence_members_are_plain_strings_on_the_wire() -> None:
    """Fails if a member serializes as an enum repr instead of its value."""
    for member in Confidence:
        assert isinstance(member, str)
        assert member == member.value
        assert f"{member}" == member.value
        assert json.dumps(member) == f'"{member.value}"'
