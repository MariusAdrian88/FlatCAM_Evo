# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/26/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

# The page of the web pendant, a constant so that a frozen build needs no data file.
PAGE_HTML = b"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>FlatCAM Pendant</title>
<style>
:root { --bg:#16181c; --fg:#e8eaed; --card:#23272e; --ok:#2e7d32; --warn:#f9a825; --bad:#c62828; --btn:#37404d; }
* { box-sizing: border-box; }
body { margin:0; padding:10px; background:var(--bg); color:var(--fg); font:16px system-ui, sans-serif; }
.card { background:var(--card); border-radius:8px; padding:10px; margin-bottom:10px; }
#state { font-size:26px; font-weight:bold; }
#alarm { color:var(--bad); min-height:1.2em; }
.pos { font-family:monospace; font-size:18px; display:grid; grid-template-columns:auto 1fr 1fr; gap:2px 12px; }
.row { display:flex; gap:8px; margin-bottom:8px; }
button { flex:1; min-height:56px; font-size:18px; border:0; border-radius:8px; color:#fff; background:var(--btn); touch-action:manipulation; }
button:active { filter:brightness(1.4); }
.hold { background:var(--warn); color:#000; } .resume { background:var(--ok); }
.stop { background:#6d4c41; } .estop { background:var(--bad); font-weight:bold; }
.pad { display:grid; grid-template-columns:repeat(4,1fr); gap:6px; }
.pad button { min-height:52px; }
input { width:100%; font-size:18px; padding:8px; border-radius:6px; border:0; }
#offline { display:none; background:var(--bad); text-align:center; padding:8px; border-radius:8px; margin-bottom:10px; font-weight:bold; }
.dim { opacity:.6; }
</style>
</head>
<body>
<div id="offline">Not connected</div>
<div class="card">
  <div id="state">-</div>
  <div id="alarm"></div>
  <div class="dim" id="machine"></div>
</div>
<div class="card">
  <div class="pos" id="pos"></div>
</div>
<div class="card">
  <div id="progress">-</div>
  <div class="dim" id="times"></div>
  <div class="dim" id="ovs"></div>
</div>
<div class="row">
  <button class="hold" data-a="hold">Hold</button>
  <button class="resume" data-a="resume">Resume</button>
</div>
<div class="row">
  <button class="stop" data-a="stop">Stop</button>
  <button class="estop" data-a="estop">E-Stop</button>
</div>
<div class="row">
  <button data-a="feed_ov" data-v="-10">Feed -10</button>
  <button data-a="feed_ov" data-v="100">Feed 100</button>
  <button data-a="feed_ov" data-v="+10">Feed +10</button>
</div>
<div class="card" id="motion" style="display:none">
  <label>Step <input id="step" type="number" inputmode="decimal" min="0.001" step="any" value="1"></label>
  <div class="pad" style="margin-top:8px">
    <button data-j="xminus_yplus">X- Y+</button><button data-j="yplus">Y+</button>
    <button data-j="xplus_yplus">X+ Y+</button><button data-j="zplus">Z+</button>
    <button data-j="xminus">X-</button><button data-z="all">Zero all</button>
    <button data-j="xplus">X+</button><button data-j="zminus">Z-</button>
    <button data-j="xminus_yminus">X- Y-</button><button data-j="yminus">Y-</button>
    <button data-j="xplus_yminus">X+ Y-</button><button data-j="aplus">A+</button>
    <button data-z="x">Zero X</button><button data-z="y">Zero Y</button>
    <button data-z="z">Zero Z</button><button data-j="aminus">A-</button>
  </div>
  <div class="row" style="margin-top:8px">
    <button data-z="a">Zero A</button>
    <button data-a="home">Home</button>
  </div>
</div>
<script>
var token = new URLSearchParams(location.search).get('t') || '';
var $ = function (id) { return document.getElementById(id); };
function api(path, body) {
  var opt = { headers: { 'X-Token': token } };
  if (body) { opt.method = 'POST'; opt.body = JSON.stringify(body); }
  return fetch(path, opt).then(function (r) {
    if (!r.ok) { throw new Error('http ' + r.status); }
    return r.json();
  });
}
function send(body) { api('/cmd', body).catch(function () { $('offline').style.display = 'block'; }); }
function fmt(n) { return (typeof n === 'number') ? n.toFixed(3) : '-'; }
function clock(s) {
  if (typeof s !== 'number' || s < 0) { return '-'; }
  s = Math.round(s);
  return Math.floor(s / 3600) + ':' + ('0' + Math.floor(s / 60) % 60).slice(-2) + ':' + ('0' + s % 60).slice(-2);
}
function show(s) {
  $('offline').style.display = 'none';
  $('state').textContent = s.connected ? (s.state || '-') : 'Not connected';
  $('alarm').textContent = s.alarm || '';
  $('machine').textContent = (s.units || '') + (s.wcs ? '  ' + s.wcs : '');
  var w = s.wpos || [], m = s.mpos || [], names = ['X', 'Y', 'Z', 'A'], rows = '';
  var n = (w.length === 4 || m.length === 4) ? 4 : 3;
  for (var i = 0; i < n; i++) {
    rows += '<span>' + names[i] + '</span><span>' + fmt(w[i]) + '</span><span class="dim">' + fmt(m[i]) + '</span>';
  }
  $('pos').innerHTML = rows;
  $('progress').textContent = 'Lines ' + (s.done == null ? '-' : s.done) + ' / ' + (s.total == null ? '-' : s.total);
  $('times').textContent = 'Elapsed ' + clock(s.elapsed) + '   Remaining ' + clock(s.remaining);
  $('ovs').textContent = 'Feed ' + (s.feed_ov == null ? '-' : s.feed_ov) + '%  Rapid ' +
    (s.rapid_ov == null ? '-' : s.rapid_ov) + '%  Spindle ' + (s.spindle_ov == null ? '-' : s.spindle_ov) + '%';
  $('motion').style.display = s.motion === true ? 'block' : 'none';
}
var polling = false;
function poll() {
  if (polling) { return; }
  polling = true;
  api('/state').then(show).catch(function () {
    $('offline').style.display = 'block';
    $('state').textContent = 'Not connected';
  }).then(function () { polling = false; });
}
document.addEventListener('click', function (e) {
  var b = e.target.closest('button');
  if (!b) { return; }
  if (b.dataset.a === 'feed_ov') { send({ action: 'feed_ov', value: b.dataset.v }); }
  else if (b.dataset.a) { send({ action: b.dataset.a }); }
  else if (b.dataset.j) {
    var step = parseFloat($('step').value);
    if (step > 0) { send({ action: 'jog', direction: b.dataset.j, step: step }); }
  } else if (b.dataset.z) { send({ action: 'zero', axis: b.dataset.z }); }
});
poll();
setInterval(poll, 250);
</script>
</body>
</html>
"""
