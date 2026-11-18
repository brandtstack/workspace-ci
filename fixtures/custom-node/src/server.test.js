'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createServer } = require('./server');

function get(env, path) {
  return new Promise((resolve, reject) => {
    const server = createServer(env).listen(0, '127.0.0.1', async () => {
      try {
        const res = await fetch(`http://127.0.0.1:${server.address().port}${path}`);
        resolve({ status: res.status, body: await res.text() });
      } catch (e) {
        reject(e);
      } finally {
        server.close();
      }
    });
  });
}

test('/healthz is 200 ok', async () => {
  assert.deepEqual(await get({}, '/healthz'), { status: 200, body: 'ok\n' });
});

test('/greeting echoes GREETING, spaces intact', async () => {
  assert.deepEqual(await get({ GREETING: 'hello world' }, '/greeting'), { status: 200, body: 'hello world' });
});

test('/greeting fails loudly when GREETING is not set', async () => {
  assert.equal((await get({}, '/greeting')).status, 500);
});

test('anything else is 404', async () => {
  assert.equal((await get({}, '/nope')).status, 404);
});
