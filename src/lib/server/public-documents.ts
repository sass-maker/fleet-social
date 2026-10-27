import authored from './public-documents.generated.json';
import { createAgentSurfaceManifest } from './agent-surfaces/manifest.mjs';
import { createAgentSurfaceHandler, discoveryLinkHeader } from './agent-surfaces/handler.mjs';
import { absoluteMarkdownLinks } from './agent-surfaces/format.mjs';
import { resolveAppUrl } from '$lib/domain/app-url';

const documents: Record<string, { title: string; description: string; markdown: string }> =
	authored;
export const publicDocumentPaths = Object.keys(documents);
const discoveryPaths = ['/llms.txt', '/llms-full.txt', '/ai.txt', '/api/ai', '/api/md/'];
const bots = [
	'*',
	'OAI-SearchBot',
	'ChatGPT-User',
	'Claude-SearchBot',
	'Claude-User',
	'PerplexityBot',
	'Perplexity-User',
	'GPTBot',
	'ClaudeBot',
	'Google-Extended',
	'Applebot-Extended',
	'CCBot',
	'Bytespider'
];

export function publicDocumentOrigin(request: Request, configured?: string): string {
	return resolveAppUrl({ configured, requestUrl: request.url }).url;
}

export async function handlePublicDocuments(
	request: Request,
	origin: string
): Promise<Response | null> {
	if (!origin || !['GET', 'HEAD', 'OPTIONS'].includes(request.method)) return null;
	const path = new URL(request.url).pathname;
	if (path === '/robots.txt' || path === '/sitemap.xml') {
		const body =
			path === '/robots.txt'
				? bots
						.flatMap((bot) => [
							`User-agent: ${bot}`,
							'Disallow: /',
							...[
								...publicDocumentPaths.flatMap((route) => [`${route}$`, `${route}.md$`]),
								...discoveryPaths
							].map((route) => `Allow: ${route}`),
							''
						])
						.join('\n') + `\nSitemap: ${origin}/sitemap.xml\n`
				: `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${publicDocumentPaths.map((route) => `<url><loc>${origin}${route}</loc></url>`).join('')}</urlset>\n`;
		return new Response(request.method === 'HEAD' || request.method === 'OPTIONS' ? null : body, {
			status: request.method === 'OPTIONS' ? 204 : 200,
			headers: {
				'Content-Type':
					path === '/robots.txt' ? 'text/plain; charset=utf-8' : 'application/xml; charset=utf-8',
				'Cache-Control': 'public, s-maxage=3600',
				'Access-Control-Allow-Origin': '*',
				'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS'
			}
		});
	}
	if (
		!publicDocumentPaths.includes(path) &&
		!path.endsWith('.md') &&
		!discoveryPaths.includes(path) &&
		path !== '/api/md' &&
		!path.startsWith('/api/md/')
	)
		return null;
	const pages = Object.fromEntries(
		Object.entries(documents).map(([route, document]) => [
			route,
			absoluteMarkdownLinks(
				`> ${document.description}\n\nCanonical: ${origin}${route}\n\n${document.markdown}`,
				origin + route
			)
		])
	);
	const manifest = createAgentSurfaceManifest({
		name: 'Fleet Social',
		url: origin,
		summary: documents['/about'].description,
		contact: 'https://sarthakagrawal.dev/',
		trainingAllowed: true,
		pages,
		sitemap: `${origin}/sitemap.xml`,
		excludedPaths: [
			'All routes except /about, /privacy, /terms and their public discovery representations'
		],
		notes: [
			'The scheduler, accounts, drafts, media, authentication and publishing APIs are private and excluded.'
		]
	});
	const handler = createAgentSurfaceHandler({ manifest });
	const response = await handler(request);
	if (response || !path.endsWith('.md')) return response;
	return handler(new Request(`${origin}/api/md${path.slice(0, -3)}`, { method: request.method }));
}

function escapeAttribute(value: string): string {
	return value.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;');
}

export async function advertisePublicDocument(
	request: Request,
	response: Response,
	origin: string
): Promise<Response> {
	const path = new URL(request.url).pathname;
	const document = documents[path];
	if (
		!document ||
		!origin ||
		!response.ok ||
		!response.headers.get('Content-Type')?.includes('text/html')
	)
		return response;
	const headers = new Headers(response.headers);
	const vary = headers.get('Vary') || '';
	if (!vary.split(',').some((value) => ['*', 'accept'].includes(value.trim().toLowerCase())))
		headers.set('Vary', [vary, 'Accept'].filter(Boolean).join(', '));
	headers.append(
		'Link',
		`<${origin}${path}.md>; rel="alternate"; type="text/markdown", ${discoveryLinkHeader(origin)}`
	);
	headers.delete('Content-Length');
	headers.delete('ETag');
	if (request.method === 'HEAD') return new Response(null, { status: response.status, headers });
	const identity =
		path === '/about'
			? `<script type="application/ld+json">${JSON.stringify({ '@context': 'https://schema.org', '@type': 'WebSite', name: 'Fleet Social', url: origin, description: document.description }).replaceAll('<', '\\u003c')}</script>`
			: '';
	const metadata = `<link rel="canonical" href="${escapeAttribute(origin + path)}"><meta name="ai-summary" content="${escapeAttribute(document.description)}"><link rel="alternate" type="text/markdown" href="${escapeAttribute(origin + path + '.md')}">${identity}`;
	return new Response(
		(await response.text()).replace(
			/<head>([\s\S]*?)<\/head>/,
			(_match, head: string) =>
				`<head>${head.replace(/<link\b[^>]*rel="canonical"[^>]*>/gi, '')}${metadata}</head>`
		),
		{
			status: response.status,
			headers
		}
	);
}
