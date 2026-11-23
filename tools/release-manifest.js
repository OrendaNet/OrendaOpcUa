'use strict';
const fs = require('node:fs');
const manifest = require('../orenda-app.json');
const version = fs.readFileSync('VERSION','utf8').trim(), digest = process.env.IMAGE_DIGEST || '';
if (!/^sha256:[0-9a-f]{64}$/.test(digest) || !/^\d+\.\d+\.\d+$/.test(version)) throw Error('Invalid release version or digest');
manifest.versions = [{version, digest, image:'ghcr.io/orendanet/orenda-opcua@' + digest, architectures:['arm64'],
  minPlatformVersion:manifest.metadata.orenda.minPlatformVersion, releaseNotes:'Native OPC UA collection and automatic Box Prometheus integration.'}];
fs.mkdirSync('dist',{recursive:true});
fs.writeFileSync('dist/orenda-app.json',JSON.stringify(manifest,null,2)+'\n');
