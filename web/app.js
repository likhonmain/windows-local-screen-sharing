'use strict';
const $ = id => document.getElementById(id);
const control = document.body.dataset.role === 'control';
const screen = $('screen'), video = $('screenVideo');
let current, poll, paused = false, streaming = false, paired = control;
let touchEnabled = true, touchController, pc, touchChannel, rtcId, generation = 0;
let activeTransport = '', lastVideoStats, painted = 0, lastPaintStats;
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
function closeVideo() {
  const oldId = rtcId; rtcId = null; touchChannel = null;
  if (pc) { const old = pc; pc = null; old.close(); }
  video.pause(); video.srcObject = null; video.hidden = true;
  lastVideoStats = null; lastPaintStats = null; painted = 0;
  if (oldId) fetch('/api/rtc/close', {method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:oldId}),keepalive:true}).catch(() => {});
}
function stopView(message) {
  touchController?.cancel(); generation++; streaming = false;
  closeVideo(); screen.removeAttribute('src'); screen.hidden = true;
  activeTransport = ''; overlay(message);
}
function imageView(message = '') {
  closeVideo(); activeTransport = 'image'; screen.hidden = false;
  screen.src = `/stream?t=${Date.now()}`;
  $('videoStats').textContent = message || 'Image compatibility · capped at 60 fps; lossless PNG at 8 fps.';
}
async function iceComplete(peer) {
  if (peer.iceGatheringState === 'complete') return;
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {peer.removeEventListener('icegatheringstatechange', listener); reject(new Error('Local video negotiation timed out'));}, 6000);
    function listener() { if(peer.iceGatheringState === 'complete') {clearTimeout(timer);peer.removeEventListener('icegatheringstatechange',listener);resolve();} }
    peer.addEventListener('icegatheringstatechange',listener);
  });
}
async function realtimeView(version) {
  if (!window.RTCPeerConnection || !window.RTCRtpReceiver) {
    imageView('This browser does not support realtime video. Use current Chrome on your phone for higher frame rates.');
    return;
  }
  const peer = new RTCPeerConnection({iceServers:[], bundlePolicy:'max-bundle'});
  pc = peer; activeTransport = 'video'; video.hidden = false;
  const transceiver = peer.addTransceiver('video', {direction:'recvonly'});
  const codecs = RTCRtpReceiver.getCapabilities?.('video')?.codecs.filter(codec => codec.mimeType.toLowerCase() === 'video/h264');
  if (codecs?.length && transceiver.setCodecPreferences) transceiver.setCodecPreferences(codecs);
  touchChannel = peer.createDataChannel('touch', {ordered:true});
  touchChannel.onmessage = event => { try {const message = JSON.parse(event.data);if(message.error)error(message.error);}catch{ } };
  let firstFrameTimer;
  peer.ontrack = event => {
    if (version !== generation) return;
    video.srcObject = event.streams[0] || new MediaStream([event.track]);
    try { if('jitterBufferTarget' in event.receiver) event.receiver.jitterBufferTarget = 0; } catch { }
    video.play().catch(() => overlay('Tap Reconnect to start video.'));
    firstFrameTimer = setTimeout(() => {
      if (version === generation && video.readyState < 2) fallback();
    }, 10000);
    video.addEventListener('loadeddata', () => clearTimeout(firstFrameTimer), {once:true});
  };
  let connected = false;
  const connectionTimeout = setTimeout(() => fallback(), 10000);
  function fallback() {
    if(version !== generation || pc !== peer) return;
    clearTimeout(connectionTimeout);
    clearTimeout(firstFrameTimer);
    imageView('Video could not connect. Run Enable-Hotspot-Access.cmd again to allow UDP, then tap Reconnect. Showing compatibility images.');
  }
  peer.onconnectionstatechange = () => {
    if(version !== generation || pc !== peer) return;
    if(peer.connectionState === 'connected') {connected = true;clearTimeout(connectionTimeout);}
    else if(['failed','disconnected','closed'].includes(peer.connectionState)) fallback();
  };
  try {
    await peer.setLocalDescription(await peer.createOffer()); await iceComplete(peer);
    if(version !== generation) {peer.close(); return;}
    const answer = await api('/api/rtc/offer', {type:peer.localDescription.type,sdp:peer.localDescription.sdp});
    if(version !== generation || pc !== peer) {
      api('/api/rtc/close',{id:answer.id}).catch(() => {});peer.close();return;
    }
    rtcId = answer.id;
    await peer.setRemoteDescription({type:answer.type,sdp:answer.sdp});
    if (!connected) $('videoStats').textContent = `Connecting direct local video · target ${current.target_fps} fps…`;
  } catch(e) {
    if(version === generation) {error(e.message);fallback();}
  }
}
function startView() {
  if(streaming || paused || document.hidden || !current?.sharing || current.error) return;
  streaming = true; const version = ++generation; overlay('Opening your screen…');
  if(current.preset === 'lossless' || $('transport').value === 'image') imageView();
  else realtimeView(version);
}
function paintFrame() {
  if(video.requestVideoFrameCallback) video.requestVideoFrameCallback(() => {
    if(streaming && activeTransport === 'video') {painted++;$('overlay').hidden = true;}
    paintFrame();
  });
}
video.onplaying = () => { if(streaming && activeTransport === 'video') $('overlay').hidden = true; };
paintFrame();
async function updateVideoStats() {
  const peer = pc;
  if(!peer || activeTransport !== 'video' || peer.connectionState !== 'connected') return;
  try {
    const stats = await peer.getStats();
    if(pc !== peer) return;
    let incoming, rtt = 0;
    stats.forEach(report => {
      if(report.type === 'inbound-rtp' && (report.kind === 'video' || report.mediaType === 'video')) incoming = report;
      if(report.type === 'candidate-pair' && report.state === 'succeeded' && report.currentRoundTripTime) rtt = report.currentRoundTripTime * 1000;
    });
    if(!incoming) return;
    const now = performance.now();
    let decoded = incoming.framesPerSecond || 0, displayed = 0;
    if(lastVideoStats) {
      const elapsed = (incoming.timestamp - lastVideoStats.timestamp) / 1000;
      if(elapsed > 0) decoded = (incoming.framesDecoded - lastVideoStats.framesDecoded) / elapsed;
    }
    if(lastPaintStats) displayed = (painted - lastPaintStats.painted) * 1000 / (now - lastPaintStats.now);
    lastVideoStats = incoming; lastPaintStats = {painted,now};
    $('videoStats').textContent = `${Math.round(decoded)} decoded fps${video.requestVideoFrameCallback ? ` · ${Math.round(displayed)} displayed fps` : ''} · target ${current.target_fps} · ${incoming.frameWidth || video.videoWidth} × ${incoming.frameHeight || video.videoHeight}${rtt ? ` · ${Math.round(rtt)} ms network round trip` : ''}`;
  } catch { }
}
function render(state) {
  const old = current;
  if(old && (old.monitor !== state.monitor || !state.touch)) touchController?.cancel();
  current = state;
  const quality = $(control ? 'controlQuality' : 'viewerQuality');
  if(!quality.options.length) for(const [key,preset] of Object.entries(state.presets)) quality.add(new Option(preset.label,key));
  $(control ? 'controlFps' : 'viewerFps').value = state.fps_limit;
  $('status').textContent = state.error ? 'Capture unavailable' : state.sharing
    ? `${state.viewers ? 'Live' : 'Ready'} · ${state.viewers} viewer${state.viewers === 1 ? '' : 's'} · target ${state.target_fps} fps${state.fps ? ` · capture ${Math.round(state.fps)}` : ''}`
    : 'Sharing paused on laptop';
  if(state.error) error(state.error); else error('');
  if(control) {
    $('address').textContent = `http://${state.ip}:${state.port}`;
    $('controlQuality').value = state.preset;
    $('sharing').textContent = state.sharing ? 'Pause sharing' : 'Start sharing';
    $('allowTouch').checked = state.touch;
    const preset = state.presets[state.preset];
    $('qualityInfo').textContent = `${preset.width ? `Up to ${preset.width}px wide` : 'Original display resolution'} · ${state.preset === 'lossless' ? 'Lossless PNG · 8 fps cap' : `Realtime H.264 · target ${state.target_fps} fps`}. Targets depend on capture, encoding, Wi-Fi, and the phone decoder.`;
    $('pipelineInfo').textContent = state.viewers ? `${state.capture_backend} · ${state.encoder} · ${Math.round(state.encoded_fps)} encoded fps` : 'Local video uses Windows GPU capture and Intel Quick Sync when available.';
    if(!$('monitor').options.length) for(const m of state.monitors) $('monitor').add(new Option(`Display ${m.id} · ${m.width} × ${m.height}`,m.id));
    $('monitor').value = state.monitor;
  } else {
    $('viewerQuality').value = state.preset;
    $('touchToggle').disabled = !state.touch;
    $('touchToggle').textContent = !state.touch ? 'Touch blocked by laptop' : touchEnabled ? 'Touch on' : 'Touch off';
    $('touchToggle').setAttribute('aria-pressed',String(state.touch && touchEnabled));
    $('stage').classList.toggle('touch-active',state.touch && touchEnabled);
    if(!state.sharing) stopView('Sharing is paused on your laptop.');
    else if(state.error) stopView(state.error);
    else if(!paused) {
      if(old && (old.preset === 'lossless') !== (state.preset === 'lossless')) stopView('Switching stream…');
      startView();
      if(activeTransport === 'image' && state.fps > 0) $('overlay').hidden = true;
    }
    updateVideoStats();
  }
}
async function refresh() {
  if(!paired || document.hidden) return;
  try {render(await api('/api/status'));}
  catch(e) {error(e.message);$('status').textContent = 'Laptop unreachable';if(!control)stopView('Connection lost. Keep your laptop on this hotspot.');}
}
async function setting(payload) {try {render(await api('/api/settings',payload));}catch(e) {error(e.message);}}
async function initialize() {
  $(control ? 'control' : 'viewer').hidden = false;
  if(control) $('qr').src = '/qr.png';
  else {
    const token = location.hash.slice(1);history.replaceState(null,'',location.pathname);
    try {if(token)await api('/api/pair',{token});else current=await api('/api/status');paired=true;}
    catch(e){overlay('Scan the current QR code or open the full pairing link from your laptop.');error(e.message);return;}
  }
  await refresh();poll=setInterval(refresh,1000);
}
$('copy').onclick=async()=>{
  try {
    if(navigator.clipboard)await navigator.clipboard.writeText(current.pair_url);
    else{const input=document.createElement('textarea');input.value=current.pair_url;document.body.append(input);input.select();if(!document.execCommand('copy'))throw new Error('Copy unavailable');input.remove();}
    $('copy').textContent='Link copied';setTimeout(()=>$('copy').textContent='Copy pairing link',1800);
  }catch(e){error(`Copy unavailable. Scan the QR code instead. ${e.message}`);}
};
$('sharing').onclick=()=>setting({sharing:!current.sharing});
$('stopServer').onclick=async()=>{
  try{await api('/api/stop',{});clearInterval(poll);$('status').textContent='Server stopped';$('sharing').disabled=$('stopServer').disabled=true;error('Run Start-Mirror.cmd to start a new session.');}catch(e){error(e.message);}
};
$('monitor').onchange=event=>setting({monitor:Number(event.target.value)});
$('allowTouch').onchange=event=>setting({touch:event.target.checked});
$('controlQuality').onchange=$('viewerQuality').onchange=event=>setting({preset:event.target.value});
$('controlFps').onchange=$('viewerFps').onchange=event=>setting({fps:Number(event.target.value)});
$('transport').onchange=()=>{stopView('Switching stream…');startView();};
$('viewToggle').onclick=()=>{paused=!paused;$('viewToggle').textContent=paused?'Resume view':'Pause view';if(paused)stopView('View paused. Tap Resume view to continue.');else startView();};
screen.onload=()=>{if(streaming)$('overlay').hidden=true;};
screen.onerror=()=>{if(activeTransport==='image'){streaming=false;overlay('Reconnecting…');setTimeout(refresh,1500);}};
$('retry').onclick=()=>{if(!paired){location.reload();return;}stopView('Reconnecting…');refresh();};
$('fit').onclick=()=>{const fill=$('stage').classList.toggle('fill');$('fit').textContent=fill?'Fit screen':'Fill screen';};
function immersive(active){document.body.classList.toggle('immersive',active);}
$('fullscreen').onclick=async()=>{
  const stage=$('stage');
  try{if(!stage.requestFullscreen)throw new Error('Fullscreen unavailable');await stage.requestFullscreen();immersive(true);}
  catch{stage.classList.add('pseudo-fullscreen');immersive(true);history.pushState({mirrorFullscreen:true},'',location.href);}
};
window.addEventListener('popstate',()=>{$('stage').classList.remove('pseudo-fullscreen');immersive(false);});
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('stage').classList.contains('pseudo-fullscreen'))history.back();});
$('touchToggle').onclick=()=>{touchController?.cancel();touchEnabled=!touchEnabled;if(current)render(current);};
if(!control)touchController=LocalTouch.attach($('stage'),{
  getBoundingClientRect:()=>(activeTransport==='video'?video:screen).getBoundingClientRect()
},{
  enabled:()=>paired&&streaming&&!paused&&!document.hidden&&current?.sharing&&current?.touch&&touchEnabled&&$('overlay').hidden,
  monitor:()=>current?.monitors.find(m=>m.id===current.monitor)||{id:1,width:0,height:0},
  send:payload=>{
    if(touchChannel?.readyState==='open'){
      if(payload.action==='move'&&touchChannel.bufferedAmount>16384)return;
      touchChannel.send(JSON.stringify(payload));return;
    }
    return api('/api/input',payload);
  },
  error:e=>error(e.message)
});
document.addEventListener('fullscreenchange',()=>immersive(!!document.fullscreenElement));
document.addEventListener('visibilitychange',()=>{if(!control&&document.hidden)stopView('Resume this tab to reconnect.');if(!document.hidden)refresh();});
window.addEventListener('pagehide',()=>{
  clearInterval(poll);
  if(!control){stopView('Disconnected');fetch('/api/input',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'release'}),keepalive:true}).catch(()=>{});}
});
initialize().catch(e=>error(e.message));
