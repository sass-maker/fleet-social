import { describe, expect, it } from 'vitest';
import { SignJWT, createLocalJWKSet, exportJWK, generateKeyPair } from 'jose';
import { accessEmail } from '../src/lib/server/access-auth';

const config = {
	ACCESS_TEAM_DOMAIN: 'fleet-test.cloudflareaccess.com',
	ACCESS_AUD: 'fleet-social-audience'
};

async function assertion(claims: Record<string, unknown>, audience = config.ACCESS_AUD) {
	const { publicKey, privateKey } = await generateKeyPair('RS256');
	const jwk = { ...(await exportJWK(publicKey)), kid: 'test-key', alg: 'RS256', use: 'sig' };
	const token = await new SignJWT(claims)
		.setProtectedHeader({ alg: 'RS256', kid: 'test-key' })
		.setIssuer(`https://${config.ACCESS_TEAM_DOMAIN}`)
		.setAudience(audience)
		.setIssuedAt()
		.setExpirationTime('5m')
		.sign(privateKey);
	return {
		headers: new Headers({ 'Cf-Access-Jwt-Assertion': token }),
		keys: createLocalJWKSet({ keys: [jwk] })
	};
}

describe('Cloudflare Access identity', () => {
	it('accepts only a signed app token for this audience', async () => {
		const valid = await assertion({ type: 'app', email: ' Owner@Example.com ' });
		expect(await accessEmail(valid.headers, config, valid.keys)).toBe('owner@example.com');
		expect(
			await accessEmail(valid.headers, { ...config, ACCESS_AUD: 'another-app' }, valid.keys)
		).toBeNull();
		const wrongType = await assertion({ type: 'org', email: 'owner@example.com' });
		expect(await accessEmail(wrongType.headers, config, wrongType.keys)).toBeNull();
	});

	it('rejects unsigned, invalid, or unconfigured identity', async () => {
		const valid = await assertion({ type: 'app', email: 'owner@example.com' });
		expect(await accessEmail(new Headers(), config, valid.keys)).toBeNull();
		expect(
			await accessEmail(new Headers({ 'Cf-Access-Jwt-Assertion': 'forged' }), config, valid.keys)
		).toBeNull();
		expect(
			await accessEmail(valid.headers, { ...config, ACCESS_TEAM_DOMAIN: 'evil.test' }, valid.keys)
		).toBeNull();
		expect(await accessEmail(valid.headers, { ...config, ACCESS_AUD: '' }, valid.keys)).toBeNull();
	});
});
