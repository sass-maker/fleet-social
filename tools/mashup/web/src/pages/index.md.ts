import { site } from '../site';

export const prerender = true;

export function GET() {
  const body = [
    '# Mashup',
    '',
    '> Turn approved creator archives into coherent, inspectable edits.',
    '',
    site.summary,
    '',
    `Status: ${site.status}.`,
    '',
    '## Who it is for',
    '',
    site.audience,
    '',
    '## Current proof',
    '',
    '- A source-faithful 47.15-second argument assembled from four beats across three Creative Commons podcast episodes.',
    '- A compact 13.16-second single-idea cut with the same provenance and boundary review.',
    `- [Watch the primary proof](${site.primaryMedia})`,
    `- [Inspect its media receipt](${site.primaryReceipt})`,
    '',
    '## How it works',
    '',
    ...site.capabilities.map((item) => `- ${item}`),
    '',
    '## Product boundaries',
    '',
    ...site.boundaries.map((item) => `- ${item}`),
    '- Microsoft Clarity measures the public proof site only; the local archive, editor, render pipeline, and approvals are never part of session replay.',
    '',
    '## Canonical links',
    '',
    `- Public proof: ${site.url}/`,
    `- Source repository and local setup: ${site.github}`,
    `- Agent index: ${site.url}/llms.txt`,
    `- Agent catalog: ${site.url}/api/ai`,
    `- OpenAPI: ${site.url}/openapi.json`,
    '',
    `Last updated: ${site.lastUpdated}`,
    '',
  ].join('\n');

  return new Response(body, {
    headers: { 'content-type': 'text/markdown; charset=utf-8' },
  });
}
