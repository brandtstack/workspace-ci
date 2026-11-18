import { fixtureValue } from '../src/fixture';

// Statically prerendered at build: NEXT_PUBLIC_* is inlined then, so this page proves the build-arg reached the build (a runtime env var could not change it).
export default function Page() {
  return (
    <main>
      <h1>fixture-next</h1>
      <p>
        build-arg: <span id="fixture">{fixtureValue(process.env.NEXT_PUBLIC_FIXTURE)}</span>
      </p>
    </main>
  );
}
