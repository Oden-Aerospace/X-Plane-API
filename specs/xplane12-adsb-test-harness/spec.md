# X-Plane 12 1090ES API and Test Harness

- **Status:** Approved
- **Owner:** Oden Aerospace
- **Created:** 2026-10-11
- **Related:** architecture discovery and handoff notes (development workspace)

## Problem

We need a Python-facing way to obtain aircraft state from X-Plane 12 and exercise it through both live simulator data and recorded playback. The first validation setup is the X-Plane 12 Cessna 172 G1000, with intended platform coverage for Windows PC, Intel Mac, and Apple Silicon Mac. The project should transform simulator state into 1090 MHz Extended Squitter (1090ES)-style output even when the simulated aircraft does not have ADS-B avionics. A locally hosted web test harness should display a six-pack of flight instruments and let a user exercise the same state path using live data or a file.

The product source repository is `X-Plane-API/`; working specifications and discovery records live in this workspace. No implementation has been approved by this draft.

## Goals

- Define and implement a Python API boundary that provides normalized, timestamped aircraft state from either live X-Plane 12 telemetry or file playback.
- Validate the live source against the latest stable X-Plane 12 available during development and the included Cessna 172 G1000.
- Support and validate Windows PC, Intel macOS, and Apple Silicon macOS in the first milestone.
- Convert available simulator state into offline 1090ES output suitable for inspection and software-based testing, independently of whether the aircraft has ADS-B avionics. Emit raw 112-bit frames, an AVR text stream, and decoded JSON diagnostics.
- Provide a locally hosted test harness with a six-pack display and a way to choose live input or file playback.
- Keep data acquisition, normalized state, instrument display, 1090ES encoding, and output transport separable so they can be tested and changed independently.
- Preserve the architecture research and unresolved decisions for future contributors in the linked discovery note.

## Non-goals

- Supporting X-Plane 11.
- Requiring, reading, or emulating ADS-B avionics installed on the simulated aircraft.
- Receiving ADS-B signals from physical aircraft or external ADS-B receivers as an input requirement for this milestone.
- Transmitting radio-frequency ADS-B, controlling an SDR, or claiming that generated output is certified, suitable for operational aviation, or authorized for transmission.
- Writing a custom native X-Plane C/C++ plugin unless discovery and a separately approved plan show that supported external data access cannot meet requirements.
- Guaranteeing support for aircraft other than the included Cessna 172 G1000 in the first validation milestone.

## Users and scenarios

### View a live simulated flight

As a developer, I want to connect the Python API to a running X-Plane 12 Cessna 172 G1000 session so that I can inspect live state, see the six-pack update in a local browser, and verify generated 1090ES output.

### Replay recorded flight state

As a developer, I want to load a supported recording and replay it through the same normalized state and output pipeline so that I can test instrument and encoder behavior without running X-Plane.

### Validate generated frames

As a developer, I want to inspect generated 1090ES frames and their decoded field view, and compare them with known test vectors or an independent software decoder, so that encoding and state mapping errors are detectable.

### Handle unavailable or stale data

As a developer, I want unavailable, stale, or invalid simulator values to be represented explicitly so that the harness and encoder do not silently treat missing data as valid aircraft state.

## Requirements

### Functional

- **FR-1:** The system shall expose a Python API for timestamped normalized aircraft-state samples. The state schema, required fields, units, and validity model remain to be finalized.
- **FR-2:** The system shall support a live X-Plane 12 data source, initially investigated using native UDP/RREF. The selected live adapter shall be verified with the target simulator and Cessna 172 G1000 before it is considered supported.
- **FR-3:** The system shall support versioned JSON Lines recordings through the same normalized state interface as the live source, with play, pause, and stop controls. Playback defaults to recorded real-time intervals at 1× speed.
- **FR-4:** The system shall provide a locally hosted web harness that can select live data or a recording and display the six-pack: airspeed, attitude, altimeter, turn coordinator, heading indicator, and vertical speed indicator.
- **FR-5:** The system shall produce offline ADS-B Version 2 / DO-260B-style 1090ES messages for aircraft identification, position, airborne velocity, and surface position from available normalized state without requiring ADS-B equipment in the simulated aircraft. Airborne-position messages may use X-Plane MSL elevation as geometric height with TC20–22 only when the approved position-quality profile is configured; otherwise omit them.
- **FR-6:** The system shall expose generated 1090ES data as raw 112-bit frames, AVR text framing, and a decoded JSON diagnostic representation.
- **FR-7:** The aircraft identification/callsign shall use X-Plane's `sim/cockpit2/radios/actuators/flight_id` when available, or a user-configured identifier otherwise. The ICAO 24-bit address shall be explicitly configured; the system shall not infer it from the aircraft type code or tail number, or invent an address. Address-dependent frames shall not be emitted until a valid address is configured.
- **FR-8:** The system shall distinguish unavailable, stale, invalid, and valid state where needed; it shall not silently substitute guessed values for missing source data in generated messages.
- **FR-9:** The system shall derive ground track from successive valid latitude/longitude samples for airborne-velocity messages, and mark ground track unavailable when movement is below the configured 1 m minimum or the baseline is insufficient.
- **FR-10:** Position messages shall require an explicitly configured numeric, simulated, test-only position-quality profile; there is no default. The harness and diagnostics shall identify these values as configured test assumptions, not measured or certified integrity. Other unavailable integrity/quality values shall remain unknown where the profile permits; messages requiring unsupported values shall be omitted.
- **FR-11:** TC31 operational-status messages may be emitted only from an explicitly configured test profile; no operational-status values shall be invented by default.
- **FR-12:** The 24-bit Mode-S address shall be accepted from local configuration and may be overridden by a session-only local UI field. Both paths shall default empty and strictly validate the value; no address shall be inferred or synthesized.
- **FR-13:** For local round-trip testing of surface CPR, use the source position as the decode reference; this is only a test oracle and shall not be represented as receiver-side surface decoding without an external reference.
- **FR-14:** The system shall provide tests for parsing/normalizing state, real-time JSON Lines replay, instrument value mapping, and 1090ES encoding, including known vectors and independent-decoder checks appropriate to the selected format.
- **FR-15:** The architecture shall keep RF transmission disabled and outside this milestone. A later transmission capability requires its own approved requirements, interface, safety analysis, and applicable regulatory review.

### Constraints

- **C-1:** Target the latest stable X-Plane 12 release available during development; do not require an XP11-compatible implementation.
- **C-2:** The initial validation aircraft is the included X-Plane 12 Cessna 172 G1000.
- **C-3:** Windows PC, Intel macOS, and Apple Silicon macOS are required and must be validated in the first milestone. Linux is not currently in scope.
- **C-4:** ADS-B-like output must be generated from simulator state via the product API; it must not depend on aircraft ADS-B avionics.
- **C-5:** Keep working specs and discovery artifacts in the workspace; make product source changes only in `X-Plane-API/`.
- **C-6:** Do not assume a web framework, persistence format, or specific X-Plane integration library until repository inspection and a verified technical spike support the choice.

## Acceptance criteria

- **AC-1:** Given the target X-Plane 12 version and Cessna 172 G1000 are running with network data access configured, when the live adapter connects, then it provides timestamped samples for each required six-pack instrument value or explicitly reports which values are unavailable.
- **AC-2:** Given a valid versioned JSON Lines recording, when the user plays, pauses, or stops replay, then the same normalized sample interface emits states in recorded order, defaults to recorded real-time intervals at 1× speed, and honors those controls.
- **AC-3:** Given equivalent live-source and recorded samples, when they pass through the display model, then the harness presents the same instrument values, units, and validity indications.
- **AC-4:** Given normalized state, an explicitly configured valid 24-bit address, an explicitly configured test-only quality profile for position messages, and all other fields required for ADS-B Version 2 / DO-260B-style identification, airborne-position, airborne-velocity, or surface-position messages, when the encoder processes it, then it emits corresponding raw 112-bit frame(s), AVR text, and decoded JSON with fields matching the approved mapping and passing known-vector and independent-decoder checks. TC31 is omitted.
- **AC-5:** Given required source fields are absent, invalid, or stale, when the encoder processes the sample, then it follows a documented policy (such as omitting the message or marking output invalid) and does not encode fabricated values as valid data.
- **AC-6:** Given the harness is running locally, when the user selects a supported live or file source, then six-pack state and generated-output diagnostics are visible without requiring an external hosted service.
- **AC-7:** Given any configuration for the initial milestone, the software does not transmit 1090 MHz RF or invoke a radio/SDR transmitter.
- **AC-8:** Windows PC, Intel macOS, and Apple Silicon macOS are each tested and documented against the target X-Plane 12 build before the first milestone is considered complete.

## Open questions

### Scope and compatibility

- [ ] Does “latest X-Plane 12” mean latest stable release (excluding beta) at test/release time? Should a minimum supported XP12 version be defined?
- [ ] Is the Cessna 172 G1000 only the initial validation target, or must the initial release also operate with any other aircraft?

### State and instruments

- [ ] What are the required canonical state fields, units, precision, and sampling/update rate, including the exact X-Plane identifier field/dataref to use when available?
- [ ] Should the six-pack resemble conventional analog instruments, include digital readouts, or both? Which unit preferences are required?
- [ ] Should displayed instrument values come from aircraft-specific instrument datarefs when present, standardized simulator state, or a documented combination?
- [ ] What stale-data timeout, reconnect behavior, and connection status must users see?

### Recording and live connection

- [ ] Is external UDP/RREF acceptable as the first live adapter, subject to a real XP12/C172 validation spike? If not, what installation or data-access constraint rules it out?
- [ ] What is the required local web update rate and acceptable latency? Are HTTP polling, Server-Sent Events, or WebSockets preferred or constrained?

### 1090ES output

- [ ] Which specific fields of each selected 1090ES message type must be supported, and what are the source mappings and update cadence?
- [x] Which X-Plane API/dataref provides the aircraft identifier/callsign? **Resolved:** `sim/cockpit2/radios/actuators/flight_id` is the Mode-S Flight ID and returned `N172SP` in the tested C172 session. If unavailable, use the user-configured identifier.
- [x] Where should the 24-bit address be configured? **Resolved:** Support both local config and a session-only UI override; both default empty and validate strictly. Collision-free assignment/ownership remains an operator responsibility.
- [ ] Which intended software consumer or independent decoder should validate the raw-frame and AVR outputs?
- [x] Which position altitude should airborne messages encode? **Resolved:** Encode X-Plane MSL elevation as geometric height using TC20–22; do not silently convert the pilot altimeter indication to pressure altitude.
- [x] How should position-message integrity/accuracy be handled? **Resolved:** Require an explicitly configured test-only quality profile and label it synthetic/non-operational. Other unavailable integrity fields stay unknown when allowed; otherwise omit the message.
- [x] How is the quality profile supplied? **Resolved:** Require explicit numeric values with no defaults; omit gated position frames until configured.
- [x] Should the encoder emit TC31 operational-status frames? **Resolved:** Allow TC31 only when values are supplied by an explicit test profile; do not invent defaults.
- [x] What surface CPR reference should the local harness use? **Resolved:** Require a configured nearby airport/reference coordinate; do not use the encoded source position as its own decode reference.
- [ ] Which simulator fields map to velocity, on-ground status, and remaining integrity/quality fields? What should the encoder do when the simulator cannot provide defensible values?
- [x] What ADS-B version/profile is intended? **Resolved:** ADS-B Version 2 / DO-260B-style 1090ES output for offline testing.
- [x] How should unavailable integrity/quality values be treated? **Resolved:** Mark unknown where the profile permits; omit messages that require unavailable fields. Never fabricate integrity assurances.
- [x] How should ground track be sourced? **Resolved:** Derive it from successive valid latitude/longitude samples; mark unavailable if the baseline or movement is insufficient.
- [x] What minimum ground-track movement threshold should be used? **Resolved:** 1 m; below this, track is unavailable.
- [x] What should replay timing default to? **Resolved:** Honor recorded intervals at real-time 1×; the web harness may later expose playback controls.
- [x] Is pyModeS acceptable as an independent decoder? **Resolved:** Yes, optionally for tests only; it is GPL-3.0 and requires Python 3.11+, so keep it a separately installed test oracle, not a runtime dependency or bundled product component.
- [ ] Future transmission is outside the initial milestone. What target/interface (software network feed, another simulator, SDR, or RF equipment) should a later spec consider?

### Delivery and maintenance

- [ ] Is the Python API primarily an importable library, a local application service, or both?
- [ ] How should users install/run the harness and configure the X-Plane data source?
- [ ] Are there licensing or redistribution constraints for XPPython3, XPlaneConnect, message libraries, or decoder/test tools?

## Approval

- **Approved by:** User (chat instruction: “proceed” after being asked whether to approve this draft as the basis for an implementation plan)
- **Date:** 2026-10-11
- **Notes:** The user authorized implementation within the approved scope on 2026-10-11. Subsequent decisions selected offline ADS-B Version 2 / DO-260B-style frames, MSL elevation as geometric height via TC20–22, explicit test-only quality values (no defaults), unknown/omitted unsupported integrity, omit TC31 unless explicitly configured, 1 m derived ground-track threshold, real-time 1× replay, empty-by-default 24-bit address config with session override, surface CPR airport/reference validation, and optional test-only pyModeS. RF transmission remains excluded. The initial DF17 identification frame primitive is implemented and verified against a published vector and pyModeS. Position/surface/TC31 numeric-profile interactions remain gated by the open question in the workspace task record.
