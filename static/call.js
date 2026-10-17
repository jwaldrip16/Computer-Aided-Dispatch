const $ = id => document.getElementById(id);

function speak(text) {
  if (!('speechSynthesis' in window)) return;
  try {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 0.95;
    speechSynthesis.speak(u);
  } catch (e) {}
}

function spAddr(a) {
  return String(a == null ? '' : a)
    .replace(/,?\s*(United States( of America)?|U\.S\.A\.?|USA)\s*$/i, '')
    .trim().replace(/,+$/, '');
}

$('readBtn').addEventListener('click', () => {
  speak(`Type: ${CALL.call_type}. Address: ${spAddr(CALL.address)}. Nature: ${CALL.nature}.`
    + (CALL.response_channel ? ` Respond on ${CALL.response_channel}.` : '')
    + ` Status: ${CALL.status}.`);
});

$('shareBtn').addEventListener('click', async () => {
  const r = await fetch(`/call/${CALL.id}/share`, {method: 'POST'});
  if (!r.ok) return;
  const d = await r.json();
  $('shareOut').innerHTML =
    'Read-only share link: <a href="' + d.url + '">' + location.origin + d.url + '</a>';
});

document.querySelectorAll('#sopList input[type=checkbox]').forEach(cb => {
  cb.addEventListener('change', async () => {
    await fetch(`/call/${CALL.id}/sop`, {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: 'position=' + cb.dataset.pos + '&done=' + (cb.checked ? '1' : '0')
    });
    location.reload();
  });
});
