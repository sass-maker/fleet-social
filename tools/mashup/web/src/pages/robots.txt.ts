import { site } from '../site';

export const prerender = true;

export function GET() {
  const agents = ['*', 'GPTBot', 'OAI-SearchBot', 'ClaudeBot', 'PerplexityBot', 'Google-Extended'];
  const rules = agents
    .map((agent) => `User-agent: ${agent}\nAllow: /\nDisallow: /editor/\nDisallow: /visual-lab/`)
    .join('\n\n');

  return new Response(`${rules}\n\nSitemap: ${site.url}/sitemap.xml\n`, {
    headers: { 'content-type': 'text/plain; charset=utf-8' },
  });
}
