# X-Plane-API

Python API and local test tooling for X-Plane 12 telemetry and offline ADS-B-style output.

## Development status

The package currently provides:

- X-Plane beacon discovery and a read-only UDP RREF client, including the `RREF,` response header observed in X-Plane 12.4.3.
- A normalized, unit-bearing `FlightState` record with per-field valid/unavailable/stale/invalid status.
- A configurable `GroundTrackDeriver` that derives course over ground from successive positions; callers must choose the minimum-movement threshold.
- A C172 G1000 XP12.4.3 dataref adapter, including the six-pack and aircraft metadata.
- Versioned JSON Lines state records and a playback cursor with play/pause/stop, including a real-time 1× method that honors sample timestamp intervals and can be interrupted by pause/stop.
- A local-only HTTP dashboard with live/file source controls, a G1000-inspired glass primary flight display, and an alternative six-pack view. The browser's 1090ES panel remains a placeholder; complete state-to-frame encoding and decoded diagnostics are not implemented.
- An autopilot API modeled on a modern small-aircraft AFCS mode controller: momentary button-press inputs, target-selector inputs, and a normalized `AutopilotState` read from annunciated simulator state. Input uses UDP `CMND`/`DREF` packets and is opted into by constructing `XPlaneControlClient`; read-only use cannot transmit control traffic. See [docs/autopilot-agent-api.md](docs/autopilot-agent-api.md).

The low-level ADS-B package also builds DF17/TC4 identification frames from a validated explicit address, explicit capability code, and callsign. Position, velocity, surface, and status encoding is not implemented; position encoding remains gated on the explicit test-only quality-profile decisions recorded in the working specification.

Live beacon discovery and mapped fields were exercised on X-Plane 12.4.3-r2 with the C172 G1000 on an Intel Mac. Windows and Apple Silicon are not yet validated. A local web harness is provided; complete 1090ES message encoding and output diagnostics remain unfinished.

## Live telemetry example

In X-Plane 12, enable **Accept incoming connections** in Network settings. Then:

```python
from xplane_api import RrefClient, XPlaneStateAdapter, discover_xplane

beacon = discover_xplane(timeout_seconds=3.0)
with RrefClient(beacon.host, beacon.port) as client:
    with XPlaneStateAdapter(client, frequency_hz=2) as source:
        state = source.read_state(
            timeout_seconds=1.5,
            stale_after_seconds=2.0,
        )
        print(state.latitude_deg, state.indicated_airspeed_kt)
```

The sample frequency and stale threshold are explicit; their production defaults have not been selected. `FlightState.timestamp_utc` is when the local adapter assembled the snapshot, not an X-Plane simulation timestamp. Each field's receive freshness is represented in `field_status`.

## Autopilot API

`AutopilotPanel` operates the simulated autopilot like its mode-controller
panel: `press_hdg()`-style momentary buttons, `set_altitude_selector_ft()`-style
target selectors with strict validated ranges, and `read_state()` returning a
normalized `AutopilotState` (engagement, active/armed lateral and vertical
modes, targets, per-field status). Inputs are transmitted as unacknowledged
UDP `CMND`/`DREF` packets: an input is a request, and the authoritative result
is the annunciated state read back afterwards (`wait_for(...)` helps poll for
an expected transition and reports timeouts instead of raising).

```python
from xplane_api import (
    AutopilotPanel, AutopilotStateAdapter, LateralMode,
    RrefClient, XPlaneControlClient, discover_xplane,
)

beacon = discover_xplane(timeout_seconds=3.0)
adapter = AutopilotStateAdapter(RrefClient(beacon.host, beacon.port), frequency_hz=5)
control = XPlaneControlClient(beacon.host, beacon.port)  # explicit input opt-in
with AutopilotPanel(adapter, control) as panel:
    panel.set_heading_bug_deg(270.0)
    panel.press_hdg()
    result = panel.wait_for(
        lambda s: s.lateral_mode_active is LateralMode.HDG,
        timeout_seconds=3.0, poll_interval_seconds=0.25, stale_after_seconds=2.0,
    )
    print(result.satisfied, result.state.lateral_mode_active)
```

Read-only telemetry never constructs `XPlaneControlClient`, so it structurally
cannot send control traffic. The complete agent-oriented reference — every
button, selector range, enum value, state field, and the read→input→confirm
contract — is in [docs/autopilot-agent-api.md](docs/autopilot-agent-api.md).
State mappings and input commands were verified live on XP12.4.3 with the
included C172 G1000 (course writes are GPS-slaved on that aircraft and
ineffective with a GPS HSI source; NAV/BC/VNV require a usable nav source or
VNAV plan to take effect). This is simulation tooling, not certified avionics
or a model of any specific real AFCS product.

For live control testing, a known-good baseline flight
(`tests/fixtures/autopilot-test-baseline.sit`: stable C172 G1000 cruise with
the autopilot engaged) can be copied into the simulator's
`Output/situations/` and loaded before a session. End every control-test
session by re-engaging and confirming the autopilot; the
`sim/operation/reset_flight` command recovers a lost flight.

### Autopilot MCP server

`xplane-api-mcp` exposes the autopilot panel to MCP agent runtimes over
stdio. Install the optional extra first: `python -m pip install -e ".[mcp]"`.
The server is **read-only by default** (`read_autopilot_state`,
`wait_for_state`); launching with `--allow-control` additionally registers
`press_button` and `set_selector` and constructs the write-capable client —
a read-only launch structurally cannot transmit control traffic. Connection
is explicit and fails fast at launch: give `--host`/`--port` or
`--discover-timeout-seconds` for beacon discovery, plus the required
`--frequency-hz` subscription rate.

```sh
xplane-api-mcp --discover-timeout-seconds 3 --frequency-hz 5                  # read-only
xplane-api-mcp --discover-timeout-seconds 3 --frequency-hz 5 --allow-control  # with control
```

Tool schemas carry the button/selector enums, units, and ranges; tool
descriptions embed the operating contract (inputs are requests, annunciated
state is truth), the verified C172 G1000 caveats, and the session-safety
rule. The server serves one local agent runtime and one simulator and is
simulation tooling, not certified avionics.

## JSON Lines and playback

Use `write_jsonl(stream, state)` to write one schema-versioned state record per line and `iter_jsonl(stream)` to read records. `JsonlPlayback` provides a deterministic pull cursor. `next_sample()` is non-blocking; `next_sample_realtime()` waits for the recorded intervals at 1× and responds to pause/stop from another thread. `GroundTrackDeriver(minimum_movement_m=...)` adds a `ground_track_deg` value after it has a valid position baseline and sufficient movement; the threshold is required because it depends on accepted GPS noise and downstream needs.

Autopilot snapshots can be stored in a separate JSONL sidecar with
`write_autopilot_jsonl(stream, state)` and read with `iter_autopilot_jsonl`.
The caller selects and performs fixed-rate sampling; each supplied snapshot
is written, including unchanged snapshots. A transition between samples can
be missed. `AutopilotJsonlPlayback.from_stream(stream)` replays that sidecar
alone. To replay it with telemetry:

```python
from xplane_api import PairedJsonlPlayback

with open("flight.jsonl", encoding="utf-8") as flight_file, \
        open("autopilot.jsonl", encoding="utf-8") as autopilot_file:
    playback = PairedJsonlPlayback.from_streams(flight_file, autopilot_file)
    playback.play()
    while (event := playback.next_sample_realtime()) is not None:
        print(event.kind, event.timestamp_utc, event.state)
```

The caller must pair files from the same session and ensure their timestamps
use compatible clocks. Playback emits records on one UTC timeline, with
telemetry before autopilot at equal timestamps; it does not invent states or
control the simulator. The library does not verify session identity or sample
automatically. The harness does not yet record or replay autopilot sidecars.

## Run the local harness

Run `PYTHONPATH=src python -m xplane_api.web` and open `http://127.0.0.1:8765/`. The server binds to loopback by default. Enable **Accept incoming connections** in X-Plane 12 Network settings before connecting. Choose a live frequency and stale timeout in the UI; there are no silent library defaults for either. After the first explicit connection, these settings are saved to `~/.config/xplane-api/live-source.json`. The server attempts one connection at startup, and the dashboard makes an ensure-connection request only when the source is idle or its prior live attempt failed. There is no periodic retry, and opening the dashboard does not replace active recording playback. Reconfigure by selecting the desired rate/timeout and connecting again. Use `--source-config PATH` to select a different settings file. If X-Plane is unavailable, the failure remains visible and the UI can be used to retry or reconfigure. For playback, load a schema-v1 JSONL recording and use Play/Pause/Stop; playback defaults to recorded timing at 1×. The 1090ES area is intentionally a placeholder until message/address/quality mappings are implemented.

The harness shows annunciated autopilot state on the Glass and Six-pack
views, and at `/autopilot`. It is read-only unless launched explicitly with
`--allow-autopilot-control`; that option enables the mode-controller controls
only while a live source is connected. Inputs are requests, and the displayed
result comes only from simulator read-back. During replay, select the
telemetry JSONL and its matching autopilot JSONL sidecar, then choose **Load
paired**. Telemetry-only v1 recordings remain supported, and playback never
enables controls. The harness does not verify that selected files belong to
the same session.

`sim/cockpit2/radios/actuators/flight_id` is the X-Plane Mode-S Flight ID/callsign source (the test C172 returned `N172SP`). `sim/aircraft/view/acf_ICAO` returns an aircraft type code such as `C172`; `sim/aircraft/view/acf_tailnum` also returned `N172SP`. None of these is the assigned Mode S 24-bit ICAO address, and the package does not treat them as that address.

### Instrument views

Glass is the initial instrument view. It combines attitude with a pitch ladder
and bank scale, indicated-airspeed and indicated-barometric-altitude tapes,
magnetic heading, vertical speed, groundspeed, barometer setting, and turn/slip
deflection. Airspeed and groundspeed are shown in knots, altitude in feet,
vertical speed in feet/minute, and barometer setting in inHg. The vertical-speed
pointer clips at +/-2,000 fpm; its numeric readout retains the actual value.

The Glass/Six-pack selector changes only presentation; live connection and
recording playback state are preserved. Missing, stale, and invalid glass-display
inputs show flags and dashes instead of active indications. Losing the local API
clears the display until data returns. No navigation, autopilot, flight director,
or aircraft performance-limit data is synthesized. This is simulation tooling,
not Garmin software or certified avionics.

### Dedicated instrument pages

- `/glass` shows only the glass primary flight display.
- `/sixpack` shows only the six analog instruments, labels, units, and statuses.
- `/` and `/index.html` retain the complete source/playback dashboard.

Select live telemetry or a recording through `/`, then open either instrument
page on the **same host and port**. These pages only read the shared state;
opening or reloading them does not connect, disconnect, or restart playback.
Each server process has its own source, so configuring one port does not
configure another. Dedicated pages intentionally have no source controls or
dashboard footer; they remain simulation/development displays, not certified
avionics. Missing or stale inputs and API failures suppress dependent instrument
indications instead of retaining previous readings as current data.

## Navigation MFD

Open `/mfd` on the same host and port as the source dashboard. The north-up map
offers range, pan, follow/recenter, layer selection, nearest-airport information,
and runway/airspace details. It shares live/replay state without changing the
source. True heading orients ownship; unavailable heading uses a nondirectional
symbol. Stale position, failed polling, and source errors suppress current-looking
ownship. Map data remains inspectable independently of telemetry.

Install the package dependencies first with `python -m pip install -e .`.
GeographicLib 2.1 (MIT) provides geodesic calculations. Leaflet 1.9.4 (BSD-2-Clause)
is packaged locally; no CDN, online tiles, or accounts are needed. The map uses
imported aviation features and a grid, not a downloaded basemap. Its latitude
limit is approximately 85.051 degrees north/south.

### Configurable aviation providers

Start the harness with a local JSON file:

```sh
PYTHONPATH=src python -m xplane_api.web --aviation-config /path/to/aviation.json
```

The provider defaults to `xplane`. No installation path is guessed or scanned;
without a configured dataset, ownship/instruments still work and layers report
unavailable data. Example local configuration:

```json
{
    "provider": "xplane",
    "xplane_path": "/path/to/X-Plane 12",
    "acknowledge_data_terms": true
}
```

Set `acknowledge_data_terms` only after confirming permitted use under applicable
dataset terms. Local readability does not establish redistribution rights.
Simulator files are read-only and are never bundled with the package.

For separately downloaded public data:

```json
{
    "provider": "public",
    "acknowledge_data_terms": true,
    "files": {
        "airports": "data/airports.csv",
        "runways": "data/runways.csv",
        "frequencies": "data/airport-frequencies.csv",
        "navaids": "data/navaids.csv",
        "fixes": "data/faa-fixes.geojson",
        "airspace": "data/faa-airspace.geojson"
    }
}
```

Paths are absolute or relative to the configuration file. Public `airports` is
required; other file entries are optional. Omit entries for files not supplied.
OurAirports uses its native CSV schemas. FAA fixes/airspace must be exported
WGS84 GeoJSON FeatureCollections (Point fixes, Polygon/MultiPolygon airspace),
not raw shapefile ZIPs or AIXM. No downloads happen automatically.

Edit the configured file and select **Reload data** in the MFD or **Reload
configured data** on the dashboard. Validation/import builds a new spatial index
before switching datasets. Failure retains the previous dataset and displays
requested versus active provider, without disconnecting telemetry or resetting
replay. Browser requests cannot name arbitrary file paths. No automatic fallback
or cross-provider gap filling occurs.

### Formats and limitations

- Local X-Plane: apt.dat 1200/1100 airports, runways/helipads, frequencies, native
    nav/fix formats, and OpenAIR points/circles/DA/DB arcs. Arc sampling is explicitly
    labeled. Airport datum is preferred; a runway-midpoint reference is labeled
    when datum is unavailable.
- Enabled scenery-pack order determines airport priority. A Custom Data nav/fix
    pair replaces defaults and must use coherent cycles. Unsupported global ARINC,
    FAA CIFP, and user nav/fix overrides are rejected rather than ignored. Custom
    CIFP procedures are not depicted and are disclosed.
- Provider metadata reports source, coverage, import time, cycle, effective date,
    and license. Optional public per-layer `metadata` may supply `cycle`,
    `effective_date`, `valid_until` (ISO dates), `coverage`, `license`, and
    `attribution`. Unknown dates remain unknown; expired source validity is flagged.
    Worldwide airport/navaid coverage is distinct from US FAA fix/airspace coverage.
- Queries use an in-memory SQLite spatial index; imports are not reread for each
    request. Feature responses are bounded and disclose truncation. Dateline
    polygon bounds are conservative and may return extra candidates.
- Weather, terrain, procedures, traffic, obstacles, live airspace activation,
    NOTAMs/TFRs, navigation guidance, and aircraft control are not provided. Static
    polygons do not establish flight permission. This is not certified avionics.

`AviationManager.register(identifier, importer)` adds a trusted Python provider
factory before reload. It receives local configuration plus `base_path` and
returns `AviationDataset(provider, features, layers)`. Normalized features have
a unique string `id`, `properties.layer`, and GeoJSON geometry; supported layers
are `airports`, `navaids`, `fixes` (points), and `airspace` (polygons). Layer
metadata comes from `layer_metadata`. Airport properties may include `runways`
and `frequencies`. Providers do not modify `FlightState` or renderers, and executable
plugin paths are not loaded from JSON.

The `/api/aviation` boundary provides `/status`, `/features?bounds=w,s,e,n&layers=...`,
`/nearest?lat=...&lon=...`, `/detail?id=...`, and `/range?lat=...&lon=...&nm=...`.
`POST /api/aviation/reload` accepts only `{}` and reads the configured file.

Only synthetic geography is included for tests. The configuration
`tests/fixtures/aviation/public.json` is a fictional preview, not real geography.

## Run the test suite

After installing dependencies, run `PYTHONPATH=src python -m unittest discover -s tests`.
In the development workspace, architecture discovery and working specifications
are maintained separately from this source repository.

With Node.js 18+ available, run `node --test tests/test_glass_cockpit.cjs` for
deterministic glass-display rendering checks. Node is only a development/test
tool, not a harness runtime dependency.

Run `node --test tests/test_mfd.cjs` for MFD state/projection regression checks.