// Keeps a viewer page honest about whether its noVNC client is really live.
//
// A VNC server only sends when the screen changes, so a quiet connection
// and a dead one look the same: the page keeps the last picture and nothing
// says it is stale. That happens whenever the socket dies without a close
// event — a laptop sleeping, Wi-Fi changing, iOS freezing a backgrounded
// page, a proxy dropping the stream. And a handshake that never finishes
// holds the browser's one "connecting" WebSocket slot for the host, which
// leaves every other viewer tab on "Connecting…" behind it.
//
// So, while the page is visible:
//   - every `every` ms, and at once on returning to the page, ask the server
//     for one pixel (a non-incremental FramebufferUpdateRequest for 1x1,
//     which it must answer whatever the screen is doing); no bytes back
//     within `grace` ms means the connection is dead;
//   - a page not connected for `connectTimeout` ms (stuck handshake, or
//     noVNC retrying in a loop) is dead too.
// A dead client is handed to `onDead(rfb)`, which reconnects.
//
// `onEnded()` is called instead when the session itself is gone (removed,
// or its X server exited), so a tab left open on it stops knocking.
//
// `rfb` is a getter: noVNC makes a new client on every reconnect.
// `sessionState(name)` is exported for pages that retry on their own.

export async function sessionState(name) {
  try {
    const res = await fetch('api/sessions', { cache: 'no-store' });
    const s = (await res.json()).sessions.find(r => r.name === name);
    return !s ? 'gone' : s.alive ? 'up' : 'dead';
  } catch (e) {
    return 'unknown';           // the viewer itself is unreachable: keep trying
  }
}

export function keepAlive({ name, rfb, onDead, onEnded,
                            every = 15000, grace = 5000, connectTimeout = 10000 }) {
  let hooked = null, rx = 0, probing = false;

  const state = r => (r && r._rfbConnectionState) || '';

  function hook(r) {
    const ws = r && r._sock && r._sock._websocket;
    if (!ws || hooked === ws) return;
    hooked = ws;
    ws.addEventListener('message', () => { rx++; });
  }

  async function declareDead(r) {
    const s = await sessionState(name);
    if (s === 'gone' || s === 'dead') { stop(); onEnded && onEnded(s); return; }
    if (!document.hidden) onDead(r);   // a hidden page reconnects when it is shown
  }

  function probe() {
    const r = rfb();
    if (!r || probing || document.hidden || state(r) !== 'connected') return;
    hook(r);
    const before = rx;
    try {
      r.constructor.messages.fbUpdateRequest(r._sock, false, 0, 0, 1, 1);
    } catch (e) {
      declareDead(r);
      return;
    }
    probing = true;
    setTimeout(() => {
      probing = false;
      if (rfb() === r && state(r) === 'connected' && rx === before) declareDead(r);
    }, grace);
  }

  // Not connected for too long, whether stuck in one handshake or cycling
  // through noVNC's own quick retries against a session that has gone.
  let downSince = 0;
  function watchConnecting() {
    const r = rfb();
    if (state(r) === 'connected') { downSince = 0; return; }
    if (!downSince) { downSince = Date.now(); return; }
    // Checked even while hidden, so a background tab on a removed session
    // stops too; declareDead only reconnects a visible page.
    if (Date.now() - downSince > connectTimeout) {
      downSince = Date.now();   // give the replacement its own full window
      declareDead(r);
    }
  }

  const timers = [setInterval(probe, every), setInterval(watchConnecting, 1000)];
  const onVisible = () => { if (!document.hidden) probe(); };
  const onShow = e => { if (e.persisted) probe(); };
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('pageshow', onShow);

  function stop() {
    timers.forEach(clearInterval);
    document.removeEventListener('visibilitychange', onVisible);
    window.removeEventListener('pageshow', onShow);
  }
  return { stop, probe };
}
