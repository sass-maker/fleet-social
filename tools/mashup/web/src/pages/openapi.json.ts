import { site } from '../site';

export const prerender = true;

export function GET() {
  const textResponse = (description: string, contentType: string) => ({
    description,
    content: { [contentType]: { schema: { type: 'string' } } },
  });
  const spec = {
    openapi: '3.1.0',
    info: {
      title: 'Mashup public discovery API',
      version: '1.0.0',
      description:
        'Read-only metadata for the Mashup public proof. This API does not accept media, expose archives, render clips, approve edits, or publish content.',
    },
    servers: [{ url: site.url }],
    paths: {
      '/api/ai': {
        get: {
          operationId: 'getAgentCatalog',
          summary: 'Get the Mashup public agent catalog',
          responses: {
            '200': {
              description: 'Agent catalog',
              content: { 'application/json': { schema: { type: 'object' } } },
            },
          },
        },
      },
      '/index.md': {
        get: {
          operationId: 'getProductSummary',
          summary: 'Get the canonical Markdown product summary',
          responses: { '200': textResponse('Markdown product summary', 'text/markdown') },
        },
      },
      '/llms.txt': {
        get: {
          operationId: 'getLlmsIndex',
          summary: 'Get the LLM-oriented discovery index',
          responses: { '200': textResponse('LLM discovery index', 'text/plain') },
        },
      },
      '/sitemap.xml': {
        get: {
          operationId: 'getSitemap',
          summary: 'Get the public XML sitemap',
          responses: { '200': textResponse('XML sitemap', 'application/xml') },
        },
      },
    },
  };

  return new Response(JSON.stringify(spec, null, 2), {
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}
