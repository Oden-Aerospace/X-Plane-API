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