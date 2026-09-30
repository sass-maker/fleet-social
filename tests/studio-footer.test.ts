import { describe, expect, it } from 'vitest';
import { readStudioProjectId } from '../src/lib/domain/studio-footer';

describe('optional studio feedback attribution', () => {
	it('leaves independent self-hosted instances unconfigured', () => {
		for (const value of [undefined, null, '', ' ', 42, {}, 'https://example.com', 'a/b']) {
			expect(readStudioProjectId(value)).toBeNull();
		}
	});

	it('uses only the explicitly configured project identity', () => {
		expect(readStudioProjectId(' fleet-social ')).toBe('fleet-social');
		expect(readStudioProjectId('another-project')).toBe('another-project');
		expect(readStudioProjectId('x'.repeat(65))).toBeNull();
	});
});
