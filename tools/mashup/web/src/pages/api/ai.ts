import { site } from '../../site';

export const prerender = true;

export function GET() {
  const body = {
    name: site.name,
    version: '1',
    url: site.url,
    llms: `${site.url}/llms.txt`,
    sitemap: `${site.url}/sitemap.xml`,
    openapi: `${site.url}/openapi.json`,
    markdown: { home: `${site.url}/index.md`, negotiation: false },
    surfaces: [
      { id: 'home', url: '/', md: '/index.md', kind: 'static-proof' },
    ],
    artifacts: [
      {
        id: 'primary-proof',
        url: site.primaryMedia,
        captions: site.primaryCaptions,
        receipt: site.primaryReceipt,
        scoreLedger: site.primaryScoreLedger,
        kind: 'approved-media',
      },
      {
        id: 'compact-proof',
        url: site.compactMedia,
        captions: site.compactCaptions,
        receipt: site.compactReceipt,
        kind: 'approved-media',
      },
    ],
    source: site.github,
    auth: {
      public: true,
      notes: 'Public discovery and finished proof require no account. Archives, editing, rendering, approval, and publishing remain local operator responsibilities.',
    },
    product: {
      summary: site.summary,
      audience: site.audience,
      status: site.status,
      capabilities: site.capabilities,
      boundaries: site.boundaries,
    },
  };

  return new Response(JSON.stringify(body, null, 2), {
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}
