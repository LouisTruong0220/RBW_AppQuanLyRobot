/* app.js — giao diện mẫu. JS thuần, không thư viện ngoài.
   Mọi dữ liệu đi qua server.py (/api/...) — trình duyệt không bao giờ gọi thẳng PUDU,
   vì gọi thẳng thì phải lộ ApiAppSecret ra trình duyệt. */
'use strict';

const S = { sys: null, robots: [], statuses: {}, sn: '', robot: null, map: null, points: [],
            callTask: '', errandSession: '', lastEvent: 0, events: [] };
const $ = id => document.getElementById(id);

function h(tag, attrs, kids) {
  const e = document.createElement(tag);
  for (const k in attrs || {}) {
    if (k === 'text') e.textContent = attrs[k];
    else if (k === 'html') e.innerHTML = attrs[k];
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), attrs[k]);
    else e.setAttribute(k, attrs[k]);
  }
  (kids || []).forEach(c => c != null && e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c));
  return e;
}
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function api(name, params) {
  const q = new URLSearchParams();
  // undefined qua URLSearchParams thành chuỗi "undefined" — hãng báo lỗi rất khó hiểu. Lọc trước.
  for (const k in params || {}) if (params[k] !== undefined && params[k] !== null && params[k] !== '') q.set(k, params[k]);
  const r = await fetch('/api/' + name + (q.toString() ? '?' + q : ''));
  const js = await r.json();
  if (!r.ok || js.ok === false) throw Object.assign(new Error(js.error || ('HTTP ' + r.status)), js);
  return js;
}
async function post(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const js = await r.json();
  if (!r.ok || js.ok === false) throw Object.assign(new Error(js.error || ('HTTP ' + r.status)), js);
  return js;
}
function toast(msg, err) {
  const t = $('toast');
  t.textContent = msg; t.className = 'toast' + (err ? ' err' : '');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add('hidden'), 5000);
}
const fmtTime = s => s ? new Date((s > 1e12 ? s : s * 1000)).toLocaleString('vi-VN') : '—';
const robotName = r => r ? (r.nickname || r.sn) : '';

// ===================================================================== khởi động
async function boot() {
  document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => openTab(b.dataset.tab));
  S.sys = await api('system');
  renderBadges(); renderSettings();
  if (!S.sys.has_key) {
    $('robot-cards').innerHTML = '<div class="empty">Chưa có khoá API. Điền <code>config.json</code> (xem config.example.json) rồi chạy lại, hoặc chạy <code>python server.py --demo</code>.</div>';
    return;
  }
  await loadRobots();
  bindControls();
  pollEvents(); setInterval(pollEvents, 3000);
  setInterval(refreshStatuses, 15000);
}

function openTab(name) {
  document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t.id === 'tab-' + name));
  if (name === 'map') loadMap();
  if (name === 'control') { loadPoints(); loadRobotDetail(); }
}

function renderBadges() {
  const b = $('badges'); b.innerHTML = '';
  b.appendChild(h('span', { class: 'badge ' + (S.sys.demo ? 'demo' : 'live'), text: S.sys.demo ? 'DEMO · robot giả lập' : 'KẾT NỐI THẬT' }));
  b.appendChild(h('span', { class: 'badge ' + (S.sys.allow_commands ? 'rw' : 'ro'), text: S.sys.allow_commands ? 'ĐƯỢC RA LỆNH' : 'CHỈ ĐỌC' }));
  $('readonly-banner').classList.toggle('hidden', !!S.sys.allow_commands);
  document.querySelectorAll('.cmd').forEach(x => x.disabled = !S.sys.allow_commands);
}

// ===================================================================== 1. QUẢN LÝ
async function loadRobots() {
  try {
    S.robots = (await api('robots', { flashbot_only: 1 })).list;
  } catch (e) { $('robot-cards').innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; return; }
  const sel = $('robot-select'); sel.innerHTML = '';
  S.robots.forEach(r => sel.appendChild(h('option', { value: r.sn, text: robotName(r) + ' · ' + r.sn })));
  sel.onchange = () => selectRobot(sel.value);
  if (!S.robots.length) { $('robot-cards').innerHTML = '<div class="empty">Khoá này chưa thấy robot FlashBot nào.</div>'; return; }
  await refreshStatuses();
  const firstOnline = S.robots.find(r => (S.statuses[r.sn] || {}).run_state !== 'OFFLINE') || S.robots[0];
  selectRobot(firstOnline.sn);
  loadErrors();
}

async function refreshStatuses() {
  if (!S.robots.length) return;
  try { S.statuses = (await api('statuses', { sn: S.robots.map(r => r.sn).join(',') })).map; } catch (e) { /* giữ số cũ */ }
  renderKpis(); renderRobotCards();
}

function renderKpis() {
  const st = S.robots.map(r => S.statuses[r.sn] || {});
  const n = f => st.filter(f).length;
  const items = [[S.robots.length, 'Robot FlashBot trong khoá này'],
    [n(s => s.run_state && s.run_state !== 'OFFLINE'), 'Đang online'],
    [n(s => s.run_state === 'BUSY'), 'Đang làm việc'],
    [n(s => s.run_state !== 'OFFLINE' && s.battery != null && s.battery < 20), 'Pin dưới 20%']];
  const k = $('kpis'); k.innerHTML = '';
  items.forEach(([v, t]) => k.appendChild(h('div', { class: 'kpi' }, [h('div', { class: 'n', text: String(v) }), h('div', { class: 't', text: t })])));
}

function renderRobotCards() {
  const box = $('robot-cards'); box.innerHTML = '';
  S.robots.forEach(r => {
    const s = S.statuses[r.sn] || {};
    const st = s.run_state || (s.error ? 'LỖI' : '…');
    const bat = s.battery == null ? 0 : s.battery;
    box.appendChild(h('div', { class: 'rc' + (r.sn === S.sn ? ' sel' : ''), onclick: () => selectRobot(r.sn) }, [
      h('div', { class: 'name', text: robotName(r) }),
      h('div', { class: 'sn', text: r.sn + ' · ' + (r.shop_name || '') }),
      h('div', { html: `<span class="state s-${esc(st)}">${esc(st)}</span> &nbsp;<span class="muted">pin ${s.battery == null ? '—' : bat + '%'}${s.is_charging === 1 ? ' · đang sạc' : ''}</span>` }),
      h('div', { class: 'bat' }, [h('i', { style: `width:${bat}%;background:${bat < 20 ? 'var(--bad)' : 'var(--ok)'}` })]),
    ]));
  });
}

async function selectRobot(sn) {
  S.sn = sn; $('robot-select').value = sn; S.map = null; S.points = [];
  renderRobotCards();
  await loadRobotDetail();
  loadErrands();
  if ($('tab-map').classList.contains('on')) loadMap();
  if ($('tab-control').classList.contains('on')) loadPoints();
}

const RUN = { IDLE: 'Rảnh — nhận lệnh ngay', BUSY: 'Đang làm việc — lệnh gọi sẽ xếp hàng', OFFLINE: 'Ngoại tuyến', DISABLE: 'Tạm không nhận việc (pin yếu / chế độ sạc)' };

async function loadRobotDetail() {
  if (!S.sn) return;
  let d;
  try { d = await api('robot', { sn: S.sn }); } catch (e) { $('robot-detail').innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; return; }
  S.robot = d;
  const s = d.status || {}, p = (d.position || {}).position || {};
  const rows = [
    ['run_state', s.run_state ? `<span class="state s-${esc(s.run_state)}">${esc(s.run_state)}</span> ${esc(RUN[s.run_state] || '')}` : esc(d.status_error || '—')],
    ['battery', s.battery == null ? '—' : s.battery + '%' + (s.is_charging === 1 ? ' (đang sạc)' : '')],
    ['move_state', esc(s.move_state || '—')], ['charge_stage', esc(s.charge_stage || '—')],
    ['Bản đồ / tầng', esc(((d.position || {}).map_name || '—') + ' / ' + ((d.position || {}).floor || '—'))],
    ['Vị trí (m)', (p.x || p.y) ? `x = ${(+p.x).toFixed(2)} · y = ${(+p.y).toFixed(2)}` : esc(d.position_error || 'chưa có')],
    ['Cập nhật', fmtTime(s.timestamp)],
  ];
  const tbl = h('table', {}, rows.map(([k, v]) => h('tr', {}, [h('td', { class: 'k', text: k }), h('td', { html: v })])));
  const box = $('robot-detail'); box.innerHTML = ''; box.appendChild(tbl);
  const doors = (d.doors || {}).door_states || [];
  box.appendChild(h('div', { class: 'muted', style: 'margin-top:10px', text: `Cửa khoang (door_template_id = ${(d.doors || {}).door_template_id ?? '—'})` }));
  const dg = h('div', { class: 'doors' });
  doors.forEach(x => dg.appendChild(h('div', { class: 'door ' + x.state, text: 'Cửa ' + x.door_number + ': ' + x.state })));
  if (!doors.length) dg.appendChild(h('div', { class: 'muted', text: d.doors_error || 'Không có dữ liệu cửa' }));
  box.appendChild(dg);
  renderDoorControls(doors);
}

async function loadErrands() {
  const box = $('errand-list');
  try {
    const d = await api('errands', { sn: S.sn, limit: 8 });
    const list = d.list || [];
    if (!list.length) { box.innerHTML = '<div class="empty">Chưa có việc chạy vặt nào.</div>'; return; }
    box.innerHTML = '<table><tr><th>Lúc giao</th><th>Việc</th><th>Trạng thái</th><th>session_id</th></tr>' +
      list.map(e => `<tr><td>${fmtTime(e.create_time || e.task_time)}</td><td>${esc(((e.tasks || [])[0] || {}).task_name || '')}</td><td>${esc(e.task_status || '')}</td><td class="mono">${esc(e.session_id)}</td></tr>`).join('') + '</table>';
  } catch (e) { box.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; }
}

async function loadErrors() {
  const box = $('error-list');
  try {
    const d = await api('errors', { hours: 24 });
    const list = d.list || [];
    if (!list.length) { box.innerHTML = '<div class="empty">Không có sự cố trong 24 giờ.</div>'; return; }
    box.innerHTML = '<table><tr><th>Lúc</th><th>Robot</th><th>Mức</th><th>Chi tiết</th></tr>' +
      list.slice(0, 12).map(e => `<tr><td>${esc(e.task_time)}</td><td class="mono">${esc(e.sn)}</td><td>${esc(e.error_level)}</td><td>${esc(e.error_type)} — ${esc(e.error_detail)}</td></tr>`).join('') + '</table>';
  } catch (e) { box.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; }
}

// ===================================================================== 2. ĐIỀU KHIỂN
async function loadPoints() {
  if (!S.sn) return;
  if (!S.map) { try { S.map = await api('map', { sn: S.sn }); } catch (e) { toast('Không lấy được điểm: ' + e.message, true); return; } }
  S.points = (S.map.points || []).filter(p => p.name);
  const fill = (id, filter) => {
    const sel = $(id), cur = sel.value; sel.innerHTML = '';
    S.points.filter(filter || (() => true)).forEach(p => sel.appendChild(h('option', { value: p.name, text: `${p.name} (${p.type})` })));
    if (cur) sel.value = cur;
  };
  fill('call-point'); fill('errand-from'); fill('errand-to');
  const from = S.points.find(p => p.type === 'dining_outlet');
  if (from && !$('errand-from').dataset.set) { $('errand-from').value = from.name; $('errand-from').dataset.set = 1; }
}

function renderDoorControls(doors) {
  const box = $('doors-control'); box.innerHTML = '';
  const hatch = $('errand-hatch'); const cur = hatch.value;
  hatch.innerHTML = '<option value="">Tự chọn</option>';
  doors.forEach(d => {
    hatch.appendChild(h('option', { value: d.door_number, text: 'Khoang ' + d.door_number }));
    box.appendChild(h('div', { class: 'door-box' }, [
      h('div', { class: 't', text: 'Cửa ' + d.door_number }),
      h('div', { class: 'muted', text: d.state }),
      h('div', { class: 'row' }, [
        h('button', { class: 'btn sm cmd', onclick: () => command({ action: 'doors', doors: { [d.door_number]: true } }, `MỞ cửa ${d.door_number}`) }, ['Mở']),
        h('button', { class: 'btn sm cmd', onclick: () => command({ action: 'doors', doors: { [d.door_number]: false } }, `ĐÓNG cửa ${d.door_number}`) }, ['Đóng']),
      ]),
    ]));
  });
  if (cur) hatch.value = cur;
  if (!doors.length) box.innerHTML = '<div class="empty">Robot chưa trả trạng thái cửa (offline?).</div>';
  renderBadges();
}

async function command(body, label) {
  const r = S.robots.find(x => x.sn === S.sn);
  if (!confirm(`Gửi lệnh "${label}" tới robot ${robotName(r)} (${S.sn})?\n\nRobot sẽ thực hiện THẬT.`)) return null;
  try {
    const res = await post('/api/command', Object.assign({ sn: S.sn }, body));
    $('exchange').textContent = JSON.stringify(res.exchange, null, 2);
    toast('Đã gửi: ' + res.summary);
    setTimeout(() => { loadRobotDetail(); loadErrands(); refreshStatuses(); pollEvents(); }, 600);
    return res;
  } catch (e) {
    $('exchange').textContent = JSON.stringify(e.exchange || { error: e.message, http_status: e.http_status, trace_id: e.trace_id }, null, 2);
    toast('Lỗi: ' + e.message, true);
    return null;
  }
}

function bindControls() {
  const mapName = () => (S.map || {}).map_name;
  $('btn-call').onclick = async () => {
    const p = S.points.find(x => x.name === $('call-point').value);
    if (!p) return toast('Chọn điểm đến', true);
    const r = await command({ action: 'call', map_name: mapName(), point: p.name, point_type: p.type || 'table' }, 'gọi tới ' + p.name);
    if (r && r.result) { S.callTask = r.result.task_id; $('call-task').textContent = `task_id: ${S.callTask} · state: ${r.result.state}`; }
  };
  $('btn-call-cancel').onclick = () => command({ action: 'cancel_call', task_id: S.callTask || undefined }, 'huỷ lệnh gọi');
  $('btn-call-done').onclick = () => S.callTask ? command({ action: 'complete_call', task_id: S.callTask }, 'hoàn tất lệnh gọi') : toast('Chưa có task_id', true);
  $('btn-errand').onclick = async () => {
    const from = $('errand-from').value, to = $('errand-to').value;
    if (!from || !to || from === to) return toast('Chọn hai điểm khác nhau', true);
    const r = await command({ action: 'errand', map_name: mapName(), pickup: from, dropoff: to,
      hatch_id: $('errand-hatch').value, verification_code: $('errand-code').value.trim(),
      back_mode: $('errand-back').value }, `chạy vặt ${from} → ${to}`);
    if (r && r.result) { S.errandSession = r.result.session_id; $('errand-session').textContent = `session_id: ${S.errandSession} · ${r.result.task_status}`; }
  };
  $('btn-errand-cancel').onclick = () => S.errandSession ? command({ action: 'errand_cancel', session_id: S.errandSession }, 'huỷ việc chạy vặt') : toast('Chưa có session_id', true);
  $('btn-charge').onclick = () => command({ action: 'charge' }, 'về trạm sạc');
  $('btn-report').onclick = () => command({ action: 'report_position', interval: 2, times: 30 }, 'báo vị trí');
  renderBadges();
}

// ===================================================================== 3. BẢN ĐỒ
const LAYERS = [['img', 'Ảnh laser'], ['track', 'Đường chạy'], ['wall', 'Tường ảo'], ['zone', 'Vùng'],
  ['point', 'Điểm'], ['label', 'Tên điểm'], ['robot', 'Robot']];
const ON = Object.fromEntries(LAYERS.map(([k]) => [k, true]));
const PCOLOR = { table: '#E8654A', dining_outlet: '#F2B134', parking: '#9AA0A6', transit: '#8E7CC3', usher: '#5DBB8A', return_point: '#C77DBA' };
const V = { scale: 1, dx: 0, dy: 0, img: null };

async function loadMap() {
  if (!S.sn) return;
  const lay = $('layers');
  if (!lay.children.length) LAYERS.forEach(([k, t]) => {
    const c = h('input', { type: 'checkbox' }); c.checked = true; c.onchange = () => { ON[k] = c.checked; drawMap(); };
    lay.appendChild(h('label', {}, [c, t]));
  });
  try { S.map = await api('map', { sn: S.sn }); } catch (e) { $('map-title').textContent = 'Không lấy được bản đồ: ' + e.message; return; }
  const m = S.map, g = m.geometry;
  $('map-title').textContent = 'Bản đồ ' + m.map_name;
  $('map-meta').textContent = `${g.width}×${g.height} px · ${g.resolution} m/px · ${g.width_m}×${g.height_m} m · gốc (${g.origin.map(v => v.toFixed(2)).join(', ')}) · ${(m.points || []).length} điểm`;
  const qs = 'sn=' + encodeURIComponent(S.sn);
  $('dl-png').href = '/api/map-export?format=png&' + qs;
  $('dl-json').href = '/api/map-export?format=json&' + qs;
  $('dl-svg').href = '/api/map-export?format=svg&' + qs;
  V.img = null;
  if (m.image) { const im = new Image(); im.onload = () => { V.img = im; fitMap(); }; im.src = m.image; } else fitMap();
  renderPointTable();
}

function fitMap() {
  const c = $('map-canvas'), g = S.map.geometry;
  c.width = c.clientWidth * devicePixelRatio; c.height = c.clientHeight * devicePixelRatio;
  V.scale = Math.min(c.width / g.width, c.height / g.height) * 0.96;
  V.dx = (c.width - g.width * V.scale) / 2; V.dy = (c.height - g.height * V.scale) / 2;
  drawMap();
}

// toạ độ mét → toạ độ điểm ảnh của ảnh bản đồ (cùng công thức với map_tools.py)
function w2p(x, y) { const g = S.map.geometry; return [(x - g.origin[0]) / g.resolution, g.height - (y - g.origin[1]) / g.resolution]; }
function p2w(c, r) { const g = S.map.geometry; return [g.origin[0] + c * g.resolution, g.origin[1] + (g.height - r) * g.resolution]; }

function drawMap() {
  if (!S.map) return;
  const c = $('map-canvas'), x = c.getContext('2d'), g = S.map.geometry, k = V.scale;
  x.setTransform(1, 0, 0, 1, 0, 0); x.clearRect(0, 0, c.width, c.height);
  x.setTransform(k, 0, 0, k, V.dx, V.dy);
  x.imageSmoothingEnabled = false;
  if (ON.img && V.img) x.drawImage(V.img, 0, 0, g.width, g.height);
  else { x.fillStyle = '#ffffff'; x.fillRect(0, 0, g.width, g.height); }
  const lw = 1.6 / k * devicePixelRatio;
  const path = pts => { x.beginPath(); pts.forEach(([cc, rr], i) => i ? x.lineTo(cc, rr) : x.moveTo(cc, rr)); };
  (S.map.zones || []).forEach(z => { if (!ON.zone) return;
    path((z.zone_node_list || []).map(n => w2p(n.vector_list[0], n.vector_list[1]))); x.closePath();
    x.fillStyle = 'rgba(142,124,195,.18)'; x.fill(); x.strokeStyle = '#8E7CC3'; x.lineWidth = lw; x.stroke(); });
  (S.map.elements || []).forEach(e => {
    if (!e.pixel || !Array.isArray(e.pixel[0])) return;
    if (e.type === 'track' && ON.track) { path(e.pixel); x.strokeStyle = 'rgba(79,149,224,.75)'; x.lineWidth = lw * 2; x.setLineDash([]); x.stroke(); }
    if (e.type === 'virtual_wall' && ON.wall) { path(e.pixel); x.strokeStyle = '#E14040'; x.lineWidth = lw * 1.6; x.setLineDash([6 / k, 4 / k]); x.stroke(); x.setLineDash([]); }
  });
  (S.map.elements || []).forEach(e => {
    if (!e.pixel || Array.isArray(e.pixel[0]) || !ON.point) return;
    const [cc, rr] = e.pixel, s = 7 / k * devicePixelRatio;
    if (e.type === 'charging_pile' || e.type === 'chargeWorkStation') { x.fillStyle = '#2FA579'; x.fillRect(cc - s, rr - s, 2 * s, 2 * s); label(x, 'Trạm sạc', cc, rr, k); }
    else if (e.type === 'elevator' || e.type === 'elev_waiter') { x.strokeStyle = '#7a5fc7'; x.lineWidth = lw; x.strokeRect(cc - s, rr - s, 2 * s, 2 * s); if (e.type === 'elevator') label(x, e.name || 'Thang máy', cc, rr, k); }
  });
  (S.map.points || []).forEach(p => {
    if (!p.pixel || !ON.point) return;
    const [cc, rr] = p.pixel, s = 6 / k * devicePixelRatio;
    x.beginPath(); x.arc(cc, rr, s, 0, 7); x.fillStyle = PCOLOR[p.type] || '#E8654A'; x.fill();
    x.lineWidth = lw; x.strokeStyle = '#111'; x.stroke();
    if (ON.label) label(x, p.name, cc, rr, k);
  });
  const pos = S.map.robot_position;
  if (pos && ON.robot) {
    const [cc, rr] = w2p(pos.x, pos.y), s = 9 / k * devicePixelRatio;
    x.beginPath(); x.arc(cc, rr, s * 2, 0, 7); x.fillStyle = 'rgba(232,54,54,.25)'; x.fill();
    x.beginPath(); x.arc(cc, rr, s, 0, 7); x.fillStyle = '#E83636'; x.fill(); x.lineWidth = lw * 1.5; x.strokeStyle = '#fff'; x.stroke();
    label(x, 'Robot', cc, rr - s * 2.6, k, '#C13F28');
  }
}
function label(x, t, cc, rr, k, color) {
  const f = 12 / k * devicePixelRatio;
  x.font = `600 ${f}px Segoe UI, Arial`; x.textAlign = 'center';
  x.lineWidth = 3.5 / k * devicePixelRatio; x.strokeStyle = '#fff'; x.strokeText(t, cc, rr - f * 0.8);
  x.fillStyle = color || '#1f2328'; x.fillText(t, cc, rr - f * 0.8);
}

function renderPointTable() {
  const pts = S.map.points || [];
  $('point-table').innerHTML = pts.length ? '<table><tr><th>Tên điểm</th><th>Loại</th><th>x (m)</th><th>y (m)</th><th>pixel</th></tr>' +
    pts.map(p => `<tr><td>${esc(p.name)}</td><td>${esc(p.type)}</td><td>${(+p.x).toFixed(3)}</td><td>${(+p.y).toFixed(3)}</td><td class="mono">${p.pixel ? p.pixel.join(', ') : '—'}</td></tr>`).join('') + '</table>'
    : '<div class="empty">Bản đồ chưa có điểm.</div>';
}

(function mapInteractions() {
  const c = $('map-canvas'); let drag = null;
  c.addEventListener('wheel', e => {
    if (!S.map) return; e.preventDefault();
    const r = c.getBoundingClientRect(), mx = (e.clientX - r.left) * devicePixelRatio, my = (e.clientY - r.top) * devicePixelRatio;
    const f = e.deltaY < 0 ? 1.2 : 1 / 1.2;
    V.dx = mx - (mx - V.dx) * f; V.dy = my - (my - V.dy) * f; V.scale *= f; drawMap();
  }, { passive: false });
  c.addEventListener('mousedown', e => { drag = { x: e.clientX, y: e.clientY, dx: V.dx, dy: V.dy }; });
  addEventListener('mouseup', () => drag = null);
  c.addEventListener('mousemove', e => {
    if (!S.map) return;
    if (drag) { V.dx = drag.dx + (e.clientX - drag.x) * devicePixelRatio; V.dy = drag.dy + (e.clientY - drag.y) * devicePixelRatio; drawMap(); }
    const r = c.getBoundingClientRect();
    const col = ((e.clientX - r.left) * devicePixelRatio - V.dx) / V.scale, row = ((e.clientY - r.top) * devicePixelRatio - V.dy) / V.scale;
    const [wx, wy] = p2w(col, row);
    $('map-coord').textContent = `x = ${wx.toFixed(2)} m · y = ${wy.toFixed(2)} m   |   pixel [${col.toFixed(0)}, ${row.toFixed(0)}]`;
  });
  $('map-fit').onclick = () => S.map && fitMap();
  addEventListener('resize', () => S.map && $('tab-map').classList.contains('on') && fitMap());
})();

// ===================================================================== SỰ KIỆN
function eventSummary(e) {
  const d = e.data || {};
  switch (e.callback_type) {
    case 'notifyDoorsState': return (d.door_states || []).map(x => x.door_number + ':' + x.state).join(' · ');
    case 'notifyErrandStatus': return `${d.session_id} → ${d.task_status || ''}`;
    case 'notifyCustomCall': return `${d.task_id} → ${d.state} (${d.point || ''})`;
    case 'notifyRobotStatus': return `${d.run_state} · pin ${d.battery}% · ${d.move_state || ''}`;
    case 'notifyRechargeStatus': return `${d.task_id} → ${d.status}`;
    case 'notifyRobotPose': return `x=${(+d.x).toFixed(2)} y=${(+d.y).toFixed(2)} yaw=${d.yaw}`;
    case 'robotStatus': return d.run_status;
    default: return '';
  }
}
async function pollEvents() {
  try {
    const d = await api('events', { after: S.lastEvent });
    (d.list || []).forEach(e => { S.events.unshift(e); S.lastEvent = Math.max(S.lastEvent, e.id); });
    S.events = S.events.slice(0, 200);
  } catch (e) { return; }
  const box = $('event-list');
  $('events-note').textContent = S.sys.demo
    ? 'Chế độ demo: sự kiện do bộ giả lập sinh ra khi bạn ra lệnh, đúng cấu trúc callback của hãng.'
    : (S.sys.callback.enabled ? `Đang nghe cổng ${S.sys.callback.port}${S.sys.callback.path} — URL này phải được khai trong đơn xin ApiAppKey.` : 'Callback đang tắt (config.json → callback.enabled).');
  if (!S.events.length) { box.innerHTML = '<div class="empty">Chưa nhận sự kiện nào.</div>'; return; }
  box.innerHTML = '';
  S.events.forEach(e => {
    const pre = h('pre', { class: 'code hidden', text: JSON.stringify({ callback_type: e.callback_type, data: e.data }, null, 2) });
    box.appendChild(h('div', { class: 'ev' }, [
      h('div', { class: 'h', onclick: () => pre.classList.toggle('hidden') }, [
        h('span', { class: 'muted', text: fmtTime(e.received_at) }),
        h('span', { class: 'type', text: e.callback_type }),
        h('span', { class: 'mono muted', text: (e.data || {}).sn || '' }),
        h('span', { text: eventSummary(e) }),
      ]), pre]));
  });
}

// ===================================================================== CÀI ĐẶT
function renderSettings() {
  const s = S.sys;
  $('settings-info').innerHTML = '<table>' + [
    ['Chế độ', s.demo ? 'DEMO — robot giả lập, không gọi PUDU' : 'Kết nối thật'],
    ['ApiAppKey', s.has_key ? `<span class="mono">${esc(s.key_masked)}</span>` : 'Chưa có'],
    ['ApiAppSecret', s.has_key ? 'Đã có (chỉ máy chủ giữ, không gửi ra trình duyệt)' : 'Chưa có'],
    ['Nút máy chủ PUDU', `${esc(s.region)} · <span class="mono">${esc(s.host)}</span>`],
    ['Callback', s.callback.enabled ? `cổng ${s.callback.port} · đường dẫn <span class="mono">${esc(s.callback.path)}</span> · CallbackCode: ${s.callback.has_code ? 'đã có' : 'chưa có'}` : 'Tắt'],
    ['Phiên bản app', esc(s.version)],
  ].map(([k, v]) => `<tr><td class="k">${k}</td><td>${v}</td></tr>`).join('') + '</table>';
  const cb = $('allow-commands'); cb.checked = !!s.allow_commands;
  cb.onchange = async () => {
    if (cb.checked && !confirm('Bật quyền ra lệnh? Từ giờ mọi nút trong tab Điều khiển sẽ làm robot cử động thật.')) { cb.checked = false; return; }
    await post('/api/settings', { allow_commands: cb.checked });
    S.sys = await api('system'); renderBadges(); toast(cb.checked ? 'Đã bật quyền ra lệnh' : 'Đã về chế độ chỉ đọc');
  };
}

boot().catch(e => toast(e.message, true));
