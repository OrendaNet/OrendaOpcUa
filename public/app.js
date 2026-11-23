'use strict';
const $ = id => document.getElementById(id);
const views = ['overview', 'channels', 'events', 'output', 'connection'];
let state, view = 'overview', certificate, inspected, page, offset = 0, browseGeneration = 0;
let path = [{ id: 'i=85', name: 'Objects' }], pending = false;

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function set(id, text) { $(id).textContent = text; }
function empty(text) { return el('div', text, 'empty'); }
function shortTime(stamp) { return stamp ? new Date(stamp).toLocaleString() : '—'; }
function say(text, error = false) {
  set('feedback', text);
  $('feedback').dataset.error = String(error);
  $('feedback').hidden = !text;
}
async function request(url, body) {
  const options = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  };
  const response = await fetch(url, { ...options, cache: 'no-store' });
  const txt = await response.text();
  if (!response.ok) {
    let error;
    try { error = JSON.parse(txt).error; } catch { /* Edge can return plain text. */ }
    throw Error(error || txt.slice(0, 160) || 'Request failed.');
  }
  return response.headers.get('content-type')?.includes('application/json') ? JSON.parse(txt) : txt;
}
function action(fn) {
  return async event => {
    event?.preventDefault();
    if (pending) return;
    pending = true;
    const button = event?.submitter || (event?.currentTarget?.tagName === 'BUTTON' ? event.currentTarget : null);
    if (button) button.disabled = true;
    try { say(''); await fn(); } catch (err) { say(err.message, true); }
    finally { pending = false; if (button) button.disabled = false; }
  };
}
function btn(text, fn, css) {
  const b = el('button', text, css);
  b.type = 'button'; b.onclick = action(fn);
  return b;
}
async function refresh() {
  const next = await request('api/state');
  if (state && next.revision < state.revision) return;
  const first = !state;
  state = next;
  if (first) {
    $('url').value = state.connection?.url || '';
    $('rate').value = state.interval;
    document.querySelectorAll('.admin').forEach(node => { if (!state.manage) node.hidden = true; });
  }
  const connected = state.online ? 'Connected' : state.connection ? 'Offline' : 'Setup needed';
  set('topStatus', connected); $('topStatus').dataset.live = String(state.online);
  set('connectionStatus', connected);
  set('address', state.error || state.connection?.url || 'No connection configured');
  set('count', state.channels.length);
  set('readTime', state.lastRead ? 'Last read ' + shortTime(state.lastRead) : 'Waiting for a reading');
  const recent = state.lastScrape && Date.now() - Date.parse(state.lastScrape) < 120000;
  set('scrapeStatus', recent ? 'Receiving scrapes' : state.lastScrape ? 'Last scrape > 2 min' : 'Awaiting scrape');
  set('scrapeTime', state.lastScrape ? shortTime(state.lastScrape) : 'No collector has scraped this app');
  set('outputStatus', state.lastScrape ? 'Last collector scrape ' + shortTime(state.lastScrape) : 'Waiting for the first collector scrape.');
  $('setup').hidden = !state.manage || Boolean(state.connection);
  renderReadings(); renderSelected(); renderEvents();
}
async function mutate(name, fields) {
  await request('api/' + name, { ...fields, revision: state.revision });
  await refresh();
}
async function switchTo(next) {
  if (next === 'connection' && !state?.manage) return;
  view = next;
  set('heading', {overview:'Controller overview',channels:'Channel selection',events:'Event monitor',
    output:'Prometheus integration',connection:'Connect a controller'}[next]);
  for (const id of views) $(id).hidden = next !== id;
  document.querySelectorAll('[data-view]').forEach(b => {
    if (b.dataset.view === next) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  });
  if (next !== 'output') { $('token').hidden = true; set('token', ''); set('showToken', 'Show scrape credential'); }
  if (next === 'channels' && state.manage && state.connection) await browse();
  if (next === 'output') set('exposition', await request('api/output'));
}
function renderReadings() {
  const parent = $('readings');
  if (!state.channels.length) {
    parent.replaceChildren(empty(state.connection ? 'Choose a readable channel to see its first sample.' :
      'Connect a controller to see its readings here.')); return;
  }
  const table = el('table'), head = el('tr');
  for (const name of ['Channel', 'Value', 'Quality', 'Controller time']) head.append(el('th', name));
  const thead = el('thead'); thead.append(head); table.append(thead);
  const body = el('tbody');
  for (const c of state.channels) {
    const item = state.samples[c.metric], row = el('tr');
    for (const v of [c.metric, item?.good && item.numeric ? item.value : '—',
      item?.quality || (state.online ? 'Waiting' : 'Offline'), shortTime(item?.sourceTime)]) row.append(el('td', v));
    body.append(row);
  }
  table.append(body); parent.className = 'table'; parent.replaceChildren(table);
}
function renderSelected() {
  $('selected').replaceChildren(...state.channels.map(c => {
    const row = el('div', null, 'row'), label = el('div');
    label.append(el('strong', c.metric), el('small', c.name + ' · ' + c.identifier)); row.append(label);
    if (state.manage) row.append(btn('Remove', () => mutate('remove', {metric: c.metric})));
    return row;
  }));
  if (!state.channels.length) $('selected').append(empty('No channels selected.'));
}
function renderEvents() {
  set('eventStatus', state.eventState);
  set('source', state.notifier ? 'Event source · ' + state.notifier.name : 'Choose a controller object that supports events.');
  $('stopEvents').hidden = !state.manage || !state.notifier;
  $('eventList').replaceChildren(...state.events.map(e => {
    const div = el('article', null, 'event');
    div.append(el('strong', [e.code, e.text].filter(Boolean).join(' · ') || 'Controller event'),
      el('p', [e.phase, e.source, 'Severity ' + (e.severity ?? '—'), e.retain == null ? '' : 'Retain ' + e.retain].filter(Boolean).join(' · ')),
      el('small', 'Controller ' + shortTime(e.time) + ' · Box ' + shortTime(e.received)));
    return div;
  }));
  if (!state.events.length) $('eventList').append(empty('No events received this session.'));
}
async function browse() {
  const generation = ++browseGeneration;
  const here = path[path.length - 1];
  set('crumb', path.map(p => p.name).join(' / '));
  $('up').hidden = path.length < 2;
  $('nodes').replaceChildren(empty('Loading nodes…'));
  $('prevPage').hidden = $('nextPage').hidden = true;
  try { const result = await request('api/browse?id=' + encodeURIComponent(here.id) + '&offset=' + offset);
    if (generation !== browseGeneration) return;
    page = result; drawNodes();
  } catch (err) { $('nodes').replaceChildren(empty(err.message), btn('Retry browse', browse)); }
}
function drawNodes() {
  if (!page) return;
  const query = $('filter').value.trim().toLowerCase();
  const rows = page.rows.filter(r => (r.name + ' ' + r.id).toLowerCase().includes(query));
  $('nodes').replaceChildren(...rows.map(r => {
    const row = el('div', null, 'row');
    const folder = r.kind !== 'Variable';
    row.append(btn(r.name, async () => {
      if (!folder) return inspect(r.id);
      path.push(r); offset = 0; $('filter').value = ''; $('inspector').hidden = true; await browse();
    }, 'node'));
    if (folder) row.append(btn('Inspect', () => inspect(r.id)));
    else row.append(el('small', r.kind));
    return row;
  }));
  if (!rows.length) $('nodes').append(empty(query ? 'No matches on this page.' : 'No child nodes.'));
  $('prevPage').hidden = offset === 0; $('nextPage').hidden = page.next == null;
}
async function inspect(id) {
  $('inspector').hidden = true;
  inspected = await request('api/preview?id=' + encodeURIComponent(id));
  const n = inspected; $('inspector').hidden = false;
  $('preview').replaceChildren(el('h3', n.name), el('code', n.id, 'wrap'),
    el('p', n.kind === 'Variable' ? [n.type, n.quality, n.value ?? '—', shortTime(n.sourceTime)].join(' · ') : n.kind));
  if (n.quality === 'BadUserAccessDenied') $('preview').append(el('p', 'Check the controller user’s read permissions and enabled data options.'));
  $('addForm').hidden = !(n.good && n.numeric);
  let suffix = (path.length > 1 ? path.slice(-2).map(p => p.name).concat(n.name) : [n.name]).join('_');
  suffix = suffix.replace(/([a-z])([A-Z])/g, '$1_$2').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  $('metric').value = (/^[a-z]/.test(suffix) ? suffix : 'channel_' + suffix).slice(0, 63);
  $('useNotifier').hidden = !n.events;
  $('inspector').scrollIntoView({ block: 'nearest' });
}
document.querySelectorAll('[data-view],[data-go]').forEach(b => b.onclick = action(() => switchTo(b.dataset.view || b.dataset.go)));
$('setup').onclick = action(() => switchTo('connection'));
$('up').onclick = action(async () => { path.pop(); offset = 0; $('filter').value = ''; $('inspector').hidden = true; await browse(); });
$('nextPage').onclick = action(async () => { offset = page.next; await browse(); });
$('prevPage').onclick = action(async () => { offset = Math.max(0, offset - 64); await browse(); });
$('filter').oninput = drawNodes;
$('directForm').onsubmit = action(() => inspect($('direct').value.trim()));
$('addForm').onsubmit = action(async () => { await mutate('add', { id: inspected.id, metric: $('metric').value.trim() }); say('Channel added to Prometheus output.'); });
$('useNotifier').onclick = action(async () => { await mutate('notifier', { id: inspected.id }); await switchTo('events'); });
$('stopEvents').onclick = action(() => mutate('notifier', { id: null }));
$('refreshOutput').onclick = action(async () => set('exposition', await request('api/output')));
$('showToken').onclick = action(async () => {
  if (!$('token').hidden) { set('token', ''); $('token').hidden = true; set('showToken', 'Show scrape credential'); return; }
  set('token', (await request('api/token')).token); $('token').hidden = false; set('showToken', 'Hide scrape credential');
});
$('url').oninput = () => { certificate = null; $('trust').hidden = true; $('confirm').checked = false; };
$('method').onchange = () => { $('credentials').hidden = $('method').value !== 'UserName'; };
$('discoverForm').onsubmit = action(async () => {
  $('trust').hidden = true; $('confirm').checked = false;
  const url = $('url').value.trim();
  const found = await request('api/discover', { url });
  if ($('url').value.trim() !== url) return;
  certificate = { ...found, url };
  set('serverInfo', found.name + ' · ' + found.policy + ' · Expires ' + shortTime(found.expires));
  set('pin', found.fingerprint.match(/../g).join(':'));
  $('method').replaceChildren(...found.identities.filter(t => ['UserName','Anonymous'].includes(t)).map(t => {
    const o = el('option', t === 'UserName' ? 'Controller username and password' : 'Anonymous'); o.value = t; return o;
  }));
  if (!$('method').options.length) throw Error('This server requires an unsupported user identity type.');
  $('method').onchange(); $('trust').hidden = false;
});
$('connectForm').onsubmit = action(async () => {
  if (!certificate || !$('confirm').checked) throw Error('Compare and confirm the server fingerprint first.');
  const user = $('method').value === 'UserName';
  if (user && !$('username').value.trim()) throw Error('Enter the controller username.');
  await mutate('connect', { url: certificate.url, fingerprint: certificate.fingerprint,
    username: user ? $('username').value.trim() : '', password: user ? $('password').value : '' });
  $('password').value = ''; $('trust').hidden = true; path = [{id:'i=85',name:'Objects'}]; offset = 0;
  await switchTo('channels'); say('Connected. Choose a channel to preview.');
});
$('rateForm').onsubmit = action(async () => { await mutate('interval', { interval: Number($('rate').value) }); say('Read interval saved.'); });
async function poll() {
  try { if (!pending) await refresh(); } catch (err) { say(err.message, true); }
  setTimeout(poll, 3000);
}
poll();
