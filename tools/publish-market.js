'use strict';
const fs = require('node:fs');
const base = 'https://apps.orendanet.com', github = 'https://github.com/OrendaNet/OrendaOpcUa';
async function api(path, {body, method, token, optional} = {}) {
  const response = await fetch(base + path, {redirect:'error', signal:AbortSignal.timeout(20000),
    method: method || (body ? 'POST' : 'GET'), headers: {'Content-Type':'application/json', ...(token && {Authorization:'Bearer ' + token})},
    body: body && JSON.stringify(body)});
  if (optional && response.status === 404) return null;
  if (!response.ok) throw Error(path + ' returned HTTP ' + response.status);
  return response.json();
}
async function main() {
  const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const {id, name, description, category, runtime, metadata} = manifest, [v] = manifest.versions;
  if (id !== 'orenda-opcua' || manifest.versions.length !== 1 || !/^sha256:[0-9a-f]{64}$/.test(v.digest) || v.image !== 'ghcr.io/orendanet/orenda-opcua@' + v.digest) throw Error('Invalid immutable app release');
  const listing = '/public/v1/apps/' + id;
  if ((await api(listing,{optional:true}))?.latestVersion?.digest === v.digest) return console.log('Published digest verified', base + listing);
  const {ORENDA_REPO_ADMIN_USERNAME:username, ORENDA_REPO_ADMIN_PASSWORD:password} = process.env;
  if (!username || !password) throw Error('The market publishing workflow requires its assigned Orenda Apps credentials.');
  const {token} = await api('/admin/v1/auth/login',{body:{username,password}});
  const tenant = '/admin/v1/tenants/official', apps = tenant + '/apps', app = apps + '/' + id;
  const existing = await api(app,{token,optional:true});
  await api(existing ? app : apps,{token,method:existing ? 'PUT' : 'POST',body:{id,name,description,category,runtime,metadata,port:3101}});
  const img = 'https://raw.githubusercontent.com/OrendaNet/OrendaOpcUa/' + v.version + '/docs/screens/';
  await api(app + '/storefront',{token, method:'PUT', body:{slug:id,headline:name,shortDescription:description,
    longDescription:'Connect a controller, approve its certificate and select signals. The Box collects selected readings automatically. Inspect channel quality, controller timestamps and recent events in Edge Console.',
    categories:['industrial','monitoring'], tags:['opc-ua','prometheus','robotics','telemetry'], screenshots:[img+'overview.png',img+'channels.png'], documentationUrl:github+'#readme',supportUrl:github+'/issues'}});
  const previous = (await api(app+'/versions',{token})).versions.find(x => x.version === v.version);
  if (previous && previous.digest !== v.digest) throw Error('Market version already uses a different digest');
  if (!previous) await api(app+'/versions',{token,body:v});
  const baseReleaseId = (await api(tenant,{token})).tenant.currentReleaseId;
  await api(tenant+'/publish',{token,body:{appIds:[id],baseReleaseId,label:'opcua-'+v.version,notes:'Release OPC UA Integrator '+v.version}});
  if ((await api(listing)).latestVersion?.digest !== v.digest) throw Error('Published digest differs from the release manifest');
  console.log('Published digest verified',base+listing);
}
main().catch(err => { console.error(err.message); process.exitCode=1; });
