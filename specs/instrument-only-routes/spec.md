# Instrument-Only Display Routes

- **Status:** Approved
- **Owner:** Oden Aerospace
- **Created:** 2026-10-26
- **Related:** [Glass cockpit spec](../glass-cockpit/spec.md)

## Problem

Dedicated cockpit screens and embedded browser views need just their instrument
display, without the harness header, source controls, flight-data summary,
instrument selector, ADS-B placeholder, or footer.

## Goals

- Provide `/glass` for the integrated primary flight display and `/sixpack` for
  the six analog instruments.
- Reuse existing rendering and source state without duplicating connection or
  playback logic.

## Non-goals

- Implementing an MFD, map, aviation-data ingestion, or new telemetry fields.
- Auto-connecting X-Plane, selecting a recording, or modifying source settings
  when a display-only page opens.
- Adding independent sources per tab or sharing state across server processes.

## Users and scenarios

### Use a dedicated cockpit screen

As a developer, I want to open either display route on a dedicated screen while
controlling its live or replay source through the main harness.

## Requirements

### Functional

- **FR-1:** `GET /glass` shall serve a page showing only the existing glass PFD.
- **FR-2:** `GET /sixpack` shall serve a page showing only the six analog flight
  instruments, including their instrument names, units, and validity indications.
- **FR-3:** These pages shall omit the main header, source controls, flight-data
  summary, view selector, ADS-B panel, and footer. Instrument-specific labels and
  failure/status indications remain visible. Use the viewport responsively,
  without adding dashboard chrome or stretching circular instruments.
- **FR-4:** Both pages shall poll the existing `/api/state` endpoint on their own
  origin and render the source selected through `/` on that same server. Opening,
  reloading, or closing a display page shall not change source or playback state.
- **FR-5:** `/` and `/index.html` shall retain the complete harness and view
  selector. Existing APIs shall remain compatible.
- **FR-6:** With no usable state, or on polling failure, display-only pages shall
  not leave previous readings looking current. Preserve explicit unavailable,
  stale, and invalid indications; do not show demonstration values as telemetry.

### Constraints

- **C-1:** Retain the dependency-free local runtime and reuse existing rendering.
- **C-2:** Each server process has its own source. A source chosen on port 8766
  does not configure a display opened on port 8767.
- **C-3:** These are simulation/development displays, not certified instruments.
  Document that limitation in the README without adding non-instrument chrome
  to the dedicated pages.

## Acceptance criteria

- **AC-1:** Direct navigation and reload at `/glass` show the PFD and no six-pack
  or dashboard chrome; `/sixpack` shows six instruments and no PFD or chrome.
- **AC-2:** A source selected on `/` updates both dedicated pages on the same
  origin. Opening or reloading either page preserves source and replay position.
- **AC-3:** Missing, stale, invalid, and lost-connection scenarios do not present
  previous instrument values as current. Independent valid fields keep updating.
- **AC-4:** Desktop and 390 px mobile browser checks show readable instruments,
  intact circular dials, and no horizontal page overflow.
- **AC-5:** HTTP route tests, focused renderer tests, and existing tests pass.
  The root dashboard remains functional and unknown routes still return 404.

## Open questions

- [x] Approve the two routes and the same-server source-control behavior above.

## Approval

- **Approved by:** User (chat instruction: "approved")
- **Date:** 2026-10-26
- **Notes:** MFD/data research is separate from this bounded display-routing change.