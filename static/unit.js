let ARMED = false, AC = null, OSC = [], VOICE_T = null, PAGE_AUDIO = null, AUDIO_READY = false;
let ME_NAME = '';
let PAGE_QUEUE = [];
let ON_DUTY = false, WATCH = null, WATCH_HIGH = false, LAST_PING = 0;
let LOC_ERR = '', DEGRADED = false, PERM_BLOCKED = false;
const seen = new Set();
let firstLoad = true;
const $ = id => document.getElementById(id);

function speak(text) {
  speakMany([text]);
}

function spAddr(a) {
  return String(a == null ? '' : a)
    .replace(/,?\s*(United States( of America)?|U\.S\.A\.?|USA)\s*$/i, '')
    .trim().replace(/,+$/, '');
}

let _VOICE = null;
const VOICE_PREFS = ['Karen', 'Moira', 'Tessa', 'Nicky', 'Catherine', 'Serena', 'Fiona', 'Victoria', 'Google US English', 'Google UK English Female'];
function pickVoice() {
  try {
    const vs = speechSynthesis.getVoices() || [];
    if (!vs.length) return _VOICE;
    const low = n => (n || '').toLowerCase();
    if (SVOICE) {
      const m = vs.find(v => low(v.name) == low(SVOICE));
      if (m) { _VOICE = m; return m; }
    }
    const en = vs.filter(v => low(v.lang).indexOf('en') == 0
      && !/samantha|fred/i.test(v.name));
    for (let i = 0; i < VOICE_PREFS.length; i++) {
      const m = en.find(v => low(v.name).indexOf(low(VOICE_PREFS[i])) == 0);
      if (m) { _VOICE = m; return m; }
    }
    const g = en.find(v => /female|google/i.test(low(v.name)));
    if (g) { _VOICE = g; return g; }
    _VOICE = en[0] || vs.find(v => !/samantha/i.test(v.name)) || null;
    return _VOICE;
  } catch (e) { return null; }
}
try { speechSynthesis.addEventListener('voiceschanged', pickVoice); } catch (e) {}

// iPhones block sound that starts by itself until the screen is touched once.
// The first tap plays a silent file through the shared page audio element,
// which unlocks it so the next page's recording can start on its own.
let _soundNote = null;
function hideSoundNote() { if (_soundNote) { try { _soundNote.remove(); } catch (e) {} _soundNote = null; } }
function showSoundNote() {
  if (_soundNote || AUDIO_READY) return;
  _soundNote = document.createElement('div');
  _soundNote.textContent = 'Tap the screen once so pages can play their sound.';
  _soundNote.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:60;background:#fff3cd;color:#5c4400;padding:8px 12px;font-size:13px;text-align:center;border-top:1px solid #e6d68a;';
  document.body.appendChild(_soundNote);
}
function unlockAudio() {
  if (AUDIO_READY || ALERTING) return;
  try {
    if (!PAGE_AUDIO) PAGE_AUDIO = new Audio('/static/silence.wav');
    const p = PAGE_AUDIO.play();
    if (p && p.then) p.then(() => { AUDIO_READY = true; hideSoundNote(); }).catch(() => {});
    try {
      const u = new SpeechSynthesisUtterance(' ');
      u.volume = 0;
      speechSynthesis.speak(u);
    } catch (e2) {}
  } catch (e) {}
}
['touchend', 'click'].forEach(ev => document.addEventListener(ev, unlockAudio, { passive: true }));
setTimeout(showSoundNote, 1200);

function speakMany(texts, onDone) {
  if (!('speechSynthesis' in window)) { if (onDone) setTimeout(onDone, 800); return; }
  try {
    speechSynthesis.cancel();
    let last = null, fired = false;
    const done = () => { if (fired) return; fired = true; if (onDone) onDone(); };
    for (const t of texts) {
      const u = new SpeechSynthesisUtterance(t);
      u.rate = 0.95;
      u.voice = pickVoice();
      last = u;
      speechSynthesis.speak(u);
    }
    if (last) {
      last.onend = done;
      // if the phone never fires onend, move on after a generous estimate
      const est = texts.reduce((a, t) => a + t.length, 0) * 0.085 + 4;
      setTimeout(done, est * 1000);
    } else if (onDone) onDone();
  } catch (e) { if (onDone) setTimeout(onDone, 500); }
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
      u.voice = pickVoice();
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
  const cycles = emergency ? 4 : (ctype == 'accident' ? 2 : 3);
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
    } else if (ctype === 'accident') {
      beep(1250, secs, 0.5);
      beep(800, secs + 0.6, 0.5);
      secs += 1.4;
      for (let k = 0; k < 3; k++) { beep(620, secs, 0.5); secs += 0.9; }
    } else {
      beep(1000, secs, 0.7);
      beep(1200, secs + 0.8, 0.7);
      secs += 1.6;
    }
  }
  return secs;
}

function stopPageAudio() {
  if (PAGE_AUDIO) {
    try { PAGE_AUDIO.onended = null; PAGE_AUDIO.onerror = null; PAGE_AUDIO.pause(); } catch (e) {}
  }
}

function playRecording(c, onFail) {
  // The recorded page IS the page the dispatcher sent: the same tone and
  // the same voice, so the phone plays exactly what the recording holds.
  // If the recording cannot start (blocked autoplay, a bad file), fall
  // back to the phone's own tone and voice.
  if (!c || !c.page_id) { onFail(); return; }
  try {
    if (!PAGE_AUDIO) PAGE_AUDIO = new Audio();
    const a = PAGE_AUDIO;
    a.src = '/call-page/' + c.page_id + '/audio';
    let started = false, failed = false;
    const fail = () => {
      if (started || failed) return;
      failed = true;
      stopPageAudio();
      onFail();
    };
    a.onplaying = () => { started = true; hideSoundNote(); };
    a.onerror = () => { if (!started) fail(); };
    a.onended = () => {
      try { a.onended = null; } catch (e) {}
      $('silenceBtn').disabled = true;
      ALERTING = false;
      playNext();
    };
    setTimeout(fail, 1500);
    const p = a.play();
    if (p && p.catch) p.catch(() => fail());
  } catch (e) { PAGE_AUDIO = null; onFail(); }
}

function stopAlert(keepQueue) {
  stopPageAudio();
  OSC.forEach(o => { try { o.stop(); } catch (e) {} });
  OSC = [];
  ALERTING = false;
  if (VOICE_T) { clearTimeout(VOICE_T); VOICE_T = null; }
  try { speechSynthesis.cancel(); } catch (e) {}
  if (!keepQueue) PAGE_QUEUE = [];
  $('silenceBtn').disabled = true;
}

function playNext() {
  const nxt = PAGE_QUEUE.shift();
  if (nxt) alertCall(nxt);
}

const TITLES = {police: 'Police', fire: 'Fire', medical: 'Medical', other: 'Call', accident: 'Traffic accident w/ injuries'};

function callSpeech(c) {
  const parts = [];
  if (ME_NAME) parts.push(ME_NAME + ', respond');
  if (FMT.say_type) parts.push(c.call_type == 'accident' ? 'Traffic accident with injuries' : TITLES[c.call_type] + ' call');
  parts.push('Address: ' + spAddr(c.address) +
    (FMT.say_cross && c.cross_street ? ' at ' + c.cross_street : ''));
  if (FMT.say_nature) parts.push('Nature: ' + (c.nature || ''));
  if (c.response_channel) parts.push('Respond on ' + c.response_channel);
  return parts.join('. ') + '.';
}

let ALERTING = false;

function alertCall(c) {
  // A page that arrives while another is still playing waits its turn:
  // the phone announces each call one after the other, tone and voice.
  if (ALERTING) { PAGE_QUEUE.push(c); return; }
  stopAlert(true);
  ALERTING = true;
  $('silenceBtn').disabled = false;
  playRecording(c, () => {
    const secs = toneFor(c.call_type, c.priority === 'emergency');
    if (secs) {
      VOICE_T = setTimeout(() => {
        speakAlert(c, () => {
          $('silenceBtn').disabled = true;
          ALERTING = false;
          playNext();
        });
      }, secs * 1000);
    } else {
      $('silenceBtn').disabled = true;
      ALERTING = false;
      playNext();
    }
  });
}

function nowTime() {
  const d = new Date();
  return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
}

function speakAlert(c, onDone) {
  const s = callSpeech(c);
  const again = 'Address: ' + spAddr(c.address) +
    (FMT.say_cross && c.cross_street ? ' at ' + c.cross_street : '') + '.'
    + ' Nature: ' + (c.nature == null ? '' : c.nature) + '.'
    + (c.response_channel ? ' Respond on ' + c.response_channel + '.' : '');
  speakMany([s, again, 'The time is ' + nowTime() + '.'], onDone);
}

function readAloud(b) {
  const addr = spAddr(b.dataset.addr);
  const parts = [];
  if (FMT.say_type) parts.push(TITLES[b.dataset.type] + ' call');
  parts.push('Address: ' + addr);
  if (FMT.say_nature) parts.push('Nature: ' + b.dataset.nat);
  if (b.dataset.ch) parts.push('Respond on ' + b.dataset.ch);
  const s = parts.join('. ') + '.';
  const again = 'Address: ' + addr + '.'
    + ' Nature: ' + b.dataset.nat + '.'
    + (b.dataset.ch ? ' Respond on ' + b.dataset.ch + '.' : '');
  speakMany([s, again, 'The time is ' + nowTime() + '.']);
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch])); }
function escAttr(s) { return esc(s).replace(/'/g, '&#39;'); }
function fmt(iso) { return iso ? new Date(iso).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : ''; }

function unitList(c) {
  const latest = {};
  for (const e of (c.responses || [])) {
    if (e.username) latest[String(e.username).trim().toLowerCase()] = e.status;
  }
  return (c.units || '').split(',').map(x => x.trim()).filter(Boolean).map(n => {
    const st = latest[n.toLowerCase()];
    return esc(n) + (st ? ' <span class="ustat">(' + esc(String(st).replace(/_/g, ' ')) + ')</span>' : '');
  }).join(', ');
}

function cardHtml(c) {
  return '<div class="card unitcard row-' + c.id + '">' +
    '<div class="row spread">' +
    '<h2><span class="tag ' + c.call_type + '">' + c.call_type + '</span> ' + esc(c.nature) + '</h2>' +
    '<button class="btn read" data-id="' + c.id + '" data-addr="' + escAttr(c.address + (c.apt ? ', Apt ' + c.apt : '') +
          (c.cross_street ? ' at ' + c.cross_street : '')) + '" data-nat="' + escAttr(c.nature) + '" data-type="' + c.call_type + '" data-time="" data-ch="' + escAttr(c.response_channel || '') + '" data-time="' + escAttr(c.notified_at || c.created_at || '') + '">Read</button>' +
    '</div>' +
    '<p><b>Address:</b> ' + esc(c.address) + (c.apt ? ' &middot; Apt ' + esc(c.apt) : '') +
    (c.cross_street ? ' &middot; at ' + esc(c.cross_street) : '') + '</p>' +
    '<p><b>Priority:</b> <span class="tag p-' + c.priority + '">' + c.priority + '</span>' +
    ' <b>Status:</b> ' + c.status + ' <b>Units:</b> ' + unitList(c) + '</p>' +
    '<p class="muted">Came in ' + fmt(c.created_at) + '</p>' +
    '<div class="row respbar">' +
    (c.my_status ? '<span class="tag">' + c.my_status.replace('_', ' ') + '</span>' : '') +
    '<button class="btn resp" data-act="enroute" data-id="' + c.id + '">Enroute</button>' +
    '<button class="btn resp" data-act="on_scene" data-id="' + c.id + '">On scene</button>' +
    '<button class="btn resp" data-act="transporting" data-id="' + c.id + '">Transport</button>' +
    '<button class="btn resp" data-act="clear" data-id="' + c.id + '">Clear</button>' +
    '<button class="btn read pagebtn" data-id="' + c.id + '" data-page="' + (c.page_id || '') + '" data-addr="' + escAttr(c.address + (c.apt ? ', Apt ' + c.apt : '') +
          (c.cross_street ? ' at ' + c.cross_street : '')) + '" data-nat="' + escAttr(c.nature) + '" data-type="' + c.call_type + '" data-ch="' + escAttr(c.response_channel || '') + '">Hear page again</button>' +
    '<a class="btn nav" href="https://www.google.com/maps/dir/?api=1&destination=' +
      encodeURIComponent((c.lat != null && c.lng != null) ? (c.lat + ',' + c.lng) : c.address) +
    '&travelmode=driving&dir_action=navigate">Navigate</a>' +
    '</div>' +
    '<div class="row reasonrow hidden" id="reason-' + c.id + '">' +
    '<input id="reasonInput-' + c.id + '" placeholder="Why are you clearing? (not required)">' +
    '</div>' +
    '<div class="notelist">' +
      (c.notes || []).map(n =>
        '<p class="muted"><b>' + esc(n.author) + '</b> ' + esc(fmt(n.created_at)) +
        ': ' + esc(n.text) + '</p>').join('') +
      '<div class="row noterow"><input id="noteInput-' + c.id + '" placeholder="Note for dispatch...">' +
      '<button class="btn noteadd" data-id="' + c.id + '">Send note</button></div>' +
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
function keepNoteInputs() {
  const keep = {};
  document.querySelectorAll('.noterow input').forEach(i => { keep[i.id] = i.value; });
  return keep;
}
function restoreNoteInputs(keep) {
  for (const id in keep) {
    const inp = document.getElementById(id);
    if (inp) inp.value = keep[id];
  }
}

async function poll() {
  const keep = keepReasons();
  const keepN = keepNoteInputs();
  try {
    const r = await fetch('/api/unit-board');
    if (r.status === 401) { location.href = '/login'; return; }
    const d = await r.json();
    const nt = document.getElementById('noTypes');
    if (nt) nt.classList.toggle('hidden', !d.no_types);
    ON_DUTY = d.me_status === 'on_duty';
    ME_NAME = d.me_name || '';
    const btn = $('dutyBtn');
    btn.textContent = ON_DUTY ? 'Go off duty' : 'Go on duty';
    btn.classList.toggle('primary', !ON_DUTY);
    if (ON_DUTY) {
      $('locNote').textContent = LOC_ERR ||
        'You are on duty. Dispatch can see your location.';
      gateLocation();
    } else {
      stopLocation();
      $('locNote').textContent =
        'You are off duty. Dispatch cannot see your location.';
    }
    $('activeCards').innerHTML = d.active.map(cardHtml).join('');
    restoreReasons(keep);
    restoreNoteInputs(keepN);
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
    if (b.classList.contains('pagebtn') && b.dataset.page) {
      ALERTING = true;
      $('silenceBtn').disabled = false;
      playRecording({page_id: b.dataset.page}, () => readAloud(b));
      return;
    }
    readAloud(b);
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
  if (next === 'on_duty') { DEGRADED = false; PERM_BLOCKED = false; }
  poll();
});

function startWatch(high) {
  if (!navigator.geolocation) {
    LOC_ERR = 'This phone cannot share its location.';
    return;
  }
  if (WATCH) navigator.geolocation.clearWatch(WATCH);
  WATCH_HIGH = high;
  WATCH = navigator.geolocation.watchPosition(sendFix, locFail,
    high ? {enableHighAccuracy: true, timeout: 15000}
         : {enableHighAccuracy: false, timeout: 25000});
}

function stopLocation() {
  if (WATCH) { navigator.geolocation.clearWatch(WATCH); WATCH = null; }
  LOC_ERR = '';
}

// Keeps the GPS watch in step with duty, whatever set it: the unit's own
// button, dispatch putting them on duty, or a page reload while on duty.
function gateLocation() {
  if (!ON_DUTY) { stopLocation(); return; }
  if (PERM_BLOCKED) return;
  if (!WATCH) startWatch(DEGRADED);
}

function locFail(err) {
  if (WATCH) { navigator.geolocation.clearWatch(WATCH); WATCH = null; }
  const code = err && err.code ? err.code : 0;
  if (code === 1) {
    PERM_BLOCKED = true;
    LOC_ERR = 'Location permission is blocked. Turn on Location for this app in the phone settings, then reload this page.';
  } else if (!DEGRADED) {
    DEGRADED = true;
    startWatch(false);
  } else {
    LOC_ERR = 'The phone cannot get a location yet. Dispatch will see it as soon as it does.';
  }
}

function sendFix(pos) {
  if (!ON_DUTY) return;
  const now = Date.now();
  if (now - LAST_PING < 10000) return;
  LAST_PING = now;
  LOC_ERR = '';
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

document.addEventListener('click', async e => {
  const nb = e.target.closest('button.noteadd');
  if (!nb) return;
  const id = nb.dataset.id;
  const inp = document.getElementById('noteInput-' + id);
  const text = (inp.value || '').trim();
  if (!text) { inp.focus(); return; }
  nb.disabled = true;
  const fd = new FormData(); fd.append('text', text);
  try {
    const r = await fetch('/api/call/' + id + '/note', {method: 'POST', body: fd});
    if (r.ok) { inp.value = ''; poll(); } else {
      nb.disabled = false;
      inp.placeholder = r.status === 403 ? 'Call is closed' : 'Could not send';
    }
  } catch (err) { nb.disabled = false; }
});
