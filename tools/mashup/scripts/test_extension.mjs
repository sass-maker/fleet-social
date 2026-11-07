import { spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const project = fileURLToPath(new URL('../', import.meta.url));
const target = fileURLToPath(new URL('../tests/test_extension.py', import.meta.url));
const packageTarget = fileURLToPath(new URL('../tests/test_extension_package.py', import.meta.url));
test('local media extension, MCP protocol and standalone package', { timeout: 180_000 }, () => {
  const result = spawnSync('uv', [
    'run', '--project', project, '--locked', '--no-default-groups', '--extra', 'mcp',
    '--with', 'pytest', 'python', '-m', 'pytest', target, packageTarget, '-q'
  ], { stdio: 'inherit', timeout: 180_000 });
  assert.ifError(result.error);
  assert.equal(result.status, 0, 'authoritative Python extension tests failed');
});
