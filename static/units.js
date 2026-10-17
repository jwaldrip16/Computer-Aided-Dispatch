const $ = id => document.getElementById(id);
let map = null, layer = null;
if (window.L) {
  map = L.map('map').setView([32.6, -85.4], 10);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19}).addTo(map);
  layer = L.layerGroup().addTo(map);
}

function ago(iso) {
  if (!iso) return 'no location';
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (mins < 1) return 'just now';
  if (mins === 1) return '1 min ago';
  return mins + ' min ago';
}

async function poll() {
  try {
    const r = await fetch('/api/units');
    if (r.status === 401) { location.href = '/login'; return; }
    const d = await r.json();
    const rows = d.units.map(u =>
      '<tr><td>' + esc(u.username) + '</td><td>' + esc(u.unit_types) + '</td>' +
      '<td><span class="tag ' + (u.unit_status === 'on_duty' ? 'medical' : 'other') + '">' +
      (u.unit_status === 'on_duty' ? 'on duty' : 'off duty') + '</span></td>' +
      '<td>' + ago(u.loc_updated) + '</td>' +
      '<td>' + (u.unit_status === 'on_duty' && u.lat != null
        ? '<a target="_blank" href="https://www.google.com/maps?q=' + u.lat + ',' + u.lng + '">Map</a>'
        : '') + '</td></tr>').join('') ||
      '<tr><td colspan="5" class="muted">No unit accounts yet. Add them on the Users page.</td></tr>';
    $('unitBody').innerHTML = rows;
    if (layer) {
      layer.clearLayers();
      d.units.filter(u => u.unit_status === 'on_duty' && u.lat != null).forEach(u => {
        L.marker([u.lat, u.lng]).bindPopup(esc(u.username) + ' (on duty)').addTo(layer);
      });
    }
  } catch (e) {}
}
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch])); }
poll();
setInterval(poll, 5000);
