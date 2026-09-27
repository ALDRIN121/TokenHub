# TokenHub UI and UX review

Reviewed September 27, 2026. The documented foundation milestone is implemented
and passes its automated checks. The UI has been improved and exercised in a
browser against the real local API using an isolated synthetic provider home.

## What changed

- Put observed usage first, with a distinct workload total and separate cache
  and reasoning breakdowns. Added the previously omitted cache-write value.
- Added a composition chart only when complete, comparable observed totals
  support it. Unknown, zero, and incomplete totals have explicit explanations.
- Added sidebar section navigation, a keyboard skip link, visible focus,
  mobile navigation, a dark palette, and reduced-motion support.
- Made import-capable and detection-only providers distinct, moved evidence into
  expandable details, and added guidance for the first approval and rescan.
- Added a Refresh data action for retrying a failed request. Discovery errors
  are shown as detection failures rather than missing installations.
- Locked import, refresh, and rebuild controls during a pending operation.
  Rebuilding is available only after a supported source has been approved.
- Corrected the empty data-quality message so it does not claim unread data is
  healthy. Kept unknown values as em dashes and genuine zeros as zeros.

## Design references

[AI Agent Analytics Dashboard by Diana Larussa on Dribbble](https://dribbble.com/shots/26169161-AI-Agent-Analytics-Dashboard-Monitor-API-Calls-Token-Usage)
informed the usage-first hierarchy, compact navigation, separated metrics, and
chart/legend treatment. A [screenshot of the reference](dribbble-inspiration.png)
is saved for attribution and comparison; it is not a product asset.

The original [TokenHub visual reference](tokenhub-reference.png) was generated
with the built-in image-generation tool before implementation. The exact
[generation prompt](image-prompt.txt) is saved alongside it. Its illustrative
data and incidental labels are design material; the implemented dashboard
renders only actual API values and actions supported in this milestone.

The implemented palette uses cloud `#F5F7F9`, white `#FFFFFF`, ink `#172B32`,
teal `#16766B`, pale teal `#E8F4F0`, and slate `#60737D`. System sans-serif
typography keeps the interface local and avoids remote font requests.

## Verification

| Check | Result |
| --- | --- |
| Backend suite | 384 tests passed |
| Frontend suite | 17 tests passed |
| Integration suite, separately rerun | 5 passed, included in the backend count |
| Backend lint | Passed |
| Backend type check | Passed, 37 source files |
| Frontend TypeScript check and production build | Passed |
| Diff whitespace check | Passed |
| Browser approval → rescan → repeat rescan → rebuild | Passed |
| Browser retry after a simulated 503 | Recovered without a page reload |
| Layout at 320, 390, 768, 1024, and 1440 pixels | No horizontal page overflow |
| Keyboard skip link and section links | Worked |
| Provider detection details | Expanded and collapsed |
| Light palette visible text contrast spot check | No failing sampled text pairs |
| Final normal browser load | No console errors or warnings |

The browser import read one synthetic record, yielding 2,128,900 workload tokens
from 1,842,500 input and 286,400 output tokens. A repeat scan added zero events;
rebuilding retained those same totals. No real provider source was approved or
imported during this review.

Ten frontend regression tests were added for retry, empty quality, cache-write
display, approval/rebuild availability, overlapping actions, failed detection,
incomparable totals, chart proportions, imported zero totals, and missing counts.
The independent code review identified and verified the fixes for failed
detection and incomparable chart totals; no important finding remains.

The backend reports existing dependency deprecation warnings from
Starlette/HTTPX, AnyIO, and Alembic. These did not fail any check. No dependency
upgrade was required for the UI changes.

## Screenshots

| View | Before | After |
| --- | --- | --- |
| Desktop | [Original](screenshots/before-desktop.png) | [Viewport](screenshots/after-desktop.png), [full page](screenshots/after-desktop-full.png) |
| Mobile, 390px | [Original](screenshots/before-mobile.png) | [Viewport](screenshots/after-mobile-viewport.png), [full page](screenshots/after-mobile.png) |
| Tablet, 768px | — | [Screenshot](screenshots/after-tablet.png) |
| Dark theme | — | [Screenshot](screenshots/after-dark.png) |
| First run, before approval | — | [Screenshot](screenshots/after-first-run.png) |
| No provider/source data | — | [Screenshot](screenshots/after-empty.png) |
| Loading | — | [Screenshot](screenshots/after-loading.png) |
| API failure | — | [Screenshot](screenshots/after-error.png) |

All screenshot values are synthetic. First-run, empty, loading, and failure
views use controlled API responses in the browser; the populated screenshots
use the real backend and its database/import flow.

## Completion boundary

The completed foundation covers local discovery, explicit source approval,
Codex metadata import, incremental scans, deduplication, rebuilds, honest token
totals, data quality, and the local dashboard. Existing confidence-contract work
in the workspace is preserved.

Claude Code and Hermes usage imports, cost/pricing, per-model/session analytics,
and historical time-series views remain explicitly deferred in the product
specification. The composition bar visualizes current observed totals; it is
not a historical chart. These future features are not claimed as complete.
