const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const html = readFileSync(path.join(__dirname, '../src/xplane_api/web_static/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function fixture(display = '') {
  const nodes = new Map();
  const fetches = [];
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {
      textContent: '', innerHTML: '', className: '', dataset: {}, style: {}, hidden: false,
      clientWidth: 180, clientHeight: 360, attributes: {}, classList: { add() {}, remove() {} },
      appendChild() {}, addEventListener() {}, querySelectorAll: () => [],
      querySelector: () => node(`${id}-flag`),
      setAttribute(name, value) { this.attributes[name] = value; },
    });
    return nodes.get(id);
  }
  node('body').dataset.display = display;
  const context = vm.createContext({
    document: { body: node('body'), getElementById: node, createElement: () => node(Symbol()), querySelectorAll: () => [] },
    window: { addEventListener() {} },
    fetch: async (url, options) => {
      fetches.push({ url, options });
      return { ok: true, json: async () => ({ mode: 'idle', state: null }) };
    },
    setInterval() {}, clearInterval() {}, Blob, console,
  });
  vm.runInContext(script, context);
  return { node, context, fetches, run: source => vm.runInContext(source, context) };
}

const sample = {
  indicated_airspeed_kt: 104, barometric_altitude_ft: 3250, pitch_deg: 10, roll_deg: 20,
  magnetic_heading_deg: 359, vertical_speed_fpm: -650, ground_speed_mps: 50,
  barometer_setting_inhg: 29.92, turn_indicator_deflection_deg: 1.5, slip_indicator_deflection_deg: -2,
};
sample.field_status = Object.fromEntries(Object.keys(sample).map(field => [field, 'valid']));

test('glass renderer maps values, units, attitude directions, and scale positions', () => {
  const ui = fixture();
  ui.context.sample = sample;
  ui.run('renderGlass(sample)');
  assert.equal(ui.node('pfd-airspeedValue').textContent, 104);
  assert.equal(ui.node('pfd-altitudeValue').textContent, 3250);
  assert.equal(ui.node('pfd-pitch').attributes.transform, 'translate(0 40)');
  assert.equal(ui.node('pfd-bank').attributes.transform, 'rotate(-20 200 200)');
  assert.equal(ui.node('pfd-bankScale').attributes.transform, 'rotate(-20 200 200)');
  assert.equal(ui.node('pfd-ladder').attributes.transform, 'rotate(-20 200 200) translate(0 40)');
  assert.equal(ui.node('pfd-headingValue').textContent, '359 M');
  assert.equal(ui.node('pfd-vsiValue').textContent, '-650');
  assert.equal(ui.node('pfd-vsiAux').textContent, '-650');
  assert.equal(ui.node('pfd-gs').textContent, '97');
  assert.equal(ui.node('pfd-baro').textContent, '29.92');
  assert.equal(ui.node('pfd-turn').textContent, '1.5');
  assert.equal(ui.node('pfd-slipValue').textContent, '-2.0');
  assert.equal(ui.node('pfd-slipBall').style.left, '34%');
  assert.match(ui.node('pfd-altitudeTicks').innerHTML, />3300<\/text>/);
  assert.equal(ui.node('pfd-horizon').attributes.viewBox, '100 0 200 400');
  assert.match(ui.node('pfd-vsi').innerHTML, />-2k<\/text>/);
});

test('level attitude and zero readings remain valid rather than unavailable', () => {
  const ui = fixture();
  ui.context.sample = { ...sample, indicated_airspeed_kt: 0, barometric_altitude_ft: 0, pitch_deg: 0, roll_deg: 0, magnetic_heading_deg: 0, vertical_speed_fpm: 0, ground_speed_mps: 0 };
  ui.run('renderGlass(sample)');
  assert.equal(ui.node('pfd-airspeedValue').textContent, 0);
  assert.equal(ui.node('pfd-altitudeValue').textContent, 0);
  assert.equal(ui.node('pfd-vsiValue').textContent, '0');
  assert.equal(ui.node('pfd-gs').textContent, '0');
  assert.equal(ui.node('pfd-bank').attributes.transform, 'rotate(0 200 200)');
  assert.equal(ui.node('pfd-pitch').attributes.transform, 'translate(0 0)');
  assert.equal(ui.node('pfd-attitude').dataset.status, 'valid');
});

test('negative altitude, nose down, left bank, climb, and north wrap remain correct', () => {
  const ui = fixture();
  ui.context.sample = { ...sample, barometric_altitude_ft: -200, pitch_deg: -5, roll_deg: -30, magnetic_heading_deg: 360, vertical_speed_fpm: 3500 };
  ui.run('renderGlass(sample)');
  assert.equal(ui.node('pfd-altitudeValue').textContent, -200);
  assert.equal(ui.node('pfd-pitch').attributes.transform, 'translate(0 -20)');
  assert.equal(ui.node('pfd-bank').attributes.transform, 'rotate(30 200 200)');
  assert.equal(ui.node('pfd-headingValue').textContent, '000 M');
  assert.equal(ui.node('pfd-rose').attributes.transform, 'rotate(0 155 140)');
  assert.equal(ui.node('pfd-vsiValue').textContent, '+3500');
  assert.equal(ui.node('pfd-vsiPointer').attributes.transform, 'translate(0 -130)');
});

test('invalid and stale dependencies suppress only their own indications', () => {
  const ui = fixture();
  ui.context.sample = { ...sample, field_status: { ...sample.field_status, roll_deg: 'stale', indicated_airspeed_kt: 'invalid' } };
  ui.run('renderGlass(sample)');
  assert.equal(ui.node('pfd-attitude').dataset.status, 'stale');
  assert.equal(ui.node('pfd-airspeed').dataset.status, 'invalid');
  assert.equal(ui.node('pfd-airspeedValue').textContent, '--');
  assert.equal(ui.node('pfd-altitude').dataset.status, 'valid');
  ui.run('renderGlass(null)');
  for (const id of ['airspeed', 'altitude', 'attitude', 'heading', 'vsi']) {
    assert.equal(ui.node(`pfd-${id}`).dataset.status, 'unavailable');
    assert.equal(ui.node(`pfd-${id}Value`).textContent, '--');
  }
  assert.equal(ui.node('pfd-gs').textContent, '--');
});

test('view switching is local and live/replay samples render identically', () => {
  const ui = fixture();
  ui.context.sample = sample;
  ui.run("renderGlass({...sample, source: 'live_xplane'})");
  const live = ui.node('pfd-altitudeTicks').innerHTML;
  ui.run("renderGlass({...sample, source: 'recording'}); selectView(false)");
  assert.equal(ui.node('pfd-altitudeTicks').innerHTML, live);
  assert.equal(ui.node('glassCockpit').hidden, true);
  assert.equal(ui.node('sixPackView').attributes['aria-pressed'], 'true');
  ui.run('selectView(true)');
  assert.equal(ui.node('gauges').hidden, true);
});

test('HTTP and network failures clear all current-looking glass readings', async () => {
  const ui = fixture();
  ui.context.sample = sample;
  await ui.run('refresh()');
  ui.run('renderGlass(sample)');
  ui.context.fetch = async () => ({ ok: false, status: 503 });
  await ui.run('refresh()');
  assert.equal(ui.node('pfd-heading').dataset.status, 'unavailable');
  assert.equal(ui.node('connectionText').textContent, 'Local API unavailable');
  ui.context.fetch = async () => { throw new Error('offline'); };
  await ui.run('refresh()');
  assert.equal(ui.node('pfd-airspeedValue').textContent, '--');
});

test('dedicated modes select their view and only read the shared state', () => {
  for (const display of ['glass', 'sixpack', '']) {
    const ui = fixture(display);
    assert.equal(ui.node('glassCockpit').hidden, display === 'sixpack');
    assert.equal(ui.node('gauges').hidden, display !== 'sixpack');
    assert.equal(ui.fetches.length, display ? 1 : 2);
    assert.equal(ui.fetches.filter(request => request.url === '/api/state').length, 1);
    assert.ok(ui.fetches.every(request => request.options.method === undefined));
  }
});

test('six-pack hides stale and missing geometry without suppressing valid instruments', async () => {
  const ui = fixture('sixpack');
  await ui.run('refresh()');
  ui.context.sample = sample;
  ui.run("render({mode: 'recording', status: 'paused', state: sample})");
  assert.equal(ui.node('needle-airspeed').style.visibility, '');
  assert.equal(ui.node('skyground').style.visibility, '');
  ui.context.sample = { ...sample, field_status: { ...sample.field_status, indicated_airspeed_kt: 'stale', magnetic_heading_deg: 'invalid', roll_deg: 'stale', slip_indicator_deflection_deg: 'unavailable' } };
  ui.run("render({mode: 'recording', status: 'paused', state: sample})");
  assert.equal(ui.node('needle-airspeed').style.visibility, 'hidden');
  assert.match(ui.node('value-airspeed').innerHTML, /^--</);
  assert.equal(ui.node('rose-heading').style.visibility, 'hidden');
  assert.equal(ui.node('skyground').style.visibility, 'hidden');
  assert.equal(ui.node('slipBall').style.visibility, 'hidden');
  assert.equal(ui.node('needle-vsi').style.visibility, '');
  assert.equal(ui.node('needle-turn').style.visibility, '');
  ui.run("render({mode: 'idle', state: null})");
  for (const id of ['airspeed', 'turn', 'heading', 'vsi']) {
    assert.equal(ui.node(`needle-${id}`).style.visibility, 'hidden');
  }
  ui.context.sample = sample;
  ui.run("render({mode: 'live', status: 'connected', state: sample})");
  assert.equal(ui.node('needle-airspeed').style.visibility, '');
  assert.equal(ui.node('rose-heading').style.visibility, '');
  assert.equal(ui.node('skyground').style.visibility, '');
  assert.equal(ui.node('slipBall').style.visibility, '');
});

test('six-pack polling failure clears the previous needles and horizon', async () => {
  const ui = fixture('sixpack');
  await ui.run('refresh()');
  ui.context.sample = sample;
  ui.run("render({mode: 'live', status: 'connected', state: sample})");
  ui.context.fetch = async () => { throw new Error('offline'); };
  await ui.run('refresh()');
  assert.equal(ui.node('needle-airspeed').style.visibility, 'hidden');
  assert.equal(ui.node('skyground').style.visibility, 'hidden');
  assert.match(ui.node('value-airspeed').innerHTML, /^--</);
});