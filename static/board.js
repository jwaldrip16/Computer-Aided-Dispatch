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

const TITLES = {police: 'Police', fire: 'Fire', medical: 'Medical', other: 'Call', accident: 'Traffic accident w/ injuries'};

function callSpeech(c) {
  const parts = [];
  if (FMT.say_type) parts.push(c.call_type == 'accident' ? 'Traffic accident with injuries' : TITLES[c.call_type] + ' call');
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
  const nearBtn = c.status !== 'closed'
    ? ` <button class="btn near" data-id="${c.id}">Nearest</button>` : '';
  const clrBtn = c.status !== 'closed'
    ? ` <button class="btn clr" data-id="${c.id}">Clear</button>` : '';
  const unitList = (c.units || '').split(',').map(s => s.trim()).filter(Boolean);
  const unitsCell = (open && unitList.length)
    ? unitList.map(n =>
        `<button class="btn udel" data-id="${c.id}" data-name="${escAttr(n)}" data-units="${escAttr(c.units)}" title="Remove ${escAttr(n)}">${esc(n)} &times;</button>`).join(' ')
    : esc(c.units || '');
  const unitsCtl = open
    ? (!c.notified_at
      ? ` <input class="units-inp" placeholder="unit names" value="${escAttr(c.units || '')}"> ` +
        `<button class="btn bassign" data-id="${c.id}">Assign</button>`
      : ` <input class="units-inp" placeholder="add more units"> ` +
        `<button class="btn badd" data-id="${c.id}">Add units</button> ` +
        `<button class="btn bpage" data-id="${c.id}">Re-page</button>`)
    : '';
  return `<tr ${open ? 'class="row-' + c.id + '"' : ''}>` +
    `<td>${fmt(c.created_at)}</td>` +
    `<td><span class="tag ${c.call_type}">${c.call_type}</span></td>` +
    `<td>${esc(c.nature)}</td>` +
    `<td>${esc(c.address)}${c.response_channel ? ' &middot; Ch ' + esc(c.response_channel) : ''}</td>` +
    `<td><span class="tag p-${c.priority}">${c.priority}</span></td>` +
    `<td>${c.status}</td>` +
    `<td>${unitsCell}</td>` +
    `<td>${esc(c.dispatcher || '')}</td>` +
    `<td><a href="/call/${c.id}">Open</a> ` +
    `<button class="btn read" data-id="${c.id}" data-addr="${escAttr(c.address || 'not given')}" data-nat="${escAttr(c.nature || 'not given')}">Read</button> ` +
    send + queueBtn + nearBtn + unitsCtl + clrBtn + (IS_ADMIN ? ` <button class="btn del" data-id="${c.id}">Delete</button>` : '') +
    `</td>` +
    `</tr>`;
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch])); }
function escAttr(s) { return esc(s).replace(/'/g, '&#39;'); }

function fill(tbodyId, emptyId, list) {
  const tb = $(tbodyId);
  tb.innerHTML = list.map(rowHtml).join('');
  $(emptyId).classList.toggle('hidden', list.length > 0);
}

function unitChip(u) {
  const cls = u.unit_status === 'on_duty' ? 'u u-on' : 'u u-off';
  const st = u.unit_status === 'on_duty' ? 'on duty'
    : String(u.unit_status || 'off duty').replace(/_/g, ' ');
  const types = u.unit_types ? ' (' + u.unit_types.split(',').join(', ') + ')' : '';
  const loc = u.loc_updated
    ? ' <span class="uloc">location ' +
      new Date(u.loc_updated).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) + '</span>'
    : '';
  const duty = u.unit_status === 'on_duty'
    ? '<button class="btn dutybtn" data-name="' + escAttr(u.username) + '" data-to="off_duty">Send off duty</button>'
    : '<button class="btn dutybtn" data-name="' + escAttr(u.username) + '" data-to="on_duty">Put on duty</button>';
  return '<span class="' + cls + '">' + esc(u.username) +
    ' <span class="ustat">' + esc(st) + '</span>' + esc(types) + loc + duty + '</span>';
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
  const ba = e.target.closest('button.bassign');
  if (ba) {
    const inp = ba.closest('tr').querySelector('input.units-inp');
    ba.disabled = true;
    const fd = new FormData();
    fd.set('units', inp.value.trim());
    const res = await fetch(`/call/${ba.dataset.id}/units`, {method: 'POST', body: fd});
    if (!res.ok) alert(await res.text());
    poll();
  }
  const bu = e.target.closest('button.badd');
  if (bu) {
    const inp = bu.closest('tr').querySelector('input.units-inp');
    const v = inp.value.trim();
    if (!v) return;
    bu.disabled = true;
    const fd = new FormData();
    fd.set('units', v);
    fd.set('mode', 'add');
    const res = await fetch(`/call/${bu.dataset.id}/units`, {method: 'POST', body: fd});
    if (!res.ok) alert(await res.text());
    poll();
  }
  const ud = e.target.closest('button.udel');
  if (ud) {
    ud.disabled = true;
    const rest = (ud.dataset.units || '').split(',').map(s => s.trim())
      .filter(Boolean).filter(n => n !== ud.dataset.name);
    const fd = new FormData();
    fd.set('units', rest.join(', '));
    await fetch(`/call/${ud.dataset.id}/units`, {method: 'POST', body: fd});
    poll();
  }
  const du = e.target.closest('button.dutybtn');
  if (du) {
    du.disabled = true;
    const res = await fetch('/api/dispatch/unit-duty', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: du.dataset.name, status: du.dataset.to})});
    if (!res.ok) alert(await res.text());
    poll();
    return;
  }
  const bp = e.target.closest('button.bpage');
  if (bp) {
    bp.disabled = true;
    await fetch(`/call/${bp.dataset.id}/notify`, {method: 'POST'});
    poll();
  }
  const cl = e.target.closest('button.clr');
  if (cl) {
    cl.disabled = true;
    await fetch(`/call/${cl.dataset.id}/status`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({status: 'closed'})
    });
    poll();
  }
  const nr = e.target.closest('button.near');
  if (nr) {
    nr.disabled = true;
    const id = nr.dataset.id;
    let r = await fetch(`/call/${id}/recommend`, {method: 'POST'});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { alert(d.error || 'Could not find the closest unit.'); nr.disabled = false; return; }
    if (!d.recommendations.length) { alert('No on-duty units are sharing a location right now.'); nr.disabled = false; return; }
    const first = d.recommendations[0];
    const fd = new FormData();
    fd.append('units', first.username);
    await fetch(`/call/${id}/units`, {method: 'POST', body: fd});
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
