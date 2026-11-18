import { fixtureValue } from '../src/fixture';

// Rendered on request: the value must come from the build (NEXT_PUBLIC_* is inlined), not the runtime env.
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
