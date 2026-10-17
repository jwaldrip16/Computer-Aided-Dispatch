let VOICE_ON = localStorage.getItem('cad_voice') !== 'off';
const seen = new Set();
let firstLoad = true;
const $ = id => document.getElementById(id);

function speak(text) {
  if (!VOICE_ON || !('speechSynthesis' in window)) return;
  try {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 0.95;
    speechSynthesis.speak(u);
  } catch (e) {}
}

function fmt(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
}

const TITLES = {police: 'Police', fire: 'Fire', medical: 'Medical', other: 'Call'};

function callSpeech(c) {
  const parts = [];
  if (FMT.say_type) parts.push(TITLES[c.call_type] + ' call');
  if (FMT.say_address) parts.push('Address: ' + (c.address || ''));
  if (FMT.say_nature) parts.push('Nature: ' + (c.nature || ''));
  if (FMT.say_time) parts.push('Dispatched at ' +
    new Date(c.notified_at || c.created_at).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}));
  return parts.join('. ') + '.';
}

function rowHtml(c) {
  const open = c.status !== 'closed';
  const send = !c.notified_at
    ? `<button class="btn notify" data-id="${c.id}">Send to units</button>`
    : '<span class="muted">sent</span>';
  const queueBtn = (c.status === 'open' || c.status === 'dispatched')
    ? ` <button class="btn queue" data-id="${c.id}">Queue</button>` : '';
  return `<tr ${open ? 'class="row-' + c.id + '"' : ''}>` +
    `<td>${fmt(c.created_at)}</td>` +
    `<td><span class="tag ${c.call_type}">${c.call_type}</span></td>` +
    `<td>${esc(c.nature)}</td>` +
    `<td>${esc(c.address)}</td>` +
    `<td><span class="tag p-${c.priority}">${c.priority}</span></td>` +
    `<td>${c.status}</td>` +
    `<td>${esc(c.units || '')}</td>` +
    `<td>${esc(c.dispatcher || '')}</td>` +
    `<td><a href="/call/${c.id}">Open</a> ` +
    `<button class="btn read" data-id="${c.id}">Read</button> ` +
    send + queueBtn + (IS_ADMIN ? ` <button class="btn del" data-id="${c.id}">Delete</button>` : '') +
    `</td>` +
    `</tr>`;
}

function esc(s) { return String(s ?? '').replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch])); }
function escAttr(s) { return esc(s).replace(/'/g, '&#39;'); }

function fill(tbodyId, emptyId, list) {
  const tb = $(tbodyId);
  tb.innerHTML = list.map(rowHtml).join('');
  $(emptyId).classList.toggle('hidden', list.length > 0);
}

function unitChip(u) {
  const cls = u.unit_status === 'on_duty' ? 'u u-on' : 'u u-off';
  const types = u.unit_types ? ' (' + u.unit_types.split(',').join(', ') + ')' : '';
  const loc = u.loc_updated
    ? ' <span class="uloc">location ' +
      new Date(u.loc_updated).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) + '</span>'
    : '';
  return '<span class="' + cls + '">' + esc(u.username) + esc(types) + loc + '</span>';
}

function fillUnits(list) {
  const on = list.filter(u => u.unit_status === 'on_duty');
  const off = list.filter(u => u.unit_status !== 'on_duty');
  $('onDuty').innerHTML = on.length ? on.map(unitChip).join(' ')
    : '<span class="muted">Nobody on duty</span>';
  $('offDuty').innerHTML = off.length ? off.map(unitChip).join(' ')
    : '<span class="muted">No units</span>';
}

async function poll() {
  try {
    const r = await fetch('/api/board');
    if (r.status === 401) { location.href = '/login'; return; }
    const d = await r.json();
    fill('activeBody', 'noActive', d.active);
    fill('pendingBody', 'noPending', d.pending || []);
    fill('recentBody', 'noRecent', d.recent);
    fillUnits(d.units || []);
    for (const c of d.active) {
      if (!seen.has(c.id)) {
        if (!firstLoad) {
          const rows = document.querySelectorAll('.row-' + c.id);
          rows.forEach(x => x.classList.add('flash'));
        }
        seen.add(c.id);
      }
    }
    firstLoad = false;
  } catch (e) {}
}

$('voiceBtn').textContent = 'Voice: ' + (VOICE_ON ? 'ON' : 'OFF');
$('voiceBtn').addEventListener('click', () => {
  VOICE_ON = !VOICE_ON;
  localStorage.setItem('cad_voice', VOICE_ON ? 'on' : 'off');
  $('voiceBtn').textContent = 'Voice: ' + (VOICE_ON ? 'ON' : 'OFF');
  if (VOICE_ON) speak('Voice alerts on.');
});

document.addEventListener('click', async e => {
  const b = e.target.closest('button.read');
  if (b) speak(`Address: ${b.dataset.addr}. Nature: ${b.dataset.nat}.`);
  const qb = e.target.closest('button.queue');
  if (qb) {
    qb.disabled = true;
    await fetch(`/call/${qb.dataset.id}/pending`, {method: 'POST'});
    poll();
  }
  const n = e.target.closest('button.notify');
  if (n) {
    n.disabled = true;
    await fetch(`/call/${n.dataset.id}/notify`, {method: 'POST'});
    poll();
  }
});

$('logShareBtn').addEventListener('click', async () => {
  const r = await fetch('/share/log', {method: 'POST'});
  if (!r.ok) return;
  const d = await r.json();
  $('shareLogPanel').classList.remove('hidden');
  $('shareLogPanel').innerHTML =
    '<p>Anyone with this link can read the call log (read-only):</p>' +
    '<p><a href="' + d.url + '">' + location.origin + d.url + '</a></p>' +
    '<p class="muted">Keep it private. Anyone who has the link can read the calls.</p>';
});

poll();
setInterval(poll, 4000);

document.addEventListener('click', e => {
  const b = e.target.closest('button.del');
  if (!b) return;
  if (b.dataset.c !== '1') { b.dataset.c = '1'; b.textContent = 'Sure?'; return; }
  fetch('/call/' + b.dataset.id + '/delete', {method: 'POST'})
    .then(r => { if (r.ok) poll(); else b.textContent = 'Failed'; })
    .catch(() => { b.textContent = 'Failed'; });
});
