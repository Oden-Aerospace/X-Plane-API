# Glass Cockpit Primary Flight Display

- **Status:** Approved
- **Owner:** Oden Aerospace
- **Created:** 2026-10-26
- **Related:** [Existing telemetry and harness spec](../xplane12-adsb-test-harness/spec.md)

## Problem

The local harness displays six separate instruments. Developers testing the
C172 G1000 need an integrated glass-cockpit presentation that makes attitude,
airspeed, altitude, heading, and vertical speed readable together from live
telemetry or recorded flight state.

## Goals

- Provide a G1000-inspired primary flight display (PFD) using the existing
  normalized state and live/replay pipeline.
- Preserve the six-pack as an alternative view.
- Keep missing, stale, and invalid telemetry explicit rather than presenting
  plausible-looking flight indications without valid data.

## Non-goals

- Reproducing Garmin branding, proprietary artwork, or a certified G1000.
- A multifunction display, moving map, terrain, synthetic vision, navigation
  database, flight planning, radio tuning, or engine instrumentation.
- Autopilot controls, flight-director commands, navigation deviation, selected
  altitude/heading bugs, or aircraft-specific airspeed limit bands without
  approved source data and mappings.
- Changes to telemetry acquisition, the recording schema, ADS-B encoding, or RF.

## Users and scenarios

### Monitor live flight

As a developer, I want an integrated PFD so I can compare the harness readings
with the C172 G1000 while X-Plane is running.

### Review a recording

As a developer, I want recorded state rendered through the same PFD so I can
inspect instrument behavior without a running simulator.

### Compare presentations

As a developer, I want to switch between Glass and Six-pack views without
disconnecting the source or restarting playback.

## Requirements

### Functional

- **FR-1:** The instrument area shall offer Glass and Six-pack views through an
  accessible segmented selector. Glass shall be the initial view on page load.
  Switching views shall not change the source, playback position, or state.
- **FR-2:** Glass shall present one integrated PFD with a large attitude horizon,
  pitch ladder, bank scale, and fixed aircraft reference. Positive pitch shall
  move the horizon downward; positive right bank shall rotate it counterclockwise.
- **FR-3:** The left tape shall show indicated airspeed in knots; the right tape
  shall show indicated barometric altitude in feet. Both shall include labeled
  moving graduations and a fixed numeric readout. Altitude shall not substitute
  geometric MSL elevation for the indicated altitude field.
- **FR-4:** A vertical-speed scale and signed feet-per-minute readout shall sit
  beside the altitude tape. A heading rose and numeric magnetic heading shall
  occupy the lower display. No course/deviation indication shall be fabricated.
- **FR-5:** Turn and slip indications shall use the existing instrument-deflection
  fields, not label them as measured angular turn rate. The barometer setting in
  inHg and groundspeed in knots shall be shown when valid.
- **FR-6:** Every indication shall respect its required fields' validity. Missing,
  stale, or invalid input shall suppress its active tape/needle/horizon motion,
  replace its numeric readout with dashes, and show a readable status flag. An
  attitude solution requires both valid pitch and roll. Unrelated valid
  indications shall continue updating. No source and polling failure shall not
  leave previous flight indications appearing current.
- **FR-7:** Existing live connection, sampling/staleness inputs, recording upload,
  play/pause/stop, unload, connection status, and ADS-B area shall remain available.
- **FR-8:** The PFD shall use a compact avionics layout with high-contrast markings,
  sky/earth horizon, white primary readings, and distinct caution/failure flags.
  It shall not display simulated navigation or mode annunciations as real data.

### Constraints

- **C-1:** Use existing normalized state fields and the local HTTP polling API;
  retain the dependency-free, locally served runtime and offline operation.
- **C-2:** Product edits belong in `X-Plane-API/`; working artifacts remain here.
- **C-3:** The display is simulation/development tooling, not certified avionics
  or an operational navigation aid. Preserve the existing safety notice.
- **C-4:** Maintain readable, nonoverlapping indications on desktop and mobile,
  including a 390 px portrait viewport. Use responsive layout rather than
  requiring horizontal page scrolling. Controls need accessible labels and
  keyboard operation, with status communicated through text as well as color.

## Acceptance criteria

- **AC-1:** Given a valid sample, Glass shows matching IAS, indicated altitude,
  magnetic heading, signed vertical speed, pitch, roll, turn/slip, barometer
  setting, and converted groundspeed with their specified units.
- **AC-2:** Given level, nose-up/down, and left/right-bank samples, horizon and
  pitch-ladder motion have the expected direction and remain clipped to the PFD.
  Heading wraparound from 359 to 0 degrees shall not cause a full-turn animation.
- **AC-3:** Given missing, stale, or invalid fields, only dependent indications
  show status flags and dashes. Unloading the source or losing API connectivity
  removes current-looking indications until usable state returns.
- **AC-4:** Given equivalent live and replay samples, both produce identical PFD
  values and validity states. Switching views preserves live or playback state.
- **AC-5:** At desktop and 390 px mobile widths, all primary readings and source
  controls are readable without overlap or horizontal page overflow. The view
  selector works by keyboard and communicates its selection accessibly.
- **AC-6:** Existing automated tests pass. Focused rendering checks cover valid,
  missing/stale/invalid, negative altitude/descent, heading wraparound, and
  attitude direction. Browser checks capture desktop/mobile screenshots and
  verify the display against deterministic sample states without X-Plane.

## Open questions

- [x] Approve the proposed PFD-first scope, with the six-pack retained and no
  navigation/MFD or autopilot emulation in this change.

## Approval

- **Approved by:** User (chat instruction: "proceed")
- **Date:** 2026-10-26
- **Notes:** Approved the proposed PFD scope and desktop/mobile verification.