import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { createServer } from 'node:net';
const listener = createServer();
await new Promise((finish) => listener.listen(0, '127.0.0.1', finish));
const port = listener.address().port;
await new Promise((finish) => listener.close(finish));

const directory = `.fleet-local/e2e-${randomUUID()}`;
const env = {
	...process.env,
	FLEET_SOCIAL_E2E_ISOLATED: '1',
	FLEET_SOCIAL_E2E_PORT: String(port),
	FLEET_SOCIAL_E2E_STATE: `${directory}/state`,
	FLEET_SOCIAL_E2E_CONFIG: `${directory}/wrangler.jsonc`,
	DOTENV_DISABLED: '1'
};
for (const key of Object.keys(env))
	if (key.startsWith('CLOUDFLARE_') || key.startsWith('WRANGLER_')) delete env[key];
const child = spawn(
	process.execPath,
	['node_modules/@playwright/test/cli.js', 'test', ...process.argv.slice(2)],
	{ env, stdio: 'inherit' }
);
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('exit', (code) => process.exit(code ?? 1));
