"""Self-contained live dashboard for the Calmi gateway."""

from __future__ import annotations

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calmi Gateway</title>
<style>
  :root{
    --bg:#080d14; --panel:#0f1723; --panel2:#131f2e; --line:#1e2c3d;
    --ink:#e6eef7; --muted:#8aa0b8; --accent:#2dd4bf; --accent2:#38bdf8;
    --warn:#fbbf24; --bad:#f87171; --good:#4ade80;
  }
  *{box-sizing:border-box}
  body{margin:0;background:radial-gradient(1200px 600px at 15% -10%,#10263a 0%,var(--bg) 60%);
       color:var(--ink);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
  header{position:sticky;top:0;z-index:5;backdrop-filter:blur(10px);
    background:rgba(8,13,20,.82);border-bottom:1px solid var(--line)}
  .bar{max-width:1280px;margin:0 auto;padding:16px 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  .logo{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent2));
    display:grid;place-items:center;color:#04211d;font-weight:800;font-size:16px}
  h1{font-size:18px;margin:0;letter-spacing:.2px}
  .sub{color:var(--muted);font-size:12px}
  .pill{margin-left:auto;display:flex;align-items:center;gap:8px;padding:7px 12px;border-radius:999px;
    background:var(--panel);border:1px solid var(--line);font-size:12px;color:var(--muted)}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--good);box-shadow:0 0 0 4px rgba(74,222,128,.15)}
  .dot.off{background:var(--bad);box-shadow:0 0 0 4px rgba(248,113,113,.15)}
  main{max-width:1280px;margin:0 auto;padding:22px 20px 60px}
  .grid{display:grid;gap:14px;grid-template-columns:repeat(12,1fr)}
  .card{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
    border-radius:16px;padding:16px 18px}
  .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.12em}
  .v{font-size:28px;font-weight:700;margin-top:6px;font-variant-numeric:tabular-nums}
  .v small{font-size:13px;color:var(--muted);font-weight:500;margin-left:4px}
  .span3{grid-column:span 3}.span4{grid-column:span 4}.span6{grid-column:span 6}
  .span8{grid-column:span 8}.span12{grid-column:span 12}
  h2{font-size:13px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin:0 0 12px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{color:var(--muted);font-weight:600;text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);
    font-size:11px;text-transform:uppercase;letter-spacing:.08em}
  td{padding:9px 10px;border-bottom:1px solid rgba(30,44,61,.6);vertical-align:top}
  tr:last-child td{border-bottom:0}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
  .tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;border:1px solid var(--line);
    background:#0b1420;color:var(--muted)}
  .tag.good{color:var(--good);border-color:rgba(74,222,128,.35);background:rgba(74,222,128,.08)}
  .tag.bad{color:var(--bad);border-color:rgba(248,113,113,.35);background:rgba(248,113,113,.08)}
  .spec{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 18px}
  .spec div{display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px dashed rgba(30,44,61,.7)}
  .spec span:first-child{color:var(--muted)}
  .spec span:last-child{text-align:right;color:var(--ink)}
  .events{max-height:340px;overflow:auto}
  .ev{display:flex;gap:10px;padding:7px 0;border-bottom:1px solid rgba(30,44,61,.5);font-size:12px}
  .ev time{color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}
  .ev b{font-weight:600;min-width:80px}
  .empty{color:var(--muted);padding:14px 0;font-size:13px}
  footer{max-width:1280px;margin:0 auto;padding:0 20px 30px;color:var(--muted);font-size:12px}
  @media (max-width:900px){.span3,.span4{grid-column:span 6}.span8,.span6{grid-column:span 12}}
  @media (max-width:560px){.span3,.span4{grid-column:span 12}.spec{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><div class="bar">
  <div class="logo">C</div>
  <div><h1>Calmi Gateway</h1><div class="sub" id="sub">connecting…</div></div>
  <div class="pill"><span class="dot" id="live"></span><span id="liveText">live</span></div>
</div></header>
<main>
  <div class="grid">
    <div class="card span3"><div class="k">Devices online</div><div class="v" id="connections">–</div></div>
    <div class="card span3"><div class="k">Data received</div><div class="v" id="in">–<small>MB</small></div></div>
    <div class="card span3"><div class="k">Data sent</div><div class="v" id="out">–<small>MB</small></div></div>
    <div class="card span3"><div class="k">Replies</div><div class="v" id="turns">–</div></div>

    <div class="card span4"><div class="k">Time to first audio</div><div class="v" id="ttfa">–<small>ms</small></div></div>
    <div class="card span4"><div class="k">Average turn</div><div class="v" id="total">–<small>ms</small></div></div>
    <div class="card span4"><div class="k">Audio per reply</div><div class="v" id="audio">–<small>s</small></div></div>

    <div class="card span8">
      <h2>Sessions</h2>
      <table><thead><tr><th>Device</th><th>Connected</th><th>In</th><th>Out</th><th>Turns</th><th>State</th></tr></thead>
      <tbody id="sessions"><tr><td colspan="6" class="empty">No device connected</td></tr></tbody></table>
    </div>
    <div class="card span4">
      <h2>Service</h2>
      <div class="spec" id="spec"></div>
    </div>

    <div class="card span12">
      <h2>Wellness readings</h2>
      <table><thead><tr><th>When</th><th>Device</th><th>BPM</th><th>SpO2</th><th>Quality</th><th>Duration</th></tr></thead>
      <tbody id="readings"><tr><td colspan="6" class="empty">No readings stored yet</td></tr></tbody></table>
    </div>

    <div class="card span12">
      <h2>Live events</h2>
      <div class="events" id="events"><div class="empty">No events yet</div></div>
    </div>
  </div>
</main>
<footer id="footer"></footer>
<script>
const $ = (id) => document.getElementById(id);
const mb = (n) => (n / 1048576).toFixed(2);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ago = (s) => { s = Math.max(0, s|0); if (s < 60) return s + 's'; if (s < 3600) return Math.floor(s/60) + 'm ' + (s%60) + 's'; return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm'; };
const bytes = (n) => n < 1024 ? n + ' B' : n < 1048576 ? (n/1024).toFixed(1) + ' KB' : mb(n) + ' MB';

async function refresh() {
  try {
    const res = await fetch('/api/status', {cache:'no-store'});
    if (!res.ok) throw new Error(res.status);
    const s = await res.json();
    $('live').className = 'dot';
    $('liveText').textContent = 'live';
    $('sub').textContent = 'up ' + ago(s.uptime_s) + ' · v' + s.version + ' · ' + s.server_time;
    $('connections').textContent = s.totals.connections;
    $('in').innerHTML = mb(s.totals.bytes_in) + '<small>MB</small>';
    $('out').innerHTML = mb(s.totals.bytes_out) + '<small>MB</small>';
    $('turns').textContent = s.totals.turns;
    $('ttfa').innerHTML = s.latency.ttfa_ms + '<small>ms</small>';
    $('total').innerHTML = s.latency.total_ms + '<small>ms</small>';
    $('audio').innerHTML = (s.latency.audio_ms/1000).toFixed(1) + '<small>s</small>';

    const rows = s.sessions.map(x => `<tr>
      <td><b>${esc(x.device)}</b><div class="mono" style="color:var(--muted)">${esc(x.client)}</div></td>
      <td>${ago(x.uptime_s)} ago</td><td class="mono">${bytes(x.bytes_in)}</td><td class="mono">${bytes(x.bytes_out)}</td>
      <td>${x.turns}</td><td><span class="tag ${x.authorized?'good':'bad'}">${x.authorized?'authorized':'pending'}</span></td>
    </tr>`).join('');
    $('sessions').innerHTML = rows || '<tr><td colspan="6" class="empty">No device connected</td></tr>';

    const spec = {
      'Mode': s.service.mode, 'STT': s.service.stt, 'LLM': s.service.llm, 'TTS': s.service.tts,
      'Sample rate': s.service.sample_rate + ' Hz', 'Frame': s.service.frame_ms + ' ms',
      'TTS pace': s.service.tts_pace, 'Prebuffer': s.service.tts_prebuffer + ' s',
      'Device token': s.service.device_token_set ? 'required' : 'open',
      'History DB': s.service.database ? 'connected' : 'off / unavailable',
      'Wake word': s.service.wake_word,
      'Actions': s.totals.actions, 'Health reports': s.totals.health_reports,
      'Errors': s.totals.errors,
    };
    $('spec').innerHTML = Object.entries(spec).map(([k,v]) =>
      `<div><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join('');

    $('events').innerHTML = (s.events||[]).map(e =>
      `<div class="ev"><time>${esc((e.at||'').slice(11,19))}</time><b>${esc(e.kind)}</b><span>${esc(e.detail)}</span></div>`
    ).join('') || '<div class="empty">No events yet</div>';

    $('footer').textContent = `Connections total ${s.totals.connections} · wake checks ${s.totals.wake_checks} (${s.totals.wake_hits} hits) · readings refresh every 5 s`;
  } catch (err) {
    $('live').className = 'dot off';
    $('liveText').textContent = 'offline';
  }
}

async function readings() {
  try {
    const res = await fetch('/api/readings?limit=25', {cache:'no-store'});
    if (!res.ok) return;
    const data = await res.json();
    const rows = (data.readings||[]).map(r => `<tr>
      <td class="mono">${esc((r.recorded_at||'').replace('T',' ').slice(0,19))}</td>
      <td>${esc(r.device)}</td><td><b>${r.bpm}</b> bpm</td><td>${r.spo2 ?? '—'}</td>
      <td>${r.bpm_quality ?? '—'}</td><td>${r.duration_s ?? '—'} s</td></tr>`).join('');
    $('readings').innerHTML = rows || '<tr><td colspan="6" class="empty">No readings stored yet</td></tr>';
  } catch (e) {}
}

refresh(); readings();
setInterval(refresh, 2000);
setInterval(readings, 5000);
</script>
</body></html>
"""
