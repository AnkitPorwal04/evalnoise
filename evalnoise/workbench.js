let refreshing = false;
function apply() {
  const filters = [...document.querySelectorAll('[data-filter]')];
  let count = 0;
  for (const row of document.querySelectorAll('#records tbody tr')) {
    const show = filters.every(f => {
      const text = f.dataset.filter === 'search' ? (row.dataset.search || row.textContent) : row.dataset[f.dataset.filter];
      return !f.value || (f.dataset.filter === 'search' ? text.toLowerCase().includes(f.value.toLowerCase()) : text === f.value);
    });
    row.hidden = !show;
    if (show) count++;
  }
  const counter = document.getElementById('count');
  if (counter) counter.textContent = count + ' visible records (full counts above are unchanged)';
  for (const link of document.querySelectorAll('[data-export]')) {
    const url = new URL(link.dataset.export, location.origin);
    for (const f of filters) if (f.value) url.searchParams.set(f.dataset.filter, f.value);
    link.href = url.pathname + url.search;
  }
}
document.addEventListener('input', e => { if (e.target.matches('[data-filter]')) apply(); });
document.addEventListener('click', e => {
  if (e.target.id === 'compare-go') {
    const params = new URLSearchParams({baseline:document.getElementById('baseline').value,candidate:document.getElementById('candidate').value});
    location.href = '/provenance?' + params;
  }
  if (e.target.id === 'refresh') refresh();
});
async function refresh() {
  if (refreshing || document.hidden) return;
  refreshing = true;
  const path = location.pathname;
  const live = document.getElementById('live')?.checked;
  const filters = Object.fromEntries([...document.querySelectorAll('[data-filter]')].map(f=>[f.dataset.filter,f.value]));
  const opened = [...document.querySelectorAll('details[open] summary')].map(s=>s.textContent);
  const focus = document.activeElement?.dataset.filter;
  const scroll = document.querySelector('.scroll');
  const top = scroll?.scrollTop || 0;
  try {
    const response = await fetch(location.href, {cache:'no-store'});
    if (!response.ok) throw new Error('Snapshot unavailable (' + response.status + '); keeping previous evidence.');
    const doc = new DOMParser().parseFromString(await response.text(),'text/html');
    if (location.pathname !== path) return;
    document.querySelector('main').replaceWith(doc.querySelector('main'));
    if (document.getElementById('live')) document.getElementById('live').checked = live;
    for (const f of document.querySelectorAll('[data-filter]')) {
      const value = filters[f.dataset.filter] || '';
      if (f.tagName === 'SELECT' && value && ![...f.options].some(o=>o.value===value)) f.add(new Option(value,value));
      f.value = value;
      if (focus === f.dataset.filter) f.focus({preventScroll:true});
    }
    for (const d of document.querySelectorAll('details')) d.open = opened.includes(d.querySelector('summary').textContent);
    const newScroll = document.querySelector('.scroll');
    if (newScroll) newScroll.scrollTop = top;
    apply();
    document.getElementById('refresh-status').textContent = 'Snapshot refreshed ' + new Date().toLocaleTimeString() + ' · files may change between reads';
  } catch (error) {
    const status = document.getElementById('refresh-status');
    if (status) status.textContent = error.message;
  } finally { refreshing = false; }
}
setInterval(()=>{if(document.getElementById('live')?.checked)refresh();},5000);
apply();
