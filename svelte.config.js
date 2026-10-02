import adapter from '@sveltejs/adapter-cloudflare';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	compilerOptions: {
		// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
		runes: ({ filename }) => (filename.split(/[/\\]/).includes('node_modules') ? undefined : true)
	},
	kit: {
		adapter: adapter(),
		csp: {
			// 'auto' nonces the inline scripts SvelteKit emits for hydration,
			// which is why script-src needs no 'unsafe-inline' here.
			mode: 'auto',
			directives: {
				'default-src': ['self'],
				'script-src': [
					'self',
					'https://sassmaker.com/project-strip.js',
					'https://sassmaker.com/ai-chat-footer.js',
					'https://sassmaker.com/feedback-launcher.js'
				],
				'base-uri': ['self'],
				'object-src': ['none'],
				'frame-ancestors': ['none'],
				'form-action': ['self'],
				// Svelte writes inline style attributes and Tailwind injects a
				// stylesheet at build time; style attributes need 'unsafe-inline'.
				'style-src': ['self', 'unsafe-inline'],
				// Avatars come from five different provider CDNs, preview images
				// from arbitrary hosts, and media from this origin or
				// MEDIA_PUBLIC_BASE_URL, so images are limited to https rather
				// than enumerated.
				'img-src': ['self', 'data:', 'blob:', 'https:'],
				'media-src': ['self', 'blob:', 'https:'],
				// Path restrictions keep unrelated collection endpoints outside the feedback boundary.
				'connect-src': [
					'self',
					'https://sassmaker.com/projects.json',
					'https://api.sassmaker.com/v1/capture-config/',
					'https://api.sassmaker.com/v1/feedback'
				],
				'font-src': ['self', 'data:'],
				'manifest-src': ['self']
			}
		}
	}
};

export default config;
