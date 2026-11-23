'use strict';
const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');

class NativeClient {
  constructor(binary) {
    this.binary = binary;
    this.pending = new Map();
    this.sequence = 0;
  }

  start() {
    const child = spawn(this.binary, [], { stdio: ['pipe', 'pipe', 'inherit'], windowsHide: true,
      env: { SystemRoot: process.env.SystemRoot || '', GOMEMLIMIT: '64MiB', GOMAXPROCS: '2' } });
    this.child = child;
    const lines = createInterface({ input: child.stdout });
    const stop = () => {
      if (this.child !== child) return;
      this.child = null;
      lines.close();
      for (const task of this.pending.values()) task.reject(new Error('Native collector stopped. Reconnecting.'));
      this.pending.clear();
    };
    child.once('error', stop);
    child.once('exit', stop);
    child.stdin.on('error', stop);
    lines.on('line', line => {
      let reply;
      try { reply = JSON.parse(line); } catch { child.kill(); return; }
      const task = this.pending.get(reply.seq);
      if (!task) return;
      this.pending.delete(reply.seq);
      if (reply.error) task.reject(new Error(reply.error));
      else task.resolve(reply.result);
    });
  }

  async call(op, fields = {}) {
    if (!this.child) this.start();
    const seq = ++this.sequence;
    const child = this.child;
    const timer = setTimeout(() => child.kill(), 22000);
    try {
      return await new Promise((resolve, reject) => {
        this.pending.set(seq, { resolve, reject });
        child.stdin.write(JSON.stringify({ ...fields, op, seq }) + '\n');
      });
    } finally { clearTimeout(timer); }
  }

  stop() { this.child?.kill(); }
}
module.exports = { NativeClient };
