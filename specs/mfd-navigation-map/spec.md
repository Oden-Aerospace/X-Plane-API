# Offline Navigation MFD

- **Status:** Approved
- **Owner:** Oden Aerospace
- **Created:** 2026-10-27
- **Related:** navigation-data discovery notes (development workspace),
  [instrument-only routes](../instrument-only-routes/spec.md)

## Problem

The harness displays flight instruments but has no geographic view of the
simulated flight. Developers need an MFD that relates live or recorded ownship
position to airports, runways, navigation features, and airspace without relying
on a hosted map service or presenting simulator data as operational navigation.

## Goals

- Add a locally served `/mfd` with an interactive aviation map.
- Use the same live/replay state selected through the root dashboard.
- Support locally imported aviation datasets with explicit provenance and currency.
- Make aviation-data providers pluggable and locally configurable, defaulting to
  local X-Plane data with an explicit public-dataset alternative.
- Keep geographic data independent of flight-state sampling and PFD rendering.

## Non-goals

- Certified navigation, Garmin compatibility, or proprietary artwork.
- Flight planning, direct-to commands, radio tuning, autopilot control, CDI,
  glidepath, flight-director bars, or new PFD navigation indications.
- Terrain, synthetic vision, weather, obstacles, traffic, NOTAMs, TFRs, real-time
  airspace activation, instrument procedures, engine or fuel pages.
- Background data downloads, paid subscriptions, required cloud accounts, or
  bundling third-party simulator navigation datasets.
- Guaranteeing that imported geography matches a simulator with unsupported
  data overrides. Unsupported overrides must be identified rather than ignored.

## Users and Scenarios

### View a simulated flight geographically

As a developer, I want to see ownship relative to airports and airspace while
the existing root dashboard controls the live or recorded source.

### Inspect nearby airport information

As a developer, I want to select an airport and inspect its runways and available
frequencies without commanding the simulated aircraft or its avionics.

### Review a recording offline

As a developer, I want the MFD to follow replay state using imported data, with
no network dependency and no changes to recorded timestamps or playback controls.

### Choose an aviation-data provider

As a developer, I want to configure local X-Plane or public aviation datasets
without changing flight-state acquisition, recordings, or MFD rendering.

## Requirements

### Functional

- **FR-1:** `GET /mfd` shall provide a dedicated MFD view with map-specific
  controls and information, but no source controls, instrument selector, or
  ADS-B panel. Root shall provide a link to this view and retain existing controls.
- **FR-2:** The MFD shall consume the same `/api/state` feed as the PFD and
  six-pack on the same origin. Opening, closing, and reloading `/mfd` shall not
  change the selected source or playback state.
- **FR-3:** The map shall initially use north-up orientation and center on the
  first valid position. It shall support drag/pan, zoom/range selection, an
  ownship-follow toggle, and a recenter command. Manual panning disables follow;
  recenter restores it. Track-up orientation is deferred.
- **FR-4:** Supported range choices shall be 2, 5, 10, 20, 50, and 100 NM,
  initially 20 NM. Range means center-to-top-edge ground distance at the current
  center latitude; include a labeled scale and north indicator. Geographic
  projection limits must be explicit, not treated as missing aircraft telemetry.
- **FR-5:** Valid latitude/longitude shall position ownship. Valid true heading
  shall orient its aircraft symbol; otherwise use a nondirectional position
  symbol. A valid derived ground track may be shown separately, never substituted
  silently for heading. Magnetic heading shall not orient a true-north map.
- **FR-6:** Missing, stale, or invalid position shall suppress current-ownship
  indication, follow updates, and nearest-airport calculations, with an explicit
  status. The map and imported features remain inspectable. Polling failure shall
  not leave previous ownship data appearing current. Recovery resumes follow only
  if follow was enabled before the interruption.
- **FR-7:** Provide separately switchable airport/runway, navaid, fix, and airspace
  layers. Initial enabled layers are airports/runways and airspace. Optional
  navaid/fix layers are off initially to reduce clutter. Unavailable layers show
  a status and are not represented as an empty but successfully loaded dataset.
- **FR-8:** Airport selection shall expose identifier, name, available elevation,
  runway identifiers, dimensions and surface, and available communication
  frequencies with units. Missing fields remain unavailable. Distinguish airport
  and runway identifiers instead of assuming every local identifier is ICAO.
- **FR-9:** A nearest-airport panel shall show up to 10 imported airports within
  100 NM of valid ownship, ordered by geodesic distance, with distance in NM and
  bearing in degrees true. Selection pans to the airport without controlling
  X-Plane. Missing source coverage is distinct from no airports within range.
- **FR-10:** Airspace selection shall expose available name, class/type, floor,
  ceiling, units, and vertical reference. Preserve surface/GND, AGL, MSL, flight
  level, and unlimited semantics. Do not infer active status or generate entry,
  terrain-clearance, or vertical-proximity alerts from static polygons.
- **FR-11:** Dataset configuration/import shall be explicit and local. Do not
  scan arbitrary drives or assume a hardcoded simulator path. Import/index data
  once rather than reparsing large files on every request. Replacing a dataset
  shall not disconnect a flight source; failed imports shall not partially replace
  the last successfully imported dataset.
- **FR-12:** Each layer shall expose provider, source/cycle or effective dates
  when known, geographic coverage when known, import time, license/attribution
  requirements, and errors. Unknown currency and old cycles must be explicit;
  a file build date is not its navigation cycle. Do not silently merge competing
  provider data or incompatible navigation cycles.
- **FR-13:** The default aviation-data provider shall be `xplane`, reading a
  user-configured X-Plane 12 installation without modifying simulator files.
  Support airport, navigation, fix, and OpenAIR datasets, documenting supported
  scenery/navdata priority and overrides. Detect and report unsupported ARINC/CIFP
  or other overrides affecting displayed features. A missing installation path
  shall leave aviation layers explicitly unavailable; do not scan drives, assume
  a path, or switch providers automatically. Local read access is not permission
  to integrate or redistribute a provider's data; adapters remain gated on
  applicable terms.
- **FR-14:** No imported aviation dataset shall be committed or bundled. Tests
  shall use small synthetic fixtures. A fresh installation without imported data
  shall still render the map controls and valid ownship with explicit layer
  unavailability, without fetching a placeholder online basemap.
- **FR-15:** A documented aviation-data provider interface shall separate
  import/validation, layer capabilities and metadata, bounded geographic queries,
  and airport detail lookup from the MFD renderer and flight-state source. A new
  provider shall be registerable through this interface without modifying the
  renderer or `FlightState`. Unsupported layers must be explicit, not represented
  as successful empty results. Provider selection shall not select or replace a
  live/replay flight-state source.
- **FR-16:** Local configuration shall select the active aviation-data provider
  by identifier and supply its dataset locations/options. Initial supported
  identifiers shall be `xplane` (default) and `public` (explicit opt-in). The public
  profile shall support configured OurAirports airport/runway/frequency/navaid
  files and FAA fix/airspace datasets. Document the accepted formats during
  planning. Public airport/navaid coverage and FAA US coverage are distinct and
  shall be reported per layer; do not claim worldwide fix or airspace coverage.
- **FR-17:** Both initial providers shall read explicitly supplied local datasets
  and remain usable offline after import. Choosing `public` does not authorize
  background network access or downloads. Public downloads and terms remain
  documented operator setup steps for this phase, not dependencies of map
  rendering. Missing optional files shall affect only their dependent layers.
- **FR-18:** Configuration validation shall identify unknown provider IDs,
  invalid options, inaccessible paths, unsupported formats, and incompatible
  cycles before activating a replacement dataset. Provider changes and imports
  shall not disconnect flight-state acquisition or reset replay. Activate a new
  provider's data atomically only after successful validation/import; on failure
  retain the previous usable dataset and visibly distinguish the requested
  provider/error from the provider actually serving features. With no previous
  dataset, report aviation layers unavailable while flight instruments remain
  functional. No silent fallback or cross-provider gap filling is permitted.

### Provider Selection

The provider choice applies to aviation geography, not live/replay telemetry.
One active profile supplies its documented layers; each layer retains its
underlying source identity even when a profile intentionally combines datasets.

| Profile | Selection | Inputs | Coverage policy |
| --- | --- | --- | --- |
| `xplane` | Default | Explicit simulator installation path and supported local overrides | Report installed dataset coverage/currency; do not imply current worldwide navigation data |
| `public` | Configured opt-in | Explicit local OurAirports files and FAA fix/airspace files | Report worldwide airport/navaid coverage separately from US FAA fix/airspace coverage |

The configuration-file format, import command/API, cache location, normalized
feature schema, and exact provider interface belong in the repository-grounded
implementation plan. No runtime third-party plugin installation or executable
plugin loading is required by this spec.

### Constraints

- **C-1:** Offline operation shall include locally packaged map-library assets,
  fonts, symbols, and map layers. No CDN, external tiles, or provider credentials
  are required at runtime. Select a proven map library during planning, subject
  to dependency/license review; do not hand-roll projection and map interaction.
- **C-2:** No flight-state/recording schema change is required for this phase.
  Geographic features shall have a separate query/import boundary with bounded
  responses and predictable decluttering at larger ranges.
- **C-3:** Existing loopback service behavior and instrument routes shall remain
  compatible. Do not restart active servers or replace their sources for testing
  without approval; use isolated test instances.
- **C-4:** Map controls shall have accessible labels, keyboard alternatives to
  pointer gestures, and readable status text. Verify desktop and 390 px portrait
  layouts without horizontal overflow or overlapping controls/information.
- **C-5:** Treat the display as simulation/development tooling. Imported datasets
  may be outdated, incomplete, or different from installed scenery. Airspace
  boundaries are not evidence of present-day flight permissions or activation.

## Acceptance Criteria

- **AC-1:** Given valid live or replay position, `/mfd` shows matching ownship and
  identical values for equivalent samples, without changing source/playback state.
- **AC-2:** Range, pan, follow, recenter, layer switches, and airport selection
  behave as specified; bearing/scale and map orientation use true-north semantics.
- **AC-3:** Given missing/stale/invalid position or API failure, current-looking
  ownship and nearest-airport results disappear, while map inspection remains
  available. Valid state recovery honors the previous follow setting.
- **AC-4:** Synthetic airport/runway, navaid/fix, and airspace fixtures display
  correctly, including airspace vertical-reference labels, absent optional fields,
  distance ordering, and duplicate identifiers with distinct source context.
- **AC-5:** Import failures preserve the previous usable dataset. Supported source
  priorities are tested, unsupported overrides are reported, dataset provenance
  is inspectable, and large source files are not reread for each map/state request.
- **AC-6:** With external network access disabled, imported layers and map controls
  remain usable. Without imports, valid ownship still renders and layer status
  explicitly reports unavailable data rather than fabricating features.
- **AC-7:** Unit/HTTP checks, map-library integration checks, existing regressions,
  and desktop/mobile browser screenshots pass. Include antimeridian handling,
  projection boundaries, unavailable heading, and missing/expired metadata cases.
- **AC-8:** Given no provider override, the system selects `xplane`; without its
  required path, layers report unavailable without filesystem discovery or public
  fallback. Explicit `public` configuration imports its supplied files, exposes
  per-layer coverage/provenance, and makes no external network requests.
- **AC-9:** Given equivalent normalized features from synthetic implementations
  of both providers, map rendering and airport details agree. A fixture provider
  can register through the documented interface without renderer changes.
  Unsupported optional layers have explicit capability/unavailability states.
- **AC-10:** Invalid provider configuration or failed replacement imports do not
  change the active dataset, aircraft source, or replay position. The requested
  provider/error and retained active provider are distinguishable; successful
  replacement switches all affected layer data/metadata together without silent
  cross-provider merging.

## Open Questions

- [x] Approve the proposed initial scope: north-up map, ownship, airport/runway
  information, optional navaids/fixes, and static airspace, with weather/terrain
  and PFD navigation guidance excluded.
- [x] Choose initial dataset policy. **Resolved (2026-10-27):** User requested
  pluggable, configurable data sources with local X-Plane as default and public
  datasets as a configurable option. Both provider profiles are in initial scope;
  local usage/redistribution terms still apply.
- [x] Coverage follows the configured provider and is reported per layer. Initial
  deterministic tests use synthetic US Mid-Atlantic and antimeridian features;
  this does not assert complete or current worldwide coverage.

## Approval

- **Approved by:** User ("proceed" after the provider-policy revision)
- **Date:** 2026-10-27
- **Notes:** Approval covers the offline map and both configurable providers.
  Dataset usage remains subject to applicable terms; no redistribution, weather,
  terrain, or PFD guidance is authorized.