import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { fixtureValue } from './fixture';

// import.meta.env.VITE_FIXTURE is inlined at build time: it proves the contract build-args reached the build.
const root = document.getElementById('root');
if (!root) throw new Error('no #root');
createRoot(root).render(
  <StrictMode>
    <main>
      <h1>fixture-spa</h1>
      <p>
        build-arg: <span id="fixture">{fixtureValue(import.meta.env.VITE_FIXTURE)}</span>
      </p>
    </main>
  </StrictMode>,
);
