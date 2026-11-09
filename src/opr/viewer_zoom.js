// Pinch-to-zoom and pan for a noVNC client, served by viewer.py as /zoom.js.
//
// noVNC has no local zoom of its own. On a phone it turns a pinch into
// ctrl+wheel inside the *remote* session and a two-finger drag into remote
// scrolling, which is useless when the whole desktop is 390px wide. Here the
// fingers zoom and pan the picture on the phone instead.
//
// How it works: the RFB draws into `target`, fitting the canvas to it
// (rfb.scaleViewport). Zooming is just making `target` bigger and translating
// it inside `stage` (overflow hidden), then poking rfb.scaleViewport so noVNC
// refits its canvas to the new size. noVNC keeps control of the canvas's CSS
// size, so its own mouse-coordinate math stays right at any zoom.
//
// noVNC's gesture handler sees the same touches we do (ours run in the capture
// phase, before it). For the duration of a two-finger zoom or pan the RFB is
// switched to viewOnly, so whatever noVNC makes of those touches is dropped
// before it is sent. One finger is untouched: tap, drag and long-press still
// reach the session. A two-finger tap (noVNC's right click) and, at 1x, a
// two-finger drag (noVNC's scroll) also still go through: we only take over
// once the fingers pinch, or drag while zoomed in.
//
// Options: `rfb` is a function returning the current RFB (the full client
// makes a new one on every reconnect); `win` is the window `stage` lives in;
// `viewOnly` says the page never sends input, which frees one finger to pan,
// a double-tap to toggle zoom, and ctrl+wheel (trackpad pinch) to zoom.
export function pinchZoom({ stage, target, rfb, win = window, viewOnly = false }) {
  const doc = stage.ownerDocument;
  const SLOP = 10;                       // px of movement before a touch means anything

  let z = 1, tx = 0, ty = 0;             // zoom and the target's offset inside the stage
  let W = 0, H = 0, ox = 0, oy = 0;      // stage size and position in the viewport
  let fitW = 0, fitH = 0, zmax = 4;      // the fitted (1x) size of the picture
  let pinch = null;                      // two fingers down
  let finger = null;                     // one finger down (viewOnly pages only)
  let taken = null;                      // { rfb, viewOnly } while the RFB is muted
  let lastTap = null;
  let raf = 0;

  stage.style.touchAction = 'none';
  stage.style.overflow = 'hidden';

  function measure() {
    const canvas = target.querySelector('canvas');
    const r = stage.getBoundingClientRect();
    W = r.width; H = r.height; ox = r.left; oy = r.top;
    if (!canvas || !canvas.width || !canvas.height || !W || !H) return false;
    const s = Math.min(W / canvas.width, H / canvas.height);
    fitW = canvas.width * s;
    fitH = canvas.height * s;
    zmax = Math.max(4, canvas.width / fitW);   // at least 4x, and at least 1:1
    if (!target.style.width) {
      // Not zoomed yet: noVNC centres the canvas in the full-size target,
      // so that is where our maths must start from.
      z = 1; tx = (W - fitW) / 2; ty = (H - fitH) / 2;
    }
    return true;
  }

  function apply() {
    if (raf) return;
    raf = win.requestAnimationFrame(() => {
      raf = 0;
      z = Math.min(Math.max(z, 1), zmax);
      const w = Math.round(fitW * z), h = Math.round(fitH * z);
      tx = w <= W ? (W - w) / 2 : Math.min(0, Math.max(W - w, tx));
      ty = h <= H ? (H - h) / 2 : Math.min(0, Math.max(H - h, ty));
      target.style.width = w + 'px';
      target.style.height = h + 'px';
      target.style.transform = `translate(${tx}px, ${ty}px)`;
      const r = rfb();
      if (r) r.scaleViewport = true;     // the setter always refits the canvas
    });
  }

  // Zoom to `zNew` keeping the picture under stage point (px, py) in place.
  function zoomAbout(zNew, px, py) {
    zNew = Math.min(Math.max(zNew, 1), zmax);
    tx = px - (px - tx) / z * zNew;
    ty = py - (py - ty) / z * zNew;
    z = zNew;
    apply();
  }

  function reset() {
    z = 1; tx = ty = 0; pinch = finger = null;
    target.style.width = target.style.height = target.style.transform = '';
    win.requestAnimationFrame(() => { const r = rfb(); if (r) r.scaleViewport = true; });
  }

  function take() {
    const r = rfb();
    if (r && !taken) { taken = { rfb: r, viewOnly: r.viewOnly }; r.viewOnly = true; }
  }

  function release() {
    if (!taken) return;
    const t = taken;
    taken = null;
    // noVNC handles this same touchend after us and may still flush a
    // delayed pointer move (17 ms); give it a moment before unmuting.
    win.setTimeout(() => { t.rfb.viewOnly = t.viewOnly; }, 50);
  }

  function point(t) { return { x: t.clientX - ox, y: t.clientY - oy }; }

  function pair(e) {
    const a = point(e.touches[0]), b = point(e.touches[1]);
    return { d: Math.hypot(a.x - b.x, a.y - b.y), x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  }

  function onTarget(e) {
    for (const t of e.touches) if (!target.contains(t.target)) return false;
    return true;
  }

  function onStart(e) {
    if (!onTarget(e)) return;
    if (e.touches.length === 2) {
      if (!measure()) return;
      finger = null;
      pinch = { ...pair(e), z0: z, tx0: tx, ty0: ty, taken: false };
      e.preventDefault();
    } else if (e.touches.length === 1 && viewOnly) {
      if (!measure()) return;
      const p = point(e.touches[0]);
      finger = { x: p.x, y: p.y, tx0: tx, ty0: ty, moved: false };
      e.preventDefault();
    }
  }

  function onMove(e) {
    if (pinch && e.touches.length >= 2) {
      const g = pair(e);
      if (!pinch.taken) {
        const pinched = Math.abs(g.d - pinch.d) >= SLOP;
        const dragged = Math.hypot(g.x - pinch.x, g.y - pinch.y) >= SLOP;
        if (!pinched && !(dragged && z > 1)) { e.preventDefault(); return; }
        take();
        pinch = { ...g, z0: z, tx0: tx, ty0: ty, taken: true };   // no jump: start from here
      }
      z = Math.min(Math.max(pinch.z0 * g.d / pinch.d, 1), zmax);
      tx = g.x - (pinch.x - pinch.tx0) / pinch.z0 * z;
      ty = g.y - (pinch.y - pinch.ty0) / pinch.z0 * z;
      apply();
      e.preventDefault();
    } else if (finger && e.touches.length === 1) {
      const p = point(e.touches[0]);
      if (Math.hypot(p.x - finger.x, p.y - finger.y) >= SLOP) finger.moved = true;
      if (finger.moved && z > 1) {
        tx = finger.tx0 + p.x - finger.x;
        ty = finger.ty0 + p.y - finger.y;
        apply();
      }
      e.preventDefault();
    }
  }

  function onEnd(e) {
    if (pinch && e.touches.length < 2) {
      pinch = null;
      if (viewOnly && e.touches.length === 1) {
        const p = point(e.touches[0]);
        finger = { x: p.x, y: p.y, tx0: tx, ty0: ty, moved: true };
      }
    }
    if (e.touches.length === 0) {
      const tap = finger && !finger.moved && e.changedTouches.length === 1
        ? point(e.changedTouches[0]) : null;
      finger = null;
      release();
      if (tap) doubleTap(tap, e.timeStamp);
    }
  }

  function doubleTap(p, now) {
    if (lastTap && now - lastTap.t < 300 && Math.hypot(p.x - lastTap.x, p.y - lastTap.y) < 30) {
      lastTap = null;
      zoomAbout(z > 1 ? 1 : 2.5, p.x, p.y);
    } else {
      lastTap = { t: now, x: p.x, y: p.y };
    }
  }

  function onWheel(e) {
    if (!target.contains(e.target) || !measure()) return;
    const p = { x: e.clientX - ox, y: e.clientY - oy };
    if (e.ctrlKey) {                      // a trackpad pinch arrives as ctrl+wheel
      zoomAbout(z * Math.exp(-e.deltaY / 200), p.x, p.y);
    } else if (z > 1) {
      tx -= e.deltaX; ty -= e.deltaY;
      apply();
    } else {
      return;
    }
    e.preventDefault();
  }

  const opts = { capture: true, passive: false };
  doc.addEventListener('touchstart', onStart, opts);
  doc.addEventListener('touchmove', onMove, opts);
  doc.addEventListener('touchend', onEnd, opts);
  doc.addEventListener('touchcancel', onEnd, opts);
  if (viewOnly) doc.addEventListener('wheel', onWheel, opts);
  win.addEventListener('resize', reset);   // rotate, keyboard: back to fit

  return { reset, get zoom() { return z; } };
}
