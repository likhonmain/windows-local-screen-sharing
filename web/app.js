'use strict';
const $ = id => document.getElementById(id);
const control = document.body.dataset.role === 'control';
let current, poll, paused = false, streaming = false, paired = control, touchEnabled = true, touchController;
const screen = $('screen');
function error(message) { $('error').textContent = message || ''; $('error').hidden = !message; }
async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {cache:'no-store'} : {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
function overlay(message) { $('message').textContent = message; $('overlay').hidden = false; }
function stopView(message) {
  touchController?.cancel();
  streaming = false; screen.removeAttribute('src'); screen.hidden = true; overlay(message);
}
function startView() {
  if (streaming || paused || document.hidden || !current?.sharing || current.error) return;
  streaming = true; screen.hidden = false; overlay('Opening your screen…');
  screen.src = `/stream?t=${Date.now()}`;
}
function render(state) {
  if (current && (current.monitor !== state.monitor || !state.touch)) touchController?.cancel();
  current = state;
  const quality = $(control ? 'controlQuality' : 'viewerQuality');
  if (!quality.options.length) {
    for (const [key, preset] of Object.entries(state.presets)) quality.add(new Option(preset.label, key));
  }
  $('status').textContent = state.error ? 'Capture unavailable' : state.sharing
    ? `${state.viewers ? 'Live' : 'Ready'} · ${state.viewers} viewer${state.viewers === 1 ? '' : 's'}${state.fps ? ` · ${state.fps} fps` : ''}`
    : 'Sharing paused on laptop';
  if (state.error) error(state.error); else error('');
  if (control) {
    $('address').textContent = `http://${state.ip}:${state.port}`;
    $('controlQuality').value = state.preset;
    $('sharing').textContent = state.sharing ? 'Pause sharing' : 'Start sharing';
    $('allowTouch').checked = state.touch;
    const preset = state.presets[state.preset];
    $('qualityInfo').textContent = `${preset.width ? `Up to ${preset.width}px wide` : 'Original display resolution'} · ${preset.format === 'PNG' ? 'Lossless PNG' : `JPEG ${preset.quality}%`} · target ${preset.fps} fps. Higher quality uses more local Wi-Fi bandwidth.`;
    if (!$('monitor').options.length) {
      for (const m of state.monitors) $('monitor').add(new Option(`Display ${m.id} · ${m.width} × ${m.height}`, m.id));
    }
    $('monitor').value = state.monitor;
  } else {
    $('viewerQuality').value = state.preset;
    $('touchToggle').disabled = !state.touch;
    $('touchToggle').textContent = !state.touch ? 'Touch blocked by laptop' : touchEnabled ? 'Touch on' : 'Touch off';
    $('touchToggle').setAttribute('aria-pressed', String(state.touch && touchEnabled));
    $('stage').classList.toggle('touch-active', state.touch && touchEnabled);
    if (!state.sharing) stopView('Sharing is paused on your laptop.');
    else if (state.error) stopView(state.error);
    else if (!paused) {
      startView();
      // Continuous MJPEG may not dispatch img.onload until the stream ends.
      if (streaming && state.fps > 0) $('overlay').hidden = true;
    }
  }
}
async function refresh() {
  if (!paired || document.hidden) return;
  try { render(await api('/api/status')); }
  catch (e) { error(e.message); $('status').textContent = 'Laptop unreachable'; if (!control) stopView('Connection lost. Keep your laptop on this hotspot.'); }
}
async function setting(payload) {
  try { render(await api('/api/settings', payload)); } catch(e) { error(e.message); }
}
async function initialize() {
  $(control ? 'control' : 'viewer').hidden = false;
  if (control) $('qr').src = '/qr.png';
  if (!control) {
    const token = location.hash.slice(1);
    history.replaceState(null, '', location.pathname);
    if (token) {
      try { await api('/api/pair', {token}); paired = true; }
      catch(e) { overlay(e.message); error(e.message); return; }
    } else {
      try { render(await api('/api/status')); paired = true; }
      catch(e) { overlay('Scan the QR code or open the full pairing link from your laptop.'); error(e.message); return; }
    }
  }
  await refresh(); poll = setInterval(refresh, 2000);
}
$('copy').onclick = async () => {
  try {
    if (navigator.clipboard) await navigator.clipboard.writeText(current.pair_url);
    else { const input = document.createElement('textarea'); input.value = current.pair_url; document.body.append(input); input.select(); if(!document.execCommand('copy')) throw new Error('Copy unavailable'); input.remove(); }
    $('copy').textContent = 'Link copied'; setTimeout(() => $('copy').textContent = 'Copy pairing link', 1800);
  } catch(e) { error(`Copy unavailable. Scan the QR code instead. ${e.message}`); }
};
$('sharing').onclick = () => setting({sharing:!current.sharing});
$('stopServer').onclick = async () => {
  try { await api('/api/stop', {}); clearInterval(poll); $('status').textContent = 'Server stopped'; $('sharing').disabled = $('stopServer').disabled = true; error('Sharing has stopped. Run Start-Mirror.cmd to start a new session.'); }
  catch(e) { error(e.message); }
};
$('monitor').onchange = event => setting({monitor:Number(event.target.value)});
$('allowTouch').onchange = event => setting({touch:event.target.checked});
$('controlQuality').onchange = $('viewerQuality').onchange = event => setting({preset:event.target.value});
$('viewToggle').onclick = () => {
  paused = !paused; $('viewToggle').textContent = paused ? 'Resume view' : 'Pause view';
  if (paused) stopView('View paused. Tap Resume view to continue.'); else startView();
};
screen.onload = () => { if(streaming) $('overlay').hidden = true; };
screen.onerror = () => { streaming = false; overlay('Reconnecting…'); setTimeout(refresh, 1500); };
$('retry').onclick = () => { if(!paired) { location.reload(); return; } stopView('Reconnecting…'); refresh(); };
$('fit').onclick = () => { const fill = $('stage').classList.toggle('fill'); $('fit').textContent = fill ? 'Fit screen' : 'Fill screen'; };
function immersive(active) { document.body.classList.toggle('immersive', active); }
$('fullscreen').onclick = async () => {
  const stage = $('stage');
  try { if (!stage.requestFullscreen) throw new Error('Fullscreen unavailable'); await stage.requestFullscreen(); immersive(true); }
  catch {
    stage.classList.add('pseudo-fullscreen'); immersive(true);
    history.pushState({mirrorFullscreen:true}, '', location.href);
  }
};
window.addEventListener('popstate', () => { $('stage').classList.remove('pseudo-fullscreen'); immersive(false); });
window.addEventListener('keydown', event => {
  if (event.key === 'Escape' && $('stage').classList.contains('pseudo-fullscreen')) history.back();
});
$('touchToggle').onclick = () => {
  touchController?.cancel(); touchEnabled = !touchEnabled; if(current) render(current);
};
if (!control) touchController = LocalTouch.attach($('stage'), screen, {
  enabled:() => paired && streaming && !paused && !document.hidden && current?.sharing && current?.touch && touchEnabled && $('overlay').hidden,
  monitor:() => current?.monitors.find(m => m.id === current.monitor) || {id:1, width:0, height:0},
  send:payload => api('/api/input', payload),
  error:e => error(e.message)
});
document.addEventListener('fullscreenchange', () => immersive(!!document.fullscreenElement));
document.addEventListener('visibilitychange', () => { if(!control && document.hidden) stopView('Resume this tab to reconnect.'); if(!document.hidden) refresh(); });
window.addEventListener('pagehide', () => {
  clearInterval(poll);
  if(!control) {
    touchController?.cancel(); screen.removeAttribute('src');
    fetch('/api/input', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action:'release'}), keepalive:true}).catch(() => {});
  }
});
initialize().catch(e => error(e.message));
