import { describe, expect, it } from 'vitest';
import {
	advertisePublicDocument,
	handlePublicDocuments,
	publicDocumentOrigin,
	publicDocumentPaths
} from '../src/lib/server/public-documents';
import authored from '../src/lib/server/public-documents.generated.json';

const origin = 'https://example.test';
const read = (path: string, init?: RequestInit) =>
	handlePublicDocuments(new Request(origin + path, init), origin);

describe('public document representations', () => {
	it('uses the instance URL and never invents a public homepage', async () => {
		expect(publicDocumentOrigin(new Request(origin + '/about'), 'https://configured.test')).toBe(
			'https://configured.test'
		);
		expect(publicDocumentPaths).toEqual(['/about', '/privacy', '/terms']);
		const catalog = await (await read('/api/md/_catalog'))!.json();
		expect(catalog.map((route: { path: string }) => route.path)).toEqual(publicDocumentPaths);
		expect((await read('/index.md'))!.status).toBe(404);
	});

	it('serves all three forms from literal public source without caller state', async () => {
		for (const path of publicDocumentPaths) {
			const expected = authored[path as keyof typeof authored];
			for (const url of [path + '.md', '/api/md' + path, path]) {
				const response = (await read(url + '?private=sentinel', {
					headers: {
						Accept: 'text/markdown',
						Cookie: 'private=sentinel',
						Authorization: 'Bearer sentinel'
					}
				}))!;
				expect(response.status).toBe(200);
				const body = await response.text();
				expect(body).toContain(expected.description);
				expect(body).not.toContain('sentinel');
				expect(response.headers.get('Content-Location')).toBe(origin + path);
				expect(response.headers.get('X-Robots-Tag')).toBe('noindex, follow');
				expect(response.headers.get('X-Markdown-Tokens')).toBe(String(Math.ceil(body.length / 4)));
				expect(response.headers.get('Access-Control-Allow-Origin')).toBe('*');
				const head = (await read(url, { method: 'HEAD', headers: { Accept: 'text/markdown' } }))!;
				expect(await head.text()).toBe('');
				expect(head.headers.get('Content-Location')).toBe(origin + path);
			}
		}
		const full = await (await read('/llms-full.txt'))!.text();
		expect(full).toContain('Included 3 of 3');
		expect(full.length).toBeLessThan(500000);
		expect(full).toContain('youtube.upload');
		expect(full).toContain('https://www.youtube.com/t/terms');
	});

	it('leaves private routes and HTML negotiation intact', async () => {
		for (const path of [
			'/',
			'/posts',
			'/accounts',
			'/login',
			'/api/account',
			'/api/media/public/file'
		]) {
			expect(await read(path, { headers: { Accept: 'text/markdown' } })).toBeNull();
			expect((await read('/api/md' + path))!.status).toBe(404);
		}
		for (const path of ['/admin/private.md', '/dashboard/private.md', '/missing.md'])
			expect((await read(path))!.status).toBe(404);
		for (const accept of [
			'*/*',
			'text/markdown;q=0',
			'text/markdown,text/html',
			'text/markdown;q=0.1,text/html;q=1'
		])
			expect(await read('/about', { headers: { Accept: accept } })).toBeNull();
		expect((await read('/about.md', { method: 'OPTIONS' }))!.status).toBe(204);
		expect(await read('/about.md', { method: 'POST' })).toBeNull();
	});

	it('advertises only public HTML and preserves Vary wildcard', async () => {
		const source =
			'<html><head><link rel="canonical" href="https://alias.test/about"></head><body>same authored page</body></html>';
		const response = await advertisePublicDocument(
			new Request(origin + '/about'),
			new Response(source, { headers: { 'Content-Type': 'text/html', Vary: '*' } }),
			origin
		);
		expect(response.headers.get('Vary')).toBe('*');
		expect(response.headers.get('Link')).toContain(origin + '/about.md');
		const html = await response.text();
		expect(html).toContain('name="ai-summary"');
		expect(html).toContain('application/ld+json');
		expect(html).toContain(`rel="canonical" href="${origin}/about"`);
		expect(html.match(/rel="canonical"/g)).toHaveLength(1);
		expect(html).toContain('<body>same authored page</body>');
		const privateResponse = new Response(source, { headers: { 'Content-Type': 'text/html' } });
		expect(
			await advertisePublicDocument(new Request(origin + '/posts'), privateResponse, origin)
		).toBe(privateResponse);
	});

	it('keeps crawl policy and sitemap limited to public documents', async () => {
		const robots = await (await read('/robots.txt'))!.text();
		for (const bot of [
			'GPTBot',
			'ClaudeBot',
			'Google-Extended',
			'Applebot-Extended',
			'CCBot',
			'Bytespider'
		])
			expect(robots).toContain(`User-agent: ${bot}\nDisallow: /\nAllow: /about$`);
		const sitemap = await (await read('/sitemap.xml'))!.text();
		expect([...sitemap.matchAll(/<loc>(.*?)<\/loc>/g)].map((match) => match[1])).toEqual(
			publicDocumentPaths.map((path) => origin + path)
		);
	});
});
