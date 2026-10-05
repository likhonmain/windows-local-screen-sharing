(function(root) {
  'use strict';
  function mapPoint(rect, width, height, fill, clientX, clientY, clamp = false) {
    if (!width || !height || !rect.width || !rect.height) return null;
    const scale = (fill ? Math.max : Math.min)(rect.width / width, rect.height / height);
    const left = rect.left + (rect.width - width * scale) / 2;
    const top = rect.top + (rect.height - height * scale) / 2;
    const x = (clientX - left) / (width * scale);
    const y = (clientY - top) / (height * scale);
    if (!clamp && (x < 0 || x > 1 || y < 0 || y > 1)) return null;
    return {x:Math.max(0, Math.min(1, x)), y:Math.max(0, Math.min(1, y))};
  }

  function attach(stage, image, options) {
    const pointers = new Map();
    let gesture, timer, lastMove = 0, scrollRemainder = 0, chain = Promise.resolve();
    function send(payload) {
      const monitor = options.monitor();
      chain = chain.then(() => {
        if (!options.enabled() && !['release', 'up'].includes(payload.action)) return;
        return options.send({...payload, monitor:monitor.id});
      }).catch(options.error);
      return chain;
    }
    function position(event, clamp = false) {
      const monitor = options.monitor();
      return mapPoint(image.getBoundingClientRect(), monitor.width, monitor.height,
        stage.classList.contains('fill'), event.clientX, event.clientY, clamp);
    }
    function cancel() {
      clearTimeout(timer);
      if (gesture?.dragging) send({action:'release'});
      gesture = null; pointers.clear(); scrollRemainder = 0;
    }
    function centroid() {
      const points = [...pointers.values()];
      return points.reduce((sum, point) => sum + point.y, 0) / points.length;
    }
    stage.addEventListener('pointerdown', event => {
      if (!options.enabled() || event.button !== 0) return;
      const point = position(event);
      if (!point) return;
      event.preventDefault(); stage.setPointerCapture(event.pointerId);
      pointers.set(event.pointerId, {x:event.clientX, y:event.clientY});
      if (pointers.size > 1) {
        clearTimeout(timer);
        if (gesture?.dragging) send({action:'release'});
        gesture = {scrolling:true, lastY:centroid(), point}; scrollRemainder = 0;
        return;
      }
      gesture = {point, lastPoint:point, startX:event.clientX, startY:event.clientY,
        dragging:false, longPress:false};
      timer = setTimeout(() => {
        if (!gesture || gesture.dragging || gesture.scrolling || !options.enabled()) return;
        gesture.longPress = true; send({action:'right_click', ...gesture.point});
      }, 550);
    });
    stage.addEventListener('pointermove', event => {
      if (!pointers.has(event.pointerId) || !gesture) return;
      event.preventDefault();
      if (!options.enabled()) { cancel(); return; }
      pointers.set(event.pointerId, {x:event.clientX, y:event.clientY});
      const point = position(event, true);
      if (!point) return;
      if (gesture.scrolling) {
        if (pointers.size < 2) return;
        const y = centroid(); scrollRemainder += (y - gesture.lastY) * 3;
        gesture.lastY = y;
        if (Math.abs(scrollRemainder) >= 40 && performance.now() - lastMove >= 35) {
          const delta = Math.max(-1200, Math.min(1200, Math.round(scrollRemainder)));
          send({action:'scroll', ...point, delta}); scrollRemainder = 0; lastMove = performance.now();
        }
        return;
      }
      if (gesture.longPress) return;
      if (!gesture.dragging && Math.hypot(event.clientX - gesture.startX, event.clientY - gesture.startY) >= 8) {
        clearTimeout(timer); gesture.dragging = true;
        send({action:'down', ...gesture.point});
      }
      gesture.lastPoint = point;
      if (gesture.dragging && performance.now() - lastMove >= 8) {
        send({action:'move', ...point}); lastMove = performance.now();
      }
    });
    stage.addEventListener('pointerup', event => {
      if (!pointers.has(event.pointerId) || !gesture) return;
      event.preventDefault(); clearTimeout(timer); pointers.delete(event.pointerId);
      if (gesture.scrolling) { if (!pointers.size) gesture = null; return; }
      const point = position(event, true) || gesture.lastPoint;
      if (gesture.dragging) send({action:'up', ...point});
      else if (!gesture.longPress && options.enabled()) send({action:'click', ...point});
      gesture = null;
    });
    stage.addEventListener('pointercancel', cancel);
    stage.addEventListener('lostpointercapture', event => { if(pointers.has(event.pointerId)) cancel(); });
    stage.addEventListener('contextmenu', event => {
      if (!options.enabled()) return;
      event.preventDefault();
      // A physical mouse right click also works; suppress native touch context menus.
      if (event.pointerType === 'mouse') {
        const point = position(event); if(point) send({action:'right_click', ...point});
      }
    });
    stage.addEventListener('wheel', event => {
      if (!options.enabled()) return;
      const point = position(event); if(!point) return;
      event.preventDefault(); send({action:'scroll', ...point,
        delta:Math.max(-1200, Math.min(1200, Math.round(-event.deltaY)))});
    }, {passive:false});
    // Keep a stationary drag alive, but let the server release it if this tab vanishes.
    const heartbeat = setInterval(() => { if(gesture?.dragging && options.enabled()) send({action:'hold'}); }, 500);
    return {cancel, dispose() {clearInterval(heartbeat); cancel();}};
  }
  const api = {mapPoint, attach};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.LocalTouch = api;
})(typeof window !== 'undefined' ? window : globalThis);
