import { expect, test } from 'vitest';
import { fixtureValue } from './fixture';

test('fixtureValue falls back to unset', () => {
  expect(fixtureValue(undefined)).toBe('unset');
  expect(fixtureValue('1')).toBe('1');
});
