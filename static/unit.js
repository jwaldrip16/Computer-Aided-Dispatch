let ARMED = false, AC = null, OSC = [], VOICE_T = null;
let ON_DUTY = false, WATCH = null, LAST_PING = 0;
const seen = new Set();
let firstLoad = true;
const $ = id => document.getElementById(id);

function speak(text) {
  speakMany([text]);
}

function speakMany(texts) {
  if (!('speechSynthesis' in window)) return;
  try {
    speechSynthesis.cancel();
    for (const t of texts) {
      const u = new SpeechSynthesisUtterance(t);
      u.rate = 0.95;
      speechSynthesis.speak(u);
    }
  } catch (e) {}
}

function arm() {
  const note = $('armNote');
  let canSpeak = 'speechSynthesis' in window;
  try {
    AC = new (window.AudioContext || window.webkitAudioContext)();
    if (AC.resume) { try { AC.resume(); } catch (e) {} }
  } catch (e) {
    ARMED = false;
    note.textContent = 'This phone cannot play tones in the browser. Try Chrome or update it.';
    return;
  }
  ARMED = true;
  beep(1000, 0, 0.35);
  beep(1200, 0.5, 0.35);
  if (canSpeak) {
    try {
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance('Tone and voice are on.');
      speechSynthesis.speak(u);
    } catch (e) { canSpeak = false; }
  }
  $('armBanner').classList.add('hidden');
  if (!canSpeak) note.textContent =
    'Tones are on, but this phone does not support spoken voice.';
  $('silenceBtn').disabled = false;
  setTimeout(() => { if (!ALERTING) $('silenceBtn').disabled = true; }, 2000);
}

function beep(freq, start, dur) {
  const o = AC.createOscillator(), g = AC.createGain();
  o.type = 'sine';
  o.frequency.value = freq;
  const t = AC.currentTime + start;
  g.gain.setValueAtTime(0.0001, t);
  g.gain.linearRampToValueAtTime(0.6, t + 0.05);
  g.gain.setValueAtTime(0.6, t + dur - 0.05);
  g.gain.linearRampToValueAtTime(0.0001, t + dur);
  o.connect(g); g.connect(AC.destination);
  o.start(t); o.stop(t + dur);
  OSC.push(o);
}

function toneFor(ctype, emergency) {
  if (!ARMED) return;
  const cycles = emergency ? 4 : 3;
  let secs = 0;
  for (let i = 0; i < cycles; i++) {
    if (ctype === 'police') {
      beep(1250, secs, 0.5);
      beep(800, secs + 0.6, 0.5);
      secs += 1.4;
    } else if (ctype === 'fire') {
      for (let k = 0; k < 4; k++) { beep(950, secs, 0.25); secs += 0.45; }
      secs += 0.2;
    } else if (ctype === 'medical') {
      for (let k = 0; k < 3; k++) { beep(620, secs, 0.5); secs += 0.9; }
    } else {
      beep(1000, secs, 0.7);
      beep(1200, secs + 0.8, 0.7);
      secs += 1.6;
    }
  }
  return secs;
}

function stopAlert() {
  OSC.forEach(o => { try { o.stop(); } catch (e) {} });
  OSC = [];
  ALERTING = false;
  if (VOICE_T) { clearTimeout(VOICE_T); VOICE_T = null; }
  try { speechSynthesis.cancel(); } catch (e) {}
  $('silenceBtn').disabled = true;
}

const TITLES = {police: 'Police', fire: 'Fire', medical: 'Medical', other: 'Call'};

function callSpeech(c) {
  const parts = [];
  if (FMT.say_type) parts.push(TITLES[c.call_type] + ' call');
  parts.push('Address: ' + (c.address == null ? '' : c.address));
  if (FMT.say_nature) parts.push('Nature: ' + (c.nature || ''));
  if (FMT.say_time) parts.push('Dispatched at ' +
    new Date(c.notified_at || c.created_at).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}));
  return parts.join('. ') + '.';
}

let ALERTING = false;

function alertCall(c) {
  stopAlert();
  ALERTING = true;
  $('silenceBtn').disabled = false;
  const secs = toneFor(c.call_type, c.priority === 'emergency');
  if (secs) {
    VOICE_T = setTimeout(() => {
      speakAlert(c);
      $('silenceBtn').disabled = true;
      ALERTING = false;
    }, secs * 1000);
  }
}

function nowTime() {
  return new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}

function speakAlert(c) {
  const s = callSpeech(c);
  const again = 'Address: ' + (c.address == null ? '' : c.address) + '.'
    + ' Nature: ' + (c.nature == null ? '' : c.nature) + '.';
  speakMany([s, again, 'The time is ' + nowTime() + '.']);
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch])); }
function escAttr(s) { return esc(s).replace(/'/g, '&#39;'); }
function fmt(iso) { return iso ? new Date(iso).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : ''; }

function cardHtml(c) {
  return '<div class="card unitcard row-' + c.id + '">' +
    '<div class="row spread">' +
    '<h2><span class="tag ' + c.call_type + '">' + c.call_type + '</span> ' + esc(c.nature) + '</h2>' +
    '<button class="btn read" data-id="' + c.id + '" data-addr="' + escAttr(c.address) + '" data-nat="' + escAttr(c.nature) + '" data-type="' + c.call_type + '" data-time="' + escAttr(c.notified_at || c.created_at || '') + '">Read</button>' +
    '</div>' +
    '<p><b>Address:</b> ' + esc(c.address) + '</p>' +
    '<p><b>Priority:</b> <span class="tag p-' + c.priority + '">' + c.priority + '</span>' +
    ' <b>Status:</b> ' + c.status + ' <b>Units:</b> ' + esc(c.units || '') + '</p>' +
    '<p class="muted">Came in ' + fmt(c.created_at) + '</p>' +
    '<div class="row respbar">' +
    (c.my_status ? '<span class="tag">' + c.my_status.replace('_', ' ') + '</span>' : '') +
    '<button class="btn resp" data-act="enroute" data-id="' + c.id + '">Enroute</button>' +
    '<button class="btn resp" data-act="on_scene" data-id="' + c.id + '">On scene</button>' +
    '<button class="btn resp" data-act="transporting" data-id="' + c.id + '">Transport</button>' +
    '<button class="btn resp" data-act="clear" data-id="' + c.id + '">Clear</button>' +
    '<a class="btn nav" target="_blank" rel="noopener" href="https://www.google.com/maps/dir/?api=1&destination=' +
      encodeURIComponent((c.addr_lat != null && c.addr_lng != null) ? (c.addr_lat + ',' + c.addr_lng) : c.address) +
    '">Navigate</a>' +
    '</div>' +
    '<div class="row reasonrow hidden" id="reason-' + c.id + '">' +
    '<input id="reasonInput-' + c.id + '" placeholder="Why are you clearing? (not required)">' +
    '</div>' +
    '</div>';
}

function keepReasons() {
  const keep = {};
  document.querySelectorAll('.reasonrow:not(.hidden) input').forEach(i => {
    keep[i.id.split('-')[1]] = i.value;
  });
  return keep;
}
function restoreReasons(keep) {
  for (const id in keep) {
    const rr = document.getElementById('reason-' + id);
    const inp = document.getElementById('reasonInput-' + id);
    if (rr && inp) { rr.classList.remove('hidden'); inp.value = keep[id]; }
  }
}

async function poll() {
  const keep = keepReasons();
  try {
    const r = await fetch('/api/unit-board');
    if (r.status === 401) { location.href = '/login'; return; }
    const d = await r.json();
    const nt = document.getElementById('noTypes');
    if (nt) nt.classList.toggle('hidden', !d.no_types);
    ON_DUTY = d.me_status === 'on_duty';
    const btn = $('dutyBtn');
    btn.textContent = ON_DUTY ? 'Go off duty' : 'Go on duty';
    btn.classList.toggle('primary', !ON_DUTY);
    $('locNote').textContent = ON_DUTY
      ? 'You are on duty. Dispatch can see your location.'
      : 'You are off duty. Dispatch cannot see your location.';
    $('activeCards').innerHTML = d.active.map(cardHtml).join('');
    restoreReasons(keep);
    $('noActive').classList.toggle('hidden', d.active.length > 0);
    $('recentList').innerHTML = d.recent.map(c =>
      '<li>' + fmt(c.created_at) + ' <span class="tag ' + c.call_type + '">' + c.call_type + '</span> ' +
      esc(c.nature) + ', ' + esc(c.address) + '</li>').join('') ||
      '<li class="muted">None yet.</li>';
    const fresh = [];
    for (const c of d.active) {
      const key = c.id + ':' + (c.page_count || 0);
      if (!seen.has(key)) {
        seen.add(key);
        if (!firstLoad) {
          document.querySelectorAll('.row-' + c.id).forEach(x => x.classList.add('flash'));
          fresh.push(c);
        }
      }
    }
    if (fresh.length) alertCall(fresh[0]);
    firstLoad = false;
  } catch (e) {}
}

$('armBtn').addEventListener('click', arm);

$('silenceBtn').addEventListener('click', stopAlert);

document.addEventListener('click', e => {
  const b = e.target.closest('button.read');
  if (b) {
    const parts = [];
    if (FMT.say_type) parts.push(TITLES[b.dataset.type] + ' call');
    parts.push('Address: ' + b.dataset.addr);
    if (FMT.say_nature) parts.push('Nature: ' + b.dataset.nat);
    if (FMT.say_time) parts.push('Dispatched at ' +
      new Date(b.dataset.time).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}));
    const s = parts.join('. ') + '.';
    const again = 'Address: ' + b.dataset.addr + '.'
      + ' Nature: ' + b.dataset.nat + '.';
    speakMany([s, again, 'The time is ' + nowTime() + '.']);
  }
});

$('dutyForm').addEventListener('submit', async e => {
  e.preventDefault();
  const next = ON_DUTY ? 'off_duty' : 'on_duty';
  const r = await fetch('/api/unit/status', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: next})
  });
  if (!r.ok) return;
  if (next === 'on_duty' && navigator.geolocation) {
    if (WATCH) navigator.geolocation.clearWatch(WATCH);
    WATCH = navigator.geolocation.watchPosition(sendFix, () => {}, {enableHighAccuracy: true, timeout: 20000});
  } else {
    if (WATCH) { navigator.geolocation.clearWatch(WATCH); WATCH = null; }
  }
  poll();
});

function sendFix(pos) {
  if (!ON_DUTY) return;
  const now = Date.now();
  if (now - LAST_PING < 10000) return;
  LAST_PING = now;
  fetch('/api/unit/status', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({lat: pos.coords.latitude, lng: pos.coords.longitude,
                          accuracy: pos.coords.accuracy})
  });
}

poll();
setInterval(poll, 4000);

document.addEventListener('click', async e => {
  const b = e.target.closest('button.resp');
  if (!b) return;
  const id = b.dataset.id, act = b.dataset.act;
  if (act === 'clear' && !b.dataset.conf) {
    const rr = document.getElementById('reason-' + id);
    if (rr) { rr.classList.remove('hidden'); document.getElementById('reasonInput-' + id).focus(); }
    b.dataset.conf = '1';
    b.textContent = 'Send clear';
    return;
  }
  const inp = document.getElementById('reasonInput-' + id);
  const reason = inp ? inp.value : '';
  b.disabled = true;
  try {
    const r = await fetch('/api/unit/call-status', { method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({call_id: Number(id), status: act, reason: reason}) });
    if (r.ok) { poll(); } else { b.disabled = false; b.textContent = 'Try again'; }
  } catch (err) { b.disabled = false; b.textContent = 'Try again'; }
});
