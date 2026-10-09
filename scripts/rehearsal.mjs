import { spawn } from 'node:child_process';
import { mkdirSync, copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const destination = resolve('.fleet-local/rehearsal');
mkdirSync(destination, { recursive: true });
const source = process.argv[2];
if (source) {
	for (const [from, to] of [
		['media/survive-technology-final.mp4', 'survive.mp4'],
		['media/operators-final.mp4', 'operators.mp4'],
		['primary-poster.jpg', 'survive.jpg'],
		['compact-poster.jpg', 'operators.jpg'],
		['captions/survive-technology-final.vtt', 'survive.vtt'],
		['captions/operators-final.vtt', 'operators.vtt'],
		['receipts/survive-technology.receipt.json', 'survive.receipt.json'],
		['receipts/operators.receipt.json', 'operators.receipt.json']
	])
		copyFileSync(resolve(source, from), resolve(destination, to));
}
if (!existsSync(resolve(destination, 'survive.mp4'))) {
	console.error('Supply a folder containing the two Mashup proof videos and posters.');
	process.exit(1);
}
const rootPointer = resolve(destination, 'mashup-root.txt');
if (process.argv[3]) writeFileSync(rootPointer, resolve(process.argv[3]));
const mashupRoot = existsSync(rootPointer)
	? readFileSync(rootPointer, 'utf8').trim()
	: resolve('tools/mashup');
const env = {
	...process.env,
	FLEET_SOCIAL_REHEARSAL: '1',
	FLEET_SOCIAL_REHEARSAL_DIR: destination,
	FLEET_SOCIAL_MASHUP_ROOT: mashupRoot,
	DOTENV_DISABLED: '1'
};
for (const key of Object.keys(env)) {
	if (key.startsWith('CLOUDFLARE_') || key.startsWith('WRANGLER_')) delete env[key];
}
console.log('Fleet Social rehearsal: http://127.0.0.1:5187 — local data, no provider uploads');
const child = spawn(
	process.execPath,
	['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5187', '--strictPort'],
	{ env, stdio: 'inherit' }
);
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('exit', (code) => process.exit(code ?? 0));
