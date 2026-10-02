import { describe, expect, it } from 'vitest';
import config from '../svelte.config.js';

describe('optional shared feedback CSP', () => {
	it('allows only the maintained collector assets and API paths', () => {
		const directives = config.kit?.csp?.directives;
		if (!directives) throw new Error('Feedback CSP is missing');
		expect(directives['script-src']).toEqual([
			'self',
			'https://sassmaker.com/project-strip.js',
			'https://sassmaker.com/ai-chat-footer.js',
			'https://sassmaker.com/feedback-launcher.js'
		]);
		expect(directives['connect-src']).toEqual([
			'self',
			'https://sassmaker.com/projects.json',
			'https://api.sassmaker.com/v1/capture-config/',
			'https://api.sassmaker.com/v1/feedback'
		]);
		expect(directives['default-src']).toEqual(['self']);
		expect(directives['form-action']).toEqual(['self']);
		expect(directives['frame-ancestors']).toEqual(['none']);
	});
});
