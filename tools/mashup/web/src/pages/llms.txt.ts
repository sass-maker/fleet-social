import { site } from '../site';

export const prerender = true;

export function GET() {
  const body = [
    '# Mashup',
    `> ${site.summary}`,
    '',
    '## When to use this',
    '- Best fit: understanding or evaluating the Mashup local editorial pipeline and its current public proof.',
    '- Best fit: checking provenance, captions, source receipts, rights boundaries, or local setup.',
    '- Not a fit: uploading media, requesting a hosted render, publishing a clip, or accessing an operator archive.',
    '- Not a fit: generating replacement speech, voice cloning, or claiming automatic rights clearance.',
    '',
    '## Primary',
    `- [Product summary](${site.url}/index.md): Canonical Markdown explanation of the product, audience, proof, and boundaries.`,
    `- [Public proof](${site.url}/): Human-readable screening page for the approved finished result.`,
    `- [Primary media receipt](${site.primaryReceipt}): Provenance and operation-linked receipt for the 47-second synthesis.`,
    `- [Compact media receipt](${site.compactReceipt}): Provenance receipt for the 13-second comparison cut.`,
    `- [Source and setup](${site.github}): Local CLI, editor, approval, and rendering instructions.`,
    '',
    '## Developer and agent surfaces',
    `- [Agent catalog](${site.url}/api/ai)`,
    `- [OpenAPI specification](${site.url}/openapi.json)`,
    `- [Sitemap](${site.url}/sitemap.xml)`,
    `- [This index](${site.url}/llms.txt)`,
    '',
    '## Product boundaries',
    ...site.boundaries.map((item) => `- ${item}`),
    '',
  ].join('\n');

  return new Response(body, {
    headers: { 'content-type': 'text/plain; charset=utf-8' },
  });
}
