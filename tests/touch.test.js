const test = require('node:test');
const assert = require('node:assert/strict');
const {mapPoint, attach} = require('../web/touch.js');

test('fit rejects letterboxing and maps the actual visible desktop', () => {
  const rect = {left:10, top:20, width:1000, height:1000};
  assert.equal(mapPoint(rect, 1920, 1080, false, 510, 30), null);
  assert.deepEqual(mapPoint(rect, 1920, 1080, false, 510, 520), {x:0.5,y:0.5});
  assert.deepEqual(mapPoint(rect, 1920, 1080, false, 510, 0, true), {x:0.5,y:0});
});
test('fill accounts for cropped edges instead of stretching coordinates', () => {
  const rect = {left:0, top:0, width:1000, height:1000};
  const left = mapPoint(rect, 1920, 1080, true, 0, 500);
  const right = mapPoint(rect, 1920, 1080, true, 1000, 500);
  assert.ok(Math.abs(left.x - 0.21875) < 1e-8);
  assert.ok(Math.abs(right.x - 0.78125) < 1e-8);
});
function setup() {
  const handlers = {};
  const events = [];
  const stage = {classList:{contains:()=>false}, setPointerCapture:()=>{},
    addEventListener:(type, handler)=>handlers[type]=handler};
  const image = {getBoundingClientRect:()=>({left:0,top:0,width:1000,height:562.5})};
  const controller = attach(stage,image,{enabled:()=>true,
    monitor:()=>({id:1,width:1920,height:1080}),send:async event=>events.push(event),
    error:e=>{throw e;}});
  const fire = (type, x, y, pointerId=1) => handlers[type]({clientX:x,clientY:y,pointerId,button:0,preventDefault:()=>{}});
  return {fire,events,controller};
}
const drain = () => new Promise(resolve => setImmediate(resolve));
test('tap clicks and drag releases the left button', async () => {
  const {fire,events,controller} = setup();
  try {
    fire('pointerdown',500,281.25); fire('pointerup',500,281.25); await drain();
    assert.equal(events[0].action,'click'); assert.equal(events[0].x,0.5);
    events.length=0;
    fire('pointerdown',400,200); fire('pointermove',600,300); fire('pointerup',600,300); await drain();
    assert.equal(events[0].action,'down'); assert.equal(events.at(-1).action,'up');
  } finally {controller.dispose();}
});
test('two-finger scroll does not emit an accidental click', async () => {
  const {fire,events,controller} = setup();
  try {
    await new Promise(resolve=>setTimeout(resolve,40));
    fire('pointerdown',400,200,1); fire('pointerdown',600,200,2);
    fire('pointermove',400,300,1); fire('pointerup',400,300,1); fire('pointerup',600,200,2); await drain();
    assert.deepEqual(events.map(e=>e.action), ['scroll']); assert.ok(events[0].delta>0);
  } finally {controller.dispose();}
});
test('cancellation releases a drag and long press emits only a right click', async () => {
  const {fire,events,controller} = setup();
  try {
    fire('pointerdown',400,200); fire('pointermove',600,300); controller.cancel(); await drain();
    assert.equal(events.at(-1).action,'release'); events.length=0;
    fire('pointerdown',400,200); await new Promise(resolve=>setTimeout(resolve,600));
    fire('pointerup',400,200); await drain();
    assert.deepEqual(events.map(e=>e.action), ['right_click']);
  } finally {controller.dispose();}
});
