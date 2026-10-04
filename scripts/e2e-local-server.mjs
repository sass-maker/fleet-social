import { spawn } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { hashPassword, seedUserSql } from './lib/account.mjs';

const persist = process.env.FLEET_SOCIAL_E2E_STATE;
const config = process.env.FLEET_SOCIAL_E2E_CONFIG;
const port = Number(process.env.FLEET_SOCIAL_E2E_PORT);
if (!Number.isInteger(port) || port < 1024 || port > 65535)
	throw new Error('Invalid isolated test port');
if (
	!persist ||
	!config ||
	!/^\.fleet-local\/e2e-[a-f0-9-]{36}\/state$/.test(persist) ||
	config !== persist.replace(/state$/, 'wrangler.jsonc')
)
	throw new Error('Use scripts/e2e-local.mjs to allocate a fresh isolated test run.');
await mkdir(dirname(config), { recursive: true });
await writeFile(
	config,
	JSON.stringify({
		name: 'fleet-social-isolated-test',
		compatibility_date: '2026-09-01',
		compatibility_flags: ['nodejs_compat'],
		assets: { directory: resolve('.svelte-kit/cloudflare'), binding: 'ASSETS' },
		d1_databases: [{ binding: 'DB', database_name: 'isolated-test', database_id: randomUUID() }],
		r2_buckets: [{ binding: 'MEDIA', bucket_name: 'isolated-test' }],
		vars: {
			APP_ENCRYPTION_KEY: '11'.repeat(32),
			SKIP_TOTP: '1',
			APP_NAME: 'Fleet Social',
			APP_URL: `http://localhost:${port}`
		}
	})
);
const source = await readFile('src/lib/server/db/init-sql.ts', 'utf8');
const ddl = source.match(/export const INIT_SQL = `([\s\S]*?)`;/)?.[1].replaceAll('\\`', '`');
if (!ddl) throw new Error('Bootstrap schema not found');
const bootstrap = `${dirname(config)}/bootstrap.sql`;
await writeFile(
	bootstrap,
	ddl +
		'\n' +
		seedUserSql({
			id: randomUUID(),
			email: 'e2e@localhost',
			passwordHash: await hashPassword('e2e-password'),
			now: Date.now()
		})
);
const wrangler = resolve('node_modules/wrangler/bin/wrangler.js');
async function run(args) {
	return new Promise((finish, reject) => {
		const child = spawn(process.execPath, [wrangler, ...args], { stdio: 'inherit' });
		child.on('error', reject);
		child.on('exit', (code) =>
			code === 0
				? finish()
				: reject(new Error('Isolated local test database could not be initialized'))
		);
	});
}
await run([
	'd1',
	'execute',
	'DB',
	'--local',
	'--persist-to',
	persist,
	'--config',
	config,
	'--file',
	bootstrap
]);
const child = spawn(
	process.execPath,
	[
		wrangler,
		'dev',
		'.svelte-kit/cloudflare/_worker.js',
		'--port',
		String(port),
		'--persist-to',
		persist,
		'--config',
		config,
		'--local'
	],
	{ stdio: 'inherit' }
);
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('exit', (code) => process.exit(code ?? 1));
