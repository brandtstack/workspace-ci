'use strict';
const { createServer } = require('./server');

const port = Number(process.env.PORT || 80);
createServer(process.env).listen(port, '0.0.0.0', () => {
  console.log(`listening on 0.0.0.0:${port}`);
});
