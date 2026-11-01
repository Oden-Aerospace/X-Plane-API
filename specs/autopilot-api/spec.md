# Autopilot API

- **Status:** Approved
- **Owner:** Oden Aerospace
- **Created:** 2026-11-01
- **Related:** implementation plan and architecture notes (development
  workspace, `spec-workspace/features/autopilot-api/`),
  X-Plane 12 API architecture discovery (development workspace),
  [Glass cockpit spec](../glass-cockpit/spec.md)

## Problem

The package reads flight state from X-Plane 12 but cannot observe or operate
the simulated autopilot. Developers building avionics-style tooling need an
autopilot interface that mimics a modern small-aircraft AFCS (e.g., a
GFC 700-class system paired with a G1000): momentary mode-select button
inputs, target-value (bug/selector) inputs, and annunciated mode/engagement
outputs. Today there is no input path to X-Plane at all, and no normalized
autopilot state model.

## Goals

- Provide a Python API that models a modern small-aircraft autopilot
  mode controller: button-press inputs, target selectors, and annunciated
  output state.
- Provide a normalized, unit-bearing, per-field-status autopilot state record
  consistent with `FlightState` conventions.
- Establish the package's first write/input path to X-Plane 12, with explicit
  opt-in and verification semantics.
- Verify mode and target mappings live against X-Plane 12.4.3 with the
  included C172 G1000, matching how existing telemetry mappings were proven.
- Ship agent-friendly API documentation: a self-contained reference that a
  lightweight AI model can follow, without reading the source, to read
  autopilot state, send inputs, and validate results via the read-back
  contract.

## Non-goals

- Implementing a control-law/servo model, flight director guidance
  computation, or any autoflight logic outside X-Plane. X-Plane remains the
  autopilot; this package is the panel interface to it.
- Certified-avionics behavior claims. This is simulation/development tooling,
  not Garmin or any certified AFCS software.
- Autothrottle, trim servo control, yaw damper tuning, or envelope
  protection features not present in the target aircraft.
- Multi-aircraft/AI autopilot control.
- Changing the existing read-only telemetry pipeline semantics.
- Web-harness UI (AFCS annunciator bar or clickable AP control panel). v1 is
  library-only; harness integration is a follow-on feature with its own spec.
- An MCP server exposing the autopilot as agent tools. It is a planned
  follow-on feature with its own spec; v1 is designed MCP-ready (flat
  tool-shaped panel methods, string enums, JSON-serializable state) so the
  server can be a thin facade over `AutopilotPanel`.

## Users and scenarios

### Operate the autopilot like a mode controller

As a developer, I want to "press" AP, FD, HDG, NAV, APR, ALT, VS, and FLC and
turn the heading/altitude/VS/speed/course selectors programmatically, so my
tooling interacts with the autopilot the way a pilot interacts with the real
mode controller.

### Read annunciated autopilot state

As a developer, I want a normalized autopilot state snapshot — engagement
status, active and armed lateral/vertical modes, and target values — so I can
render an AFCS annunciator bar or drive test assertions, confirming mode
changes from annunciated state the way a pilot confirms them on the PFD.

### Script a flight procedure

As a test author, I want to command a heading and altitude capture sequence
and observe mode transitions (e.g., ALT armed → captured) so I can exercise
downstream displays and recordings against realistic autopilot behavior.

### Operate the autopilot from an AI agent

As an AI agent (potentially a lightweight model) given only the autopilot API
documentation, I want to read current state, choose and send the correct
input, and confirm the outcome from the next annunciated state, so automated
tooling can operate the autopilot reliably without inspecting package source
or guessing at semantics.

## Requirements

Requirements are drafted for discussion; mode names and mappings must be
verified against the target simulator before approval (see Open questions).

### Functional

- **FR-1:** The API shall expose an `AutopilotState` record with explicit
  unit-bearing fields and per-field status (valid/unavailable/stale/invalid),
  including at minimum: autopilot engagement, flight director status, active
  lateral mode, armed lateral mode, active vertical mode, armed vertical
  mode, selected heading (deg magnetic), selected altitude (ft), selected
  vertical speed (fpm), selected airspeed (kt), and selected course (deg).
- **FR-2:** The API shall expose momentary mode-select inputs modeled as
  button presses (not state writes) for the full GFC 700-style set: AP
  engage/disengage, FD, YD, HDG, ROL/wing-level, NAV, APR, BC, ALT hold, VS,
  FLC/speed mode, VNV, and GA/TOGA. Buttons whose function the target
  aircraft's AFCS does not support shall be documented as unsupported for
  that aircraft rather than silently absorbed (verified per C-1).
- **FR-3:** The API shall expose target-value inputs modeled as selector
  (knob/dial) settings for at least: heading bug, altitude selector,
  vertical-speed target, airspeed target, and course.
- **FR-4:** Input methods shall not fabricate resulting mode state. The
  authoritative result of any input is the subsequently read annunciated
  state from the simulator; the API shall make this read-back available and
  shall document that a press is a request, not a guaranteed transition.
- **FR-5:** Selector inputs shall validate ranges before transmission and
  reject out-of-range or non-finite values with explicit errors rather than
  silently clamping.
- **FR-6:** Autopilot state reads shall follow the same freshness semantics
  as telemetry: explicit subscription frequency, explicit staleness
  threshold, and stale/unavailable statuses when data stops arriving.
- **FR-7:** Input capability shall be explicitly opted into, separately from
  read-only telemetry, so existing read-only use cannot transmit control
  traffic unintentionally.
- **FR-8:** Disengagement shall be available as a dedicated input
  (AP disconnect) and shall not depend on toggling from an assumed current
  state.
- **FR-9:** The API shall provide an optional confirmation helper that polls
  annunciated state until a caller-supplied condition holds or an explicit
  timeout elapses, returning the final observed state either way. Inputs
  themselves remain fire-and-forget; the helper only reads.
- **FR-10:** The package shall ship agent-friendly API documentation: a
  single self-contained reference sufficient, without reading source code,
  to (a) connect and read `AutopilotState`, (b) send every supported button
  and selector input, and (c) validate outcomes via the read-back contract.
  It shall enumerate every input with its exact method signature, selector
  units and accepted ranges, every mode enum value with its meaning, every
  field of `AutopilotState` with units and status semantics, and the
  read → input → confirm loop with at least one complete runnable example
  per interaction kind (state read, button press, selector set, confirmed
  transition). It shall state explicitly that inputs are requests, that
  annunciated state is the only truth, and how errors, timeouts, and
  stale/unavailable statuses must be interpreted. Documented behavior shall
  be limited to live-verified mappings (C-1).

### Constraints

- **C-1:** Mode/command/dataref mappings must be verified live on
  X-Plane 12.4.3 with the included C172 G1000 before being documented as
  supported, matching existing repository practice. Unverified mappings are
  not shipped as working behavior.
- **C-2:** The write path targets a local or LAN simulator the user controls.
  No internet services. The web harness, if extended, remains local-only.
- **C-3:** Follow existing conventions: no silent defaults for frequency or
  staleness, dependency-free runtime unless a dependency is explicitly
  approved, unit-bearing names, frozen dataclass models.
- **C-4:** The X-Plane UDP protocol provides no acknowledgment for command
  or dataref-write packets. The API must not claim delivery guarantees it
  cannot provide; verification is by annunciated-state read-back.
- **C-5:** This is simulation tooling. Documentation must state it is not
  certified avionics and must not present X-Plane autopilot behavior as a
  model of any specific real AFCS product.
- **C-6:** The public API shall be MCP-ready so a follow-on MCP server can be
  a thin facade: panel inputs are flat, verb-like methods with
  JSON-compatible parameter types; mode and button identifiers are string
  enums; and `AutopilotState` is losslessly serializable to JSON, including
  per-field status.

## Acceptance criteria

- **AC-1:** Given a running XP12.4.3 C172 G1000 session with input enabled,
  when the API presses HDG with a selected heading bug, then a subsequent
  `AutopilotState` read reports heading mode active and the selected heading
  value, within an explicit confirmation timeout.
- **AC-2:** Given AP engaged in a vertical-speed climb with an altitude
  selected, when the aircraft approaches the selected altitude, then state
  reads show the armed altitude mode transitioning to active without any
  additional input.
- **AC-3:** Given a read-only client (input not opted in), when only
  telemetry APIs are used, then no `CMND`/`DREF` (or equivalent write)
  traffic is transmitted.
- **AC-4:** Given the simulator stops responding, when autopilot state is
  read after the staleness threshold, then fields report stale/unavailable
  rather than retaining last values as current, and inputs raise or report
  explicit errors rather than appearing to succeed.
- **AC-5:** Given an out-of-range selector value (e.g., heading 400°,
  non-finite altitude), when a selector input is called, then the API raises
  an explicit error and transmits nothing.
- **AC-6:** Given AP engaged, when AP disconnect is input, then a subsequent
  state read reports the autopilot disengaged.
- **AC-7:** Given only the agent-facing documentation (no package source),
  when its runnable examples are executed against a live XP12.4.3 C172 G1000
  session, then each example runs as written and produces the documented
  outcome; and every public input method, mode enum value, and
  `AutopilotState` field appears in the documentation (verifiable by an
  automated completeness check).

## Resolved decisions (user, 2026-11-01)

- **Input transport:** classic UDP `CMND`/`DREF` packets, extending the
  existing dependency-free UDP stack. Reads stay on RREF. The X-Plane 12.1+
  Web API remains a possible future alternate backend; the layering in the
  implementation plan keeps the panel interface transport-agnostic.
- **Input opt-in shape:** a separate write-capable client
  (`XPlaneControlClient`) composed with the read adapter, so read-only users
  structurally cannot emit control traffic (FR-7, AC-3).
- **Mode scope:** full GFC 700-style set including VNV, BC, GA/TOGA, and YD
  (FR-2), each still gated on live C172 G1000 verification (C-1).
- **Mode vocabulary:** generic AFCS mode enums (HDG, NAV, APR, ALT, ALTS,
  VS, FLC, …) are the public contract. Raw X-Plane status values are retained
  on the state record for diagnostics, and unmapped simulator states are
  represented as an explicit UNKNOWN — never guessed.
- **JSONL recording:** deferred. v1 autopilot state is live-only; recording
  AP state alongside telemetry is a follow-on feature.
- **Confirmation semantics:** fire-and-forget inputs plus an optional
  confirm-with-timeout read helper (FR-9).
- **Harness integration:** library-only in v1; harness UI is a follow-on
  feature (see Non-goals).

## Open questions

- [ ] **Live mode availability:** *(partially resolved by read-only probe,
      2026-11-01 — see the implementation plan's live probe findings).*
      The C172 G1000 verifiably drives the generic `sim/cockpit2/autopilot/*`
      state datarefs and all five selector dials; HDG/ALT/ALTS statuses were
      observed live. Still to verify in the write phase: button-command
      efficacy for each mode (notably VNV, BC, GA, YD — YD may not exist on
      this aircraft), and the 0/1/2 off/armed/active status convention across
      more transitions.

## Approval

- **Approved by:** curtisoden
- **Date:** 2026-11-01
- **Notes:** Approved after interactive resolution of transport, opt-in,
  mode-scope, vocabulary, confirmation, documentation (FR-10), and MCP-scope
  decisions; see the implementation plan discussion log. Write-phase live
  verification of button-command efficacy remains an implementation gate
  (C-1), not an open design question.
