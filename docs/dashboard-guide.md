# TokenHub dashboard guide

Applies to TokenHub 0.1.5 and later.

## Upgrade and open

```sh
python -m pip install --upgrade tokenhub
tokenhub stop
tokenhub start
```

Restart the existing local server after upgrading, then reload the dashboard. TokenHub retains its local database and previously approved sources. Python 3.12 or later is required. `tokenhub --version` prints the installed version.

## Find your usage

The first screen shows workload tokens, input, output and session activity. Use **Agent** and **Date range** to choose a scope, or click an agent in **Agent breakdown**. All five agents remain in the ranking, and the selected agent stays highlighted. Ranking amounts and shares always compare all agents within the selected date range; the summary, trend and analysis use your selected agent.

**All agents** resets the agent filter. The date range remains selected. Unknown or unavailable counters appear as **—**; a reported zero remains **0**. Hover a number to see its exact count.

## Move between sessions and models

Scroll a little below the overview to reach **Usage analysis**, or choose **Sessions** or **Models** in the header to scroll directly there. Both use the same workspace, and changing tabs retains each tab's search, sort and pagination. Sessions are selected initially.

The analysis context stays visible while you scroll. Its agent buttons switch scope directly. On a narrow screen the button strip and detailed token table scroll horizontally within their own regions. Choosing another agent or date resets the table contexts; clicking the already selected agent preserves them.

Click a model to view its session contributions. **All models**, the Models tab or the header Models link returns to the model listing. Expand a session to see its model counters. Returning to **Overview**, opening **Sources**, and browser Back preserve the selected agent. Agent, date and destination are also stored in the URL for reloads and bookmarks; search and pagination are kept within the current page session.

## Read trends and counters

Choose daily or weekly grouping and a metric in **Usage trends**. A selected date range compares with the preceding period of the same length. Calendar dates use your browser's time zone; weeks begin on Monday. An ongoing period includes only usage observed so far. **View chart data** reveals exact bucket counts. **Period breakdown and accuracy** contains the detailed comparisons and measurement notes.

**Counter details and accuracy** explains freshness and source coverage and exposes cache-read, cache-write and reasoning counts. The small **Coverage · review** indicator links to these details when coverage or freshness needs attention.

Workload tokens include records with complete input and output counters. Cache-read tokens are already included in input, and reasoning is already included in output. Do not add them to workload a second time. Cache-write counts remain a separate reported field; connector normalization determines their contribution to input. Incomplete records can contribute to an observed input or output subtotal without contributing to workload, so those subtotals may not reconcile when counters are missing. TokenHub does not estimate the missing values.

Hermes attributes session-reported usage to the session's end date, or start date while active. It cannot split that usage across individual request dates or model switches. Copilot counts saved Chat requests with token counters; inline suggestions are excluded. Antigravity counts supported saved conversation counters. Unrecorded usage is outside the totals.

## Sync and source issues

The header **Auto sync**, **Syncing**, or **Sync · review** control opens **Sync and coverage**. It contains import progress, the last completed scan, unreadable-source counts and a refresh action. A completed scan does not mean every source was readable. Partial source issues do not displace your imported usage.

Open **Sources** to approve or reconnect agents, inspect individual source health, change automatic collection, or rebuild the local index. New source access remains subject to approval. If the usage API or selected date range cannot load, the page shows a prominent retry message and leaves unavailable counts unknown.

## Theme and keyboard controls

The header **Dark mode** switch toggles light/dark appearance and saves the choice on this device. The initial theme follows the system preference if no choice is saved.

Models/Sessions tabs support Left/Right, Home and End. The sync drawer closes with Escape or Close and returns focus to its opener. Section navigation scrolls smoothly when motion is allowed; reduced-motion preferences disable smooth scrolling and animation.

## Privacy

TokenHub runs on loopback and stores usage metadata locally. Agent artwork is bundled with the package, so the dashboard does not request icons from third-party websites. It does not store or send prompts, transcripts, credentials or provider accounts.
