const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const html = readFileSync(path.join(__dirname, '../src/xplane_api/web_static/mfd.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const helpers = script.slice(0, script.indexOf("    const map = L.map"));
function run(expression) { return vm.runInNewContext(`${helpers}\n${expression}`); }
test('complete MFD script compiles', () => { new vm.Script(script); });
test('ownship uses true heading and leaves missing heading nondirectional', () => {
  const result = run("flightView({state:{latitude_deg:38,longitude_deg:-77,magnetic_heading_deg:90,field_status:{latitude_deg:'valid',longitude_deg:'valid',magnetic_heading_deg:'valid'}}})");
  assert.equal(result.heading, null);
  assert.equal(result.position[0], 38);
});
test('stale position is suppressed, valid latitude beyond projection limit stays distinct', () => {
  assert.equal(run("flightView({state:{latitude_deg:38,longitude_deg:-77,field_status:{latitude_deg:'stale',longitude_deg:'valid'}}}).status"), 'stale');
  assert.equal(run('flightView({state:null}).position'), null);
  assert.equal(run("flightView({state:{latitude_deg:89,longitude_deg:0,field_status:{latitude_deg:'valid',longitude_deg:'valid'}}}).projectionLimited"), true);
});
test('polygon wrapping uses the nearby world copy instead of crossing the world', () => {
  const result = run('unwrapLine([[179,0],[-179,0],[-179,1],[179,0]],180)');
  assert.equal(result[1][0] - result[0][0], 2);
  assert.equal(run('wrapLongitude(541)'), -179);
});
test('flight plan geometry preserves route order and highlights the active leg', () => {
  const plan = {
    available:true,
    active_index:1,
    entries:[
      {identifier:'KSEA',type:'airport',latitude_deg:47,longitude_deg:-123,field_status:{position:'available'}},
      {identifier:'SEA',type:'fix',latitude_deg:48,longitude_deg:-122,field_status:{position:'available'}},
      {identifier:'FIX2',type:'fix',latitude_deg:49,longitude_deg:-121,field_status:{position:'available'}},
    ],
  };
  const geometry = JSON.parse(JSON.stringify(run(`flightPlanGeometry(${JSON.stringify(plan)},[46,-124],-123)`)));
  assert.equal(geometry.routeSegments.length, 1);
  assert.deepEqual(geometry.routeSegments[0], [[47,-123],[48,-122],[49,-121]]);
  assert.deepEqual(geometry.activeLeg, [[47,-123],[48,-122]]);
  assert.equal(geometry.waypoints[1].active, true);
});
test('flight plan geometry uses ownship for the first active leg and breaks at missing positions', () => {
  const plan = {
    available:true,
    active_index:0,
    entries:[
      {identifier:'KSEA',type:'airport',latitude_deg:47,longitude_deg:-123,field_status:{position:'available'}},
      {identifier:'NO POS',type:'fix',latitude_deg:null,longitude_deg:null,field_status:{position:'unavailable'}},
      {identifier:'FIX2',type:'fix',latitude_deg:49,longitude_deg:-121,field_status:{position:'available'}},
    ],
  };
  const geometry = JSON.parse(JSON.stringify(run(`flightPlanGeometry(${JSON.stringify(plan)},[46,-124],-123)`)));
  assert.deepEqual(geometry.activeLeg, [[46,-124],[47,-123]]);
  assert.equal(geometry.routeSegments.length, 0);
  assert.equal(geometry.waypoints.length, 2);
});
test('flight plan layer defaults on without removing existing aviation layers', () => {
  const control = html.match(/<input type="checkbox" id="layer-flight-plan"[^>]*>/)?.[0];
  assert.ok(control);
  assert.match(control, /\bchecked\b/);
  for (const layer of ['airports', 'navaids', 'fixes', 'airspace']) {
    assert.match(html, new RegExp(`id="layer-${layer}"`));
  }
});
test('airport labels are prioritized by runway length and decluttered by range', () => {
  const features = [
    {id:'near-strip',geometry:{type:'Point',coordinates:[-77,38]},properties:{layer:'airports',runways:[{length_m:300}]}},
    {id:'large-field',geometry:{type:'Point',coordinates:[-77.2,38]},properties:{layer:'airports',runways:[{length_m:2500}]}},
    ...Array.from({length:10},(_,index)=>({id:`airport-${index}`,geometry:{type:'Point',coordinates:[-77+index*.01,38]},properties:{layer:'airports',runways:[{length_m:500}]}})),
    ...Array.from({length:20},(_,index)=>({id:`strip-${index}`,geometry:{type:'Point',coordinates:[-77+index*.01,38]},properties:{layer:'airports',runways:[]}})),
  ];
  const ids = run(`airportLabelIds(${JSON.stringify(features)},20,{lat:38,lng:-77})`);
  assert.equal(ids.size,8);
  assert.equal(ids.has('large-field'),true);
  assert.equal(ids.has('near-strip'),false);
  assert.equal(run(`airportLabelIds(${JSON.stringify(features)},100,{lat:38,lng:-77})`).size,0);
});
test('a failed live source cannot present its retained last sample as current', () => {
  const result = run("flightView({mode:'live',status:'error',state:{latitude_deg:38,longitude_deg:-77,true_heading_deg:90,field_status:{latitude_deg:'valid',longitude_deg:'valid',true_heading_deg:'valid'}}})");
  assert.equal(result.position, null);
  assert.equal(result.heading, null);
  assert.equal(result.status, 'error');
});