// pudu-client.mjs — ký và gọi PUDU Cloud API bằng Node.js 18+ (không cần npm install).
// Cùng thuật toán với pudu_client.py. Dùng làm điểm xuất phát nếu nền tảng của bạn chạy Node.
//
//   PUDU_APP_KEY=... PUDU_APP_SECRET=... node examples/nodejs/pudu-client.mjs <SN>
//
// ⚠ Chỉ chạy ở PHÍA MÁY CHỦ. Đưa đoạn này vào trình duyệt là lộ ApiAppSecret.
import crypto from 'node:crypto';

const HOSTS = { sea: 'css-open-platform.pudutech.com', cn: 'open-platform.pudutech.com',
                de: 'csg-open-platform.pudutech.com', us: 'csu-open-platform.pudutech.com' };
const PREFIX = '/pudu-entry';

export function buildQuery(params) {
  // xếp tên tham số theo bảng chữ cái — sai thứ tự là sai chữ ký
  return Object.keys(params).filter(k => params[k] !== undefined && params[k] !== null && params[k] !== '')
    .sort().map(k => `${k}=${encodeURIComponent(Array.isArray(params[k]) ? params[k].join(',') : String(params[k]))}`)
    .join('&');
}

export function contentMd5(body) {
  // base64 của chuỗi HEX md5 — không phải base64 của md5 nhị phân
  return body ? Buffer.from(crypto.createHash('md5').update(body, 'utf8').digest('hex')).toString('base64') : '';
}

export async function puduRequest({ key, secret, region = 'sea', method = 'GET', path, params = {}, body }) {
  method = method.toUpperCase();
  const fullPath = path.startsWith(PREFIX) ? path : PREFIX + path;
  const query = method === 'GET' ? buildQuery(params) : '';
  const bodyText = method === 'GET' ? '' : JSON.stringify(body || {});
  const md5 = contentMd5(bodyText);
  const xDate = new Date().toUTCString();                       // "Mon, 02 Jan 2006 15:04:05 GMT"
  const pathToSign = fullPath + (query ? '?' + decodeURIComponent(query) : '');
  const toSign = `x-date: ${xDate}\n${method}\napplication/json\napplication/json\n${md5}\n${pathToSign}`;
  const signature = crypto.createHmac('sha1', secret).update(toSign, 'utf8').digest('base64');
  const headers = {
    Accept: 'application/json', 'Content-Type': 'application/json', 'x-date': xDate,
    Authorization: `hmac id="${key}", algorithm="hmac-sha1", headers="x-date", signature="${signature}"`,
  };
  if (md5) headers['Content-MD5'] = md5;
  const res = await fetch(`https://${HOSTS[region]}${fullPath}${query ? '?' + query : ''}`,
    { method, headers, body: bodyText || undefined });
  const js = await res.json();
  if (res.status !== 200 || !['SUCCESS', 'OK', ''].includes(String(js.message || '').toUpperCase())) {
    throw Object.assign(new Error(js.message || `HTTP ${res.status}`), { status: res.status, body: js });
  }
  return js;
}

// ---- chạy thử: đọc trạng thái + cửa khoang của một robot (chỉ đọc)
if (import.meta.url === `file://${process.argv[1].replace(/\\/g, '/')}` || process.argv[1]?.endsWith('pudu-client.mjs')) {
  const sn = process.argv[2];
  const key = process.env.PUDU_APP_KEY, secret = process.env.PUDU_APP_SECRET;
  if (!sn || !key || !secret) {
    console.log('Cách dùng: PUDU_APP_KEY=... PUDU_APP_SECRET=... node pudu-client.mjs <SN>');
    process.exit(1);
  }
  const status = await puduRequest({ key, secret, path: '/open-platform-service/v2/status/get_by_sn', params: { sn } });
  console.log('Trạng thái:', status.data);
  const doors = await puduRequest({ key, secret, path: '/open-platform-service/v1/door_state', params: { sn } });
  console.log('Cửa khoang:', doors.data);
}
