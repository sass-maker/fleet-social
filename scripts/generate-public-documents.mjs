import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { parse } from 'svelte/compiler';

const routes = ['/about', '/privacy', '/terms'];
const documents = {};
const text = (value) => value.replace(/\s+/g, ' ').trim();
function attribute(node, name) {
	const value = node.attributes?.find((item) => item.name === name)?.value;
	if (!value) return '';
	assert.ok(
		Array.isArray(value) && value.every((item) => item.type === 'Text'),
		`Nonliteral ${name}`
	);
	return value.map((item) => item.data).join('');
}
function render(node) {
	if (node.type === 'Text') return node.data.replace(/\s+/g, ' ');
	if (node.type === 'Comment' || node.type === 'Head') return '';
	assert.equal(node.type, 'Element', `Unsupported authored Svelte node: ${node.type}`);
	if (node.name === 'nav' || attribute(node, 'aria-hidden') === 'true') return '';
	const body = node.children.map(render).join('');
	if (/^h[1-6]$/.test(node.name))
		return `\n\n${'#'.repeat(Number(node.name[1]))} ${text(body)}\n\n`;
	if (node.name === 'a') return `[${text(body)}](${attribute(node, 'href')})`;
	if (node.name === 'code') return `\`${text(body)}\``;
	if (node.name === 'strong' || node.name === 'b') return `**${text(body)}**`;
	if (node.name === 'em' || node.name === 'i') return `_${text(body)}_`;
	if (node.name === 'br') return '\n';
	if (node.name === 'li') return `\n- ${text(body)}\n`;
	assert.ok(
		['main', 'section', 'div', 'p', 'span', 'ul', 'ol'].includes(node.name),
		`Unsupported element: ${node.name}`
	);
	return node.name === 'span' ? body : `\n\n${body.trim()}\n\n`;
}
for (const route of routes) {
	const source = await readFile(
		new URL(`../src/routes${route}/+page.svelte`, import.meta.url),
		'utf8'
	);
	const ast = parse(source);
	assert.ok(!ast.instance && !ast.module, `Public source must remain literal: ${route}`);
	const head = ast.html.children.find((node) => node.type === 'Head');
	const title = head.children
		.find((node) => node.name === 'title')
		.children.map((node) => node.data)
		.join('');
	const description = attribute(
		head.children.find((node) => node.name === 'meta' && attribute(node, 'name') === 'description'),
		'content'
	);
	const main = ast.html.children.find((node) => node.name === 'main');
	assert.ok(main, `Missing public main: ${route}`);
	const markdown =
		render(main)
			.replace(/[ \t]+\n/g, '\n')
			.replace(/\n{3,}/g, '\n\n')
			.trim() + '\n';
	documents[route] = { title, description, markdown };
}
const output = new URL('../src/lib/server/public-documents.generated.json', import.meta.url);
const serialized = JSON.stringify(documents, null, 2) + '\n';
if (process.argv.includes('--check'))
	assert.equal(await readFile(output, 'utf8'), serialized, 'Regenerate public documents');
else await writeFile(output, serialized);
console.log(`Public documents: ${routes.length} literal source pages`);
