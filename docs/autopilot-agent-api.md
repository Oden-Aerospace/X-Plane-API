# Autopilot Agent API Reference

Self-contained reference for operating the simulated X-Plane 12 autopilot
through `xplane_api`. It is written so an AI agent can read state, send
inputs, and validate results without reading package source code.

Agents running in MCP tool runtimes can use the `xplane-api-mcp` server
instead (optional `mcp` extra; read-only by default, control via
`--allow-control`): its four tools mirror this reference and carry the same
contract in their schemas and descriptions. The rest of this document is for
agents calling the Python API directly.

Verified against X-Plane 12.4.3 with the included Cessna 172 G1000. This is
simulation tooling, not certified avionics; it does not model any specific
real AFCS product.

## The one rule: inputs are requests, annunciated state is truth

The autopilot logic runs inside X-Plane. Buttons and selectors only *request*
changes over unacknowledged UDP; the simulator decides the resulting modes.
Pressing a button returns no result, exactly like a real mode-controller
button. **After every input, read state back and check it.** Never assume an
input succeeded. The confirm loop is always:

1. Read state (optional, to know the starting point).
2. Send one input.
3. Read state again (or use `wait_for`) and compare against what you expect.
4. If the expected annunciation did not appear before your timeout, the
   request did not take effect — report that; do not retry blindly.

## Connect

```python
from xplane_api import (
    AutopilotPanel,
    AutopilotStateAdapter,
    RrefClient,
    XPlaneControlClient,
    discover_xplane,
)

beacon = discover_xplane(timeout_seconds=3.0)

reader = RrefClient(beacon.host, beacon.port)
adapter = AutopilotStateAdapter(reader, frequency_hz=5)

# Constructing XPlaneControlClient is the explicit opt-in to sending
# control traffic. Omit it (and the panel) for read-only use.
control = XPlaneControlClient(beacon.host, beacon.port)

panel = AutopilotPanel(adapter, control)
```

`frequency_hz` is how often the simulator streams state updates. There are
no default frequencies or timeouts anywhere: every read names its own
`timeout_seconds` (how long this call may wait) and `stale_after_seconds`
(how old a value may be and still count as current).

Close with `panel.close()` (closes both the adapter and the control client)
or use `with AutopilotPanel(adapter, control) as panel:`.

## Read current state

```python
state = panel.read_state(timeout_seconds=1.0, stale_after_seconds=2.0)

print(state.ap_engaged)             # True / False / None
print(state.lateral_mode_active)    # LateralMode.HDG, ...
print(state.vertical_mode_armed)    # VerticalMode.ALTS, ...
print(state.selected_altitude_ft)   # 2000.0
print(state.is_usable("lateral_mode_active"))  # status check
print(state.to_json_dict())         # full JSON-compatible snapshot
```

### `AutopilotState` fields

Every field is `None` when unavailable. Check `field_status[name]` (or
`is_usable(name)`) before trusting any value.

| Field | Type | Units / values | Meaning |
| --- | --- | --- | --- |
| `timestamp_utc` | datetime | UTC | When this snapshot was assembled locally |
| `ap_engaged` | bool | — | Autopilot servos engaged |
| `flight_director_on` | bool | — | Flight director on (with or without servos) |
| `yaw_damper_on` | bool | — | Yaw damper on |
| `ap_disconnect_annunciated` | bool | — | Disconnect annunciation active |
| `lateral_mode_active` | LateralMode | enum below | Active (captured) lateral mode |
| `lateral_mode_armed` | LateralMode | enum below | Armed lateral mode |
| `vertical_mode_active` | VerticalMode | enum below | Active (captured) vertical mode |
| `vertical_mode_armed` | VerticalMode | enum below | Armed vertical mode (highest-precedence when several are armed; see `raw_status_values`) |
| `selected_heading_deg_mag` | float | degrees magnetic | Heading bug |
| `selected_altitude_ft` | float | feet | Altitude selector |
| `selected_vertical_speed_fpm` | float | feet/minute | Vertical-speed target |
| `selected_airspeed_kt` | float | knots | Airspeed target |
| `selected_course_deg_mag` | float | degrees magnetic | Selected course (CRS/OBS) |
| `fd_pitch_command_deg` | float | degrees | Flight-director pitch command bar |
| `fd_roll_command_deg` | float | degrees | Flight-director roll command bar |
| `raw_status_values` | mapping | dataref → float | Raw simulator mode statuses (diagnostics) |
| `field_status` | mapping | field → status | Per-field usability, below |

`AutopilotState` also provides `is_usable(field_name)`, `to_json_dict()`,
and `AutopilotState.from_json_dict(payload)`.

### Field status values (`FieldStatus`)

| Status | Meaning | What you must do |
| --- | --- | --- |
| `valid` | Fresh, usable value | Use it |
| `unavailable` | No data received for this field | Treat the value as absent; do not substitute defaults |
| `stale` | Data stopped arriving within `stale_after_seconds` | Do not treat as current; the sim may be paused, stopped, or unreachable |
| `invalid` | Data arrived but is unusable (e.g., non-finite) | Discard; report if it persists |

### Mode enums

`LateralMode`: `NONE`, `ROL`, `HDG`, `NAV`, `GPSS`, `APR`, `BC`, `GA`,
`UNKNOWN`.

`VerticalMode`: `NONE`, `PIT`, `ALT`, `ALTS`, `ALTV`, `VS`, `FLC`, `GS`,
`VNV`, `GA`, `UNKNOWN`.

| Value | Meaning |
| --- | --- |
| `NONE` | Annunciator validly blank — nothing active/armed |
| `ROL` | Wings-level / roll hold |
| `HDG` | Heading select |
| `NAV` | VOR/LOC navigation tracking |
| `GPSS` | GPS steering |
| `APR` | Approach (localizer) |
| `BC` | Localizer back-course |
| `PIT` | Pitch hold |
| `ALT` | Altitude hold |
| `ALTS` | Selected-altitude capture |
| `ALTV` | VNAV-altitude capture |
| `VS` | Vertical speed |
| `FLC` | Flight level change (speed hold) |
| `GS` | Glideslope |
| `VNV` | VNAV path |
| `GA` | Takeoff/go-around |
| `UNKNOWN` | Simulator reported a state outside this vocabulary — inspect `raw_status_values`, never guess |

A mode field that is `None` means the *data* is unavailable/stale;
`NONE` means the data is good and no mode is annunciated.

## Send inputs

### Buttons (momentary; request only)

All button methods take no arguments and return nothing:

| Method | Button | Requests |
| --- | --- | --- |
| `panel.press_ap()` | AP | Toggle autopilot servos |
| `panel.press_ap_disconnect()` | AP DISC | Disconnect servos (deterministic off) |
| `panel.press_fd()` | FD | Toggle flight director |
| `panel.press_yd()` | YD | Toggle yaw damper |
| `panel.press_hdg()` | HDG | Heading select mode |
| `panel.press_rol()` | ROL | Wings-level roll mode |
| `panel.press_nav()` | NAV | Arm/select navigation tracking |
| `panel.press_apr()` | APR | Arm/select approach |
| `panel.press_bc()` | BC | Arm/select back-course |
| `panel.press_alt()` | ALT | Altitude hold |
| `panel.press_vs()` | VS | Vertical-speed mode |
| `panel.press_flc()` | FLC | Flight-level-change mode |
| `panel.press_vnv()` | VNV | Arm/select VNAV path |
| `panel.press_ga()` | GA | Takeoff/go-around |
| `panel.press_gs()` | GS | Toggle the glideslope arm — the verified way to disarm an armed approach glideslope |

Generic form (same behavior, string-friendly): `panel.press_button("HDG")`
with any `AfcsButton` value: `AP`, `AP_DISC`, `FD`, `YD`, `HDG`, `ROL`,
`NAV`, `APR`, `BC`, `ALT`, `VS`, `FLC`, `VNV`, `GA`, `GS`. Unknown names
raise `ValueError` and send nothing.

Buttons behave like the real panel: pressing a mode button again usually
cancels that mode; pressing `APR` with no usable approach source changes
nothing. The read-back tells you which happened.

**Verified behavior on the C172 G1000 (XP 12.4.3, live checklist):**

- `ap_engaged` reports **servo engagement** (`servos_on`). The flight
  director can be on while servos are off — check both fields.
- `press_ap_disconnect()` drops the servos and leaves the flight director
  on, like the yoke AP DISC button. `press_ap()` toggles servos both ways.
- Verified effective: AP, AP_DISC, FD, YD, HDG, ROL, ALT, VS, FLC, GA,
  APR (armed GS with an ILS tuned), and all selector writes except course
  (see caveat above).
- Conditionally effective (no annunciation change without the required
  source/plan in the tested session): NAV and BC (need a usable nav
  source), VNV (needs a VNAV flight plan). These presses transmitted but
  did not take effect — the expected, documented non-transition outcome.
- Pressing a button whose mode is already active toggles it off (e.g.,
  HDG active + `press_hdg()` reverts to ROL; ALT active + `press_alt()`
  reverts to PIT). Check current state before pressing.
- **Armed-mode masking:** several vertical modes can be armed at once
  (e.g., GS and ALTS during an armed approach). The single
  `vertical_mode_armed` field reports the highest-precedence one
  (GS outranks ALTS); check `raw_status_values`
  (e.g., `sim/cockpit2/autopilot/alts_armed`) to see the full armed set.
- **APR re-press does not disarm (verified):** with an approach armed,
  pressing APR again does not clear the GS arm and drops the active
  lateral mode to ROL. To disarm an armed glideslope, use `press_gs()`
  (verified), then restore your lateral mode (e.g., HDG) and confirm via
  read-back.

### Selectors (value inputs with strict ranges)

Out-of-range or non-finite values raise `ValueError` and **nothing is
transmitted** — values are never clamped. Ranges are API transmission
guards, not aircraft performance claims.

| Method | Units | Accepted range |
| --- | --- | --- |
| `panel.set_heading_bug_deg(value)` | degrees magnetic | `0 <= value < 360` |
| `panel.set_altitude_selector_ft(value)` | feet | `-1000 <= value <= 60000` |
| `panel.set_vertical_speed_target_fpm(value)` | feet/minute | `-8000 <= value <= 8000` |
| `panel.set_airspeed_target_kt(value)` | knots | `0 <= value <= 400` |
| `panel.set_course_deg(value)` | degrees magnetic | `0 <= value < 360` |

**Course caveat (verified):** on the C172 G1000 with the HSI source set to
GPS, the course is slaved to the active flight-plan leg and course writes do
not take effect — the read-back keeps the GPS course. Treat
`set_course_deg` as ineffective in that configuration; always confirm via
read-back like any other input.

Generic form: `panel.set_selector("ALTITUDE", 5500.0)` with any
`AfcsSelector` value: `HEADING`, `ALTITUDE`, `VERTICAL_SPEED`, `AIRSPEED`,
`COURSE`.

Selectors are legal to set whether or not a related mode is active, exactly
like turning the physical knobs.

## Validate results

### Example: press a button and confirm (heading mode)

```python
from xplane_api import LateralMode

panel.set_heading_bug_deg(270.0)
panel.press_hdg()

result = panel.wait_for(
    lambda s: s.lateral_mode_active is LateralMode.HDG
    and s.selected_heading_deg_mag == 270.0,
    timeout_seconds=3.0,
    poll_interval_seconds=0.25,
    stale_after_seconds=2.0,
)
if result.satisfied:
    print("HDG active toward", result.state.selected_heading_deg_mag)
else:
    print("HDG did not engage; annunciated:", result.state.lateral_mode_active)
```

`wait_for(predicate, *, timeout_seconds, poll_interval_seconds,
stale_after_seconds)` polls state until the predicate holds or the timeout
elapses. It returns a `ConfirmationResult` with two fields: `satisfied`
(bool) and `state` (the final observed `AutopilotState`, either way). It
never raises on timeout and sends no input itself.

### Example: set a selector and confirm (altitude capture armed)

```python
from xplane_api import VerticalMode

panel.set_altitude_selector_ft(5500.0)
panel.press_vs()  # climb/descend toward it in VS mode

result = panel.wait_for(
    lambda s: s.vertical_mode_active is VerticalMode.VS
    and s.vertical_mode_armed is VerticalMode.ALTS,
    timeout_seconds=3.0,
    poll_interval_seconds=0.25,
    stale_after_seconds=2.0,
)
# Later, with no further input, ALTS captures and becomes ALT as the
# aircraft reaches 5500 ft — confirm with another wait_for.
```

### Example: plain read-back without the helper

```python
panel.press_ap_disconnect()
state = panel.read_state(timeout_seconds=1.0, stale_after_seconds=2.0)
assert state.is_usable("ap_engaged") and state.ap_engaged is False
```

### Worked failure case: detecting a non-transition

Pressing APR with no approach source leaves the annunciation unchanged.
A timeout with an unchanged state is the correct, expected signal:

```python
panel.press_apr()
result = panel.wait_for(
    lambda s: s.lateral_mode_active is LateralMode.APR
    or s.lateral_mode_armed is LateralMode.APR,
    timeout_seconds=3.0,
    poll_interval_seconds=0.25,
    stale_after_seconds=2.0,
)
if not result.satisfied:
    # The request did not take effect. Inspect what IS annunciated:
    print("APR not armed; lateral is", result.state.lateral_mode_active)
    # Do not retry in a loop; report the observed state instead.
```

## Interpreting errors and bad data

| Signal | Meaning | Correct response |
| --- | --- | --- |
| `ValueError` from an input method | Your argument was invalid (unknown button/selector, out-of-range or non-finite value) | Fix the argument; nothing was sent |
| `OSError` from an input method | The packet could not be sent from this host | Connectivity problem; nothing reached the simulator |
| `RuntimeError: control client is closed` | The panel/control client was closed | Reconnect before sending |
| `wait_for` returns `satisfied=False` | The expected annunciation never appeared | The request did not take effect; report `result.state` |
| Mode fields `None` with `unavailable` status | No data received | Simulator not sending; check connection and subscriptions |
| Fields marked `stale` | Data stopped arriving | Simulator paused/stopped/unreachable; treat nothing as current; inputs may silently do nothing |
| Mode is `UNKNOWN` | Simulator state outside the documented vocabulary | Use `raw_status_values` for diagnostics; do not guess |

Remember: a successful send proves only that a UDP packet left this host.
The simulator acknowledges nothing. Truth is always the next state read.

## Session safety for automation

An autopilot left disengaged keeps nobody flying the aircraft. If your
automation disengages the autopilot (any `press_ap`, `press_ap_disconnect`,
`press_fd`, or `press_ga` call can), you are responsible for the aircraft's
state afterwards:

- **Always end a session by re-engaging and confirming**: press AP, then
  `wait_for(lambda s: s.ap_engaged is True, ...)` plus your intended
  lateral/vertical modes. Do not finish on an unconfirmed restore.
- **Confirm stability, not just annunciation**: check that vertical speed
  and targets make sense for continued flight before ending.
- **Recovery**: if the flight is unrecoverable, the X-Plane command
  `sim/operation/reset_flight` (send via
  `XPlaneControlClient.send_command`) resets to the most recent flight
  start. Re-engage and confirm the autopilot after any reset.
