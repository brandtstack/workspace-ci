// Build-time helper for next.Dockerfile's migrate-true stage (contract `migrate: true`).
//   prepare <app-lock>  writes package.json { prisma, dotenv? } at the app lockfile's versions, and copies the
//                       lockfile to package-lock.json so `npm install` reuses every locked version.
//   check <app-lock>    after the install: every package in the new lockfile must have the app lockfile's
//                       version, so nothing in /opt/migrate floated.
// Plain CommonJS, no dependencies: it runs before anything is installed.
'use strict';
const fs = require('fs');
const [mode, appLockPath] = process.argv.slice(2);
const die = (msg) => { console.error('migrate-pin: ' + msg); process.exit(1); };
let appLock;
try { appLock = JSON.parse(fs.readFileSync(appLockPath, 'utf8')); } catch (e) { die('cannot read ' + appLockPath); }
const pkgs = (appLock && appLock.packages) || {};
if (!appLock || !appLock.packages) die('package-lock.json has no "packages" (lockfileVersion 2 or 3 is required)');
const version = (name) => (pkgs['node_modules/' + name] || {}).version || '';

if (mode === 'prepare') {
  const prisma = version('prisma');
  if (!prisma) die('migrate: true needs prisma in package-lock.json (node_modules/prisma)');
  const deps = { prisma };
  const dotenv = version('dotenv');
  if (dotenv) deps.dotenv = dotenv;
  fs.writeFileSync('package.json', JSON.stringify({ name: 'migrate', private: true, dependencies: deps }, null, 2));
  fs.copyFileSync(appLockPath, 'package-lock.json');
  console.log('migrate-pin: ' + Object.entries(deps).map(([k, v]) => k + '@' + v).join(' '));
} else if (mode === 'check') {
  const got = JSON.parse(fs.readFileSync('package-lock.json', 'utf8')).packages || {};
  const drift = [];
  let n = 0;
  for (const [key, entry] of Object.entries(got)) {
    if (!key) continue;
    n++;
    const want = pkgs[key];
    if (!want || want.version !== entry.version) drift.push(key + ' ' + entry.version + ' (app lockfile: ' + (want ? want.version : 'absent') + ')');
  }
  if (drift.length) die('installed packages differ from the app lockfile:\n  ' + drift.join('\n  '));
  console.log('migrate-pin: ' + n + ' packages, all at the app lockfile versions');
} else {
  die('usage: migrate-pin.cjs prepare|check <app-lock>');
}
