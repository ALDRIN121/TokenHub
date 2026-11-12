"""One discovery pass that answers three questions for every agent.

``DiscoveryEngine`` is the only place that knows the order of the three
questions:

* where an agent's data might be (:mod:`tokenhub.discovery.locations`),
* whether a location really is a data source (same module), and
* whether TokenHub has already imported it (:mod:`tokenhub.discovery.ledger`).

Connectors ask the engine for roots; the API asks it for a path-free report.
Both see the same answer for the same directory, so detection and import can
never disagree about which folder holds an agent's data.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tokenhub.discovery.ledger import ImportLedger
from tokenhub.discovery.locations import (
    AgentLayout,
    Location,
    LocationOrigin,
    LocationState,
    LocationVerdict,
    ProcessInfo,
    resolve_locations,
    validate_location,
)


@dataclass(frozen=True, slots=True)
class LocationAnswer:
    """The three answers for one candidate directory."""

    location: Location
    state: LocationState
    markers: tuple[str, ...] = ()
    imported: bool = False
    imported_events: int = 0

    @property
    def origin(self) -> LocationOrigin:
        return self.location.origin

    @property
    def evidence(self) -> str:
        return self.location.evidence


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    """Everything discovery knows about one agent in this pass."""

    agent_id: str
    display_name: str
    locations: tuple[LocationAnswer, ...] = ()

    @property
    def importable(self) -> tuple[LocationAnswer, ...]:
        """Locations that hold the agent's own layout and are not linked away."""
        return tuple(
            answer for answer in self.locations if answer.state is LocationState.VALID
        )

    @property
    def already_imported(self) -> tuple[LocationAnswer, ...]:
        return tuple(answer for answer in self.importable if answer.imported)

    @property
    def pending(self) -> tuple[LocationAnswer, ...]:
        """Valid locations TokenHub has not imported anything from yet."""
        return tuple(answer for answer in self.importable if not answer.imported)


@dataclass(frozen=True, slots=True)
class DiscoveryEngine:
    """Resolve layouts against one host snapshot, then answer per agent."""

    layouts: tuple[AgentLayout, ...]
    home: Path
    environment: Mapping[str, str]
    search_roots: tuple[Path, ...] = ()
    user_locations: Mapping[str, tuple[Path, ...]] = field(default_factory=dict)
    processes: tuple[ProcessInfo, ...] = ()
    installed_applications: tuple[str, ...] = ()
    platform: str = sys.platform

    @classmethod
    def for_layouts(
        cls,
        layouts: Iterable[AgentLayout],
        *,
        home: Path,
        environment: Mapping[str, str],
        search_roots: Sequence[Path] = (),
        user_locations: Mapping[str, tuple[Path, ...]] | None = None,
        processes: Sequence[ProcessInfo] = (),
        installed_applications: Sequence[str] = (),
        platform: str = sys.platform,
    ) -> DiscoveryEngine:
        return cls(
            layouts=tuple(layouts),
            home=home,
            environment=environment,
            search_roots=tuple(search_roots),
            user_locations=dict(user_locations or {}),
            processes=tuple(processes),
            installed_applications=tuple(installed_applications),
            platform=platform,
        )

    def _locations_for(self, layout: AgentLayout) -> tuple[Location, ...]:
        """Question one for one agent."""
        return resolve_locations(
            layout,
            self.home,
            self.environment,
            search_roots=self.search_roots,
            user_locations=self.user_locations.get(layout.agent_id, ()),
            processes=self.processes,
            installed_applications=self.installed_applications,
            platform=self.platform,
        )

    def _verdict_for(self, layout: AgentLayout, location: Location) -> LocationVerdict:
        """Question two for one candidate."""
        return validate_location(layout, location)

    def answers(self, ledger: ImportLedger | None = None) -> tuple[AgentAnswer, ...]:
        """Every agent, with the three answers resolved for each location."""
        ledger = ledger or ImportLedger()
        answers: list[AgentAnswer] = []
        for layout in self.layouts:
            entries: list[LocationAnswer] = []
            for location in self._locations_for(layout):
                verdict = self._verdict_for(layout, location)
                matches = ledger.for_location(layout.agent_id, location.path)
                entries.append(
                    LocationAnswer(
                        location=location,
                        state=verdict.state,
                        markers=verdict.markers,
                        imported=any(match.imported for match in matches),
                        imported_events=sum(match.events for match in matches),
                    )
                )
            answers.append(
                AgentAnswer(
                    agent_id=layout.agent_id,
                    display_name=layout.display_name or layout.agent_id,
                    locations=tuple(entries),
                )
            )
        return tuple(answers)
