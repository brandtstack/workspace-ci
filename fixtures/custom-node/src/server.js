'use strict';
const http = require('node:http');

// The request handler, taking its env as a parameter so the test needs no process env.
function handler(env) {
  return (req, res) => {
    const path = new URL(req.url, 'http://127.0.0.1').pathname;
    if (path === '/healthz') {
      res.writeHead(200, { 'content-type': 'text/plain' });
      res.end('ok\n');
    } else if (path === '/greeting') {
      // Fixture only: echoes GREETING, so fixture-check.sh can see that smoke-env reached the container.
      if (!env.GREETING) {
        res.writeHead(500, { 'content-type': 'text/plain' });
        res.end('GREETING is not set\n');
        return;
      }
      res.writeHead(200, { 'content-type': 'text/plain' });
      res.end(env.GREETING);
    } else {
      res.writeHead(404, { 'content-type': 'text/plain' });
      res.end('not found\n');
    }
  };
}

function createServer(env) {
  return http.createServer(handler(env));
}

module.exports = { handler, createServer };
