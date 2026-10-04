import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const catalogPath = path.resolve(
	process.argv[2] ?? path.resolve(repoRoot, '../saas-maker/catalog/projects.json')
);
const outputPath = path.resolve(repoRoot, 'src/lib/domain/fleet-projects.json');
const catalog = JSON.parse(await readFile(catalogPath, 'utf8'));
const projects = catalog.projects
	.filter((project) => ['primary', 'active'].includes(project.lifecycle?.status))
	.map(({ id, name }) => ({ id, name }))
	.sort((a, b) => a.name.localeCompare(b.name));
await writeFile(outputPath, `${JSON.stringify(projects, null, '\t')}\n`);
console.log(`Synced ${projects.length} active Fleet projects from ${catalogPath}`);
