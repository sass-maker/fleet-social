import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const project = fileURLToPath(new URL('../', import.meta.url));
const target = fileURLToPath(new URL('../tests/test_extension.py', import.meta.url));
const result = spawnSync('uv', [
  'run', '--project', project, '--locked', '--no-default-groups', '--extra', 'mcp',
  '--with', 'pytest', 'python', '-m', 'pytest', target, '-q'
], { stdio: 'inherit', timeout: 180_000 });
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
