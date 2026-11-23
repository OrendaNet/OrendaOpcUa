'use strict';
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const { timingSafeEqual, X509Certificate } = require('node:crypto');
const { createRuntimeClient, authenticateEdgeRequest } = require('@orenda/box-sdk');
const { Monitor } = require('./lib/monitor');

const assets = new Map(Object.entries({
  '/':'text/html', '/index.html':'text/html', '/style.css':'text/css', '/app.js':'text/javascript',
  '/orendabox.png':'image/png', '/montserrat.woff2':'font/woff2'
}));
function equal(a, b) {
  const x = Buffer.from(a || ''), y = Buffer.from(b || '');
  return x.length === y.length && timingSafeEqual(x, y);
}
function output(monitor, box) {
  const app = JSON.stringify(process.env.ORENDA_APP_ID || 'orenda-opcua');
  const lines = box ? [] : ['# TYPE orenda_opcua_connected gauge', 'orenda_opcua_connected ' + Number(monitor.online())];
  for (const {name, value} of monitor.snapshot()) {
    const labels = 'app_id=' + app + ',channel=' + JSON.stringify(name);
    lines.push(box ? 'orenda_app_channel_good{' + labels + '} ' + Number(value !== null) : 'orenda_opcua_channel_good{channel=' + JSON.stringify(name) + '} ' + Number(value !== null));
    if (value !== null) lines.push(box ? 'orenda_app_channel_value{' + labels + '} ' + value : 'orenda_opcua_value_' + name + ' ' + value);
  }
  return lines.join('\n') + '\n';
}
async function body(req) {
  if (req.headers['content-type']?.split(';')[0] !== 'application/json') throw Error('Send a JSON object.');
  const chunks = []; let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 32768) throw Error('Request is too large.');
    chunks.push(chunk);
  }
  const data = JSON.parse(Buffer.concat(chunks).toString());
  if (!data || Array.isArray(data) || typeof data !== 'object') throw Error('Send a JSON object.');
  return data;
}
async function main() {
  const monitor = new Monitor(process.env.ORENDA_APP_DATA_DIR || '/data',
    process.env.OPCUA_COLLECTOR || path.join(__dirname, 'bin', process.platform === 'win32' ? 'collector.exe' : 'collector'), createRuntimeClient());
  await monitor.start();
  const server = http.createServer(async (req, res) => {
    const send = (status, obj, type = 'application/json') => {
      res.writeHead(status, {'Content-Type':type, 'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff'});
      res.end(type === 'application/json' ? JSON.stringify(obj) : obj);
    };
    try {
      const url = new URL(req.url, 'http://localhost'), route = url.pathname;
      if (req.method === 'GET' && route === '/health') return send(200, {ok:true});
      if (req.method === 'GET' && route === '/metrics') {
        if (!equal(req.headers.authorization, 'Bearer ' + monitor.cfg.token)) return send(401, {error:'Scrape credential required'});
        monitor.externalScrape = new Date().toISOString();
        return send(200, output(monitor,false), 'text/plain; version=0.0.4; charset=utf-8');
      }
      const identity = authenticateEdgeRequest(req.headers);
      if (!identity) return send(401, {error:'Open the app from Edge Console.'});
      const manage = identity.roles.some(role => role === 'admin' || role === 'owner');
      if (!manage && (req.method !== 'GET' || ['/api/token','/api/preview','/api/browse'].includes(route))) return send(403, {error:'An Edge administrator or owner is required.'});
      if (req.method === 'GET') {
        if (assets.has(route)) return send(200, await fs.readFile(path.join(__dirname,'public',route === '/' ? 'index.html' : route.slice(1))), assets.get(route));
        if (route === '/api/state') return send(200, monitor.state(manage));
        if (route === '/api/output') return send(200, output(monitor,true), 'text/plain; charset=utf-8');
        if (route === '/api/token') return send(200, {token:monitor.cfg.token});
        if (route === '/api/certificate') {
          res.setHeader('Content-Disposition','attachment; filename="orenda-opcua.der"');
          return send(200, new X509Certificate(monitor.cfg.cert).raw, 'application/pkix-cert');
        }
        if (route === '/api/browse' || route === '/api/preview') return send(200, await monitor.serialize(() => monitor.operation(route.slice(5), Object.fromEntries(url.searchParams))));
      } else if (req.method === 'POST' && /^\/api\/(discover|connect|interval|add|addMany|remove|notifier)$/.test(route)) {
        const data = await body(req);
        return send(200, await monitor.serialize(() => monitor.operation(route.slice(5), data)));
      }
      send(404, {error:'Unknown route'});
    } catch (error) { send(400, {error:String(error.message).slice(0,400)}); }
  });
  server.listen(Number(process.env.PORT ?? 3101), process.env.HOST || '0.0.0.0', () => console.log('Listening on port', server.address().port));
  for (const signal of ['SIGINT','SIGTERM']) process.once(signal, () => {monitor.stop();server.closeAllConnections();server.close(() => process.exit(0));});
}
main().catch(err => {console.error('Startup failed:', err.message);process.exitCode = 1;process.exit(1);});
