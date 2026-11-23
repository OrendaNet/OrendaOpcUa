'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { NativeClient } = require('./native');

const metricPattern = /^[a-z][a-z0-9_]{0,62}$/;
const networkDenied = 'Open Edge Console → Apps → OPC UA Integrator → Permissions and grant outbound network access.';
function suffix(label) {
  const words = label.replace(/([a-z])([A-Z])/g, '$1_$2').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(-52);
  return /^[a-z]/.test(words) ? words : 'channel_' + words;
}
function stable(id, namespaces) {
  const parts = /^(?:ns=(\d+);)?([isgb]=.+)$/s.exec(id);
  if (!parts || !namespaces[Number(parts[1] || 0)]) throw Error('Unknown controller namespace');
  return { namespace: namespaces[Number(parts[1] || 0)], identifier: parts[2] };
}
function resolve(ref, namespaces) {
  const ns = namespaces.indexOf(ref.namespace);
  return ns < 0 ? null : (ns ? 'ns=' + ns + ';' : '') + ref.identifier;
}
class Monitor {
  constructor(directory, binary, runtime) {
    this.directory = directory;
    this.native = new NativeClient(binary);
    this.runtime = runtime;
    this.chain = Promise.resolve();
    this.queued = 0;
    this.generation = null;
    this.namespaces = [];
    this.samples = {};
    this.events = [];
    this.eventState = 'Off';
    this.exportState = 'Waiting for Box runtime';
    this.error = '';
    this.networkUntil = 0;
    this.lastRead = this.lastScrape = this.externalScrape = null;
    this.retry = 1000;
  }
  async start() {
    await fs.mkdir(this.directory, { recursive: true, mode: 0o700 });
    this.file = path.join(this.directory, 'settings.json');
    try { this.cfg = JSON.parse(await fs.readFile(this.file, 'utf8')); }
    catch (err) {
      if (err.code !== 'ENOENT') throw err;
      const identity = await this.native.call('identity');
      await this.save({ ...identity, token: crypto.randomBytes(36).toString('base64url'), revision: 0, connection: null, interval: 2, channels: [], notifier: null });
    }
    await this.updateGrants();
    this.publishLoop();
    this.readLoop();
  }
  async save(cfg) {
    const tmp = this.file + '.tmp';
    const file = await fs.open(tmp, 'w', 0o600);
    try { await file.writeFile(JSON.stringify(cfg)); await file.sync(); }
    finally { await file.close(); }
    await fs.rename(tmp, this.file);
    this.cfg = cfg;
  }
  change(fields) { return this.save({ ...this.cfg, ...fields, revision: this.cfg.revision + 1 }); }
  serialize(fn) {
    if (this.queued >= 16) return Promise.reject(Error('Controller is busy. Try again.'));
    this.queued++;
    const next = this.chain.then(fn).finally(() => this.queued--);
    this.chain = next.catch(() => {});
    return next;
  }
  async updateGrants() {
    try {
      const context = await this.runtime.context();
      this.networkUntil = context.capabilities.includes('network:outbound') ? Date.now() + 20000 : 0;
    } catch { this.networkUntil = 0; }
  }
  network() { if (Date.now() >= this.networkUntil) throw Error(networkDenied); }
  online() { return Boolean(this.generation && this.lastRead && Date.now() - Date.parse(this.lastRead) < (this.cfg.interval * 2 + 6) * 1000 && Date.now() < this.networkUntil); }
  snapshot() {
    return this.cfg.channels.map(({metric: name}) => {
      const v = this.samples[name];
      return { name, value: this.online() && v?.good && v.numeric ? Number(v.value) : null };
    });
  }
  async publishLoop() {
    if (this.stopping) return;
    await this.updateGrants();
    try {
      const result = await this.runtime.metrics.publish(this.snapshot());
      this.lastScrape = result.lastScrape;
      this.exportState = this.lastScrape ? 'Publishing to Box' : 'Awaiting first Box scrape';
    } catch (err) {
      this.exportState = err.status === 403 ? 'Approve measurement export in Edge Console' : 'Box collector unavailable. Retrying automatically.';
    }
    if (!this.stopping) this.exportTimer = setTimeout(() => this.publishLoop(), 5000);
  }
  async session(connection) {
    this.network();
    const {cert, key, uri} = this.cfg;
    const data = await this.native.call('connect', {...connection, cert, key, uri});
    this.namespaces = data.namespaces;
    this.generation = crypto.randomUUID();
    this.samples = {}; this.lastRead = null; this.error = ''; this.retry = 1000;
    await this.subscribe();
  }
  async subscribe() {
    const ref = this.cfg.notifier;
    if (!ref) { this.eventState = 'Off'; return; }
    try {
      const node = resolve(ref, this.namespaces);
      if (!node) throw Error('Event source namespace is unavailable');
      await this.native.call('notifier', {node});
      this.eventState = 'Listening';
    } catch (err) { this.eventState = err.message; }
  }
  async readLoop() {
    if (this.stopping) return;
    let delay = this.cfg.interval * 1000;
    await this.serialize(async () => {
      if (!this.cfg.connection) return;
      try {
        this.network();
        if (!this.generation) await this.session(this.cfg.connection);
        const active = [], samples = {};
        for (const c of this.cfg.channels) {
          const id = resolve(c, this.namespaces);
          if (id) active.push([c.metric, id]);
          else samples[c.metric] = { quality: 'NamespaceMissing', good: false, numeric: false };
        }
        const data = await this.native.call('read', {nodes: active.map(([, id]) => id)});
        active.forEach(([metric], i) => { samples[metric] = data.values[i]; });
        this.samples = samples; this.lastRead = new Date().toISOString(); this.error = ''; this.retry = 1000;
        this.events = [...data.events.reverse(), ...this.events].slice(0, 200);
        if (data.eventError) { this.eventState = data.eventError; await this.subscribe(); }
      } catch (err) {
        this.error = err.message; this.samples = {}; this.generation = null; this.lastRead = null;
        if (this.cfg.notifier) this.eventState = 'Waiting for controller';
        await this.native.call('close').catch(() => {});
        delay = this.retry; this.retry = Math.min(30000, this.retry * 2);
      }
    }).catch(() => {});
    if (!this.stopping) this.readTimer = setTimeout(() => this.readLoop(), delay);
  }
  state(manage) {
    const c = this.cfg, conn = c.connection;
    return { appId: process.env.ORENDA_APP_ID || 'orenda-opcua', manage, revision: c.revision, interval: c.interval,
      generation: this.generation, connection: conn && {url: conn.url, username: conn.username, fingerprint: conn.fingerprint},
      online: this.online(), error: this.error, channels: c.channels.map(channel => ({...channel, nodeId: this.generation ? resolve(channel, this.namespaces) : null})), samples: this.online() ? this.samples : {},
      lastRead: this.lastRead, lastScrape: this.lastScrape, externalScrape: this.externalScrape, exportState: this.exportState,
      events: this.events, notifier: c.notifier, eventState: this.eventState };
  }
  async operation(op, data) {
    const cfg = this.cfg;
    if (op === 'discover') { this.network(); return this.native.call(op, {url: data.url}); }
    if (op === 'browse' || op === 'preview') {
      this.network();
      const result = await this.native.call(op, {node: data.id || 'i=85', offset: Number(data.offset || 0)});
      return { ...result, generation: this.generation };
    }
    if (data.revision !== cfg.revision) throw Error('Configuration changed. Refresh and try again.');
    if (op === 'connect') {
      const { url, fingerprint, username = '', password = '' } = data;
      if (![url, fingerprint, username, password].every(v => typeof v === 'string' && v.length <= 768) || !/^[a-f0-9]{64}$/.test(fingerprint)) throw Error('Discover and confirm the server certificate first.');
      if (cfg.connection && cfg.connection.url !== url && (cfg.channels.length || cfg.notifier)) throw Error('Remove selected channels and the event source before changing controllers.');
      const conn = {url, fingerprint, username, password};
      await this.session(conn); await this.change({connection: conn});
    } else if (op === 'interval') {
      if (!Number.isInteger(data.interval) || data.interval < 1 || data.interval > 60) throw Error('Choose a read interval from 1 to 60 seconds.');
      await this.change({interval: data.interval});
    } else if (op === 'remove') {
      await this.change({channels: cfg.channels.filter(c => c.metric !== data.metric)});
      delete this.samples[data.metric];
    } else if (op === 'notifier') {
      this.network();
      let ref = null;
      if (data.id) {
        const info = await this.native.call('preview', {node:data.id});
        if (!info.events) throw Error('Select an object that supports event subscriptions.');
        ref = {...stable(info.id, this.namespaces), name: info.name};
      }
      await this.native.call('notifier', {node: data.id || ''});
      await this.change({notifier: ref}); this.eventState = ref ? 'Listening' : 'Off';
    } else if (op === 'add' || op === 'addMany') {
      this.network();
      if (op === 'addMany' && (!this.generation || data.generation !== this.generation)) throw Error('Controller reconnected. Select the channels again.');
      const batch = op === 'addMany' ? data.nodes : [{id: data.id, metric: data.metric}];
      if (!Array.isArray(batch) || !batch.length || batch.length > 32 || batch.length + cfg.channels.length > 128) throw Error('Select up to 32 channels at once and 128 per installation.');
      const channels = [...cfg.channels];
      for (const choice of batch) {
        if (!choice || typeof choice.id !== 'string' || (op === 'addMany' && typeof choice.path !== 'string')) throw Error('Invalid channel selection');
        const info = await this.native.call('preview', {node: choice.id});
        if (!info.numeric || !info.good) throw Error(info.name + ' requires a readable numeric or Boolean value (' + info.quality + ').');
        const ref = stable(info.id, this.namespaces);
        if (channels.some(c => c.namespace === ref.namespace && c.identifier === ref.identifier)) throw Error(info.name + ' is already selected.');
        const base = suffix(choice.path || info.name);
        let metric = op === 'add' ? choice.metric : base;
        if (op === 'addMany') for (let i = 2; channels.some(c => c.metric === metric); i++) metric = base + '_' + i;
        if (!metricPattern.test(metric) || channels.some(c => c.metric === metric)) throw Error('Choose a unique metric name starting with a lowercase letter.');
        channels.push({...ref, name: info.name, metric});
      }
      await this.change({channels});
    } else throw Error('Unknown application action');
    return {ok:true};
  }
  stop() { this.stopping = true; clearTimeout(this.readTimer); clearTimeout(this.exportTimer); this.native.stop(); }
}
module.exports = {Monitor};
