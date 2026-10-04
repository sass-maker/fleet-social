import { createRemoteJWKSet, jwtVerify, type JWTVerifyGetKey } from 'jose';

/** Access adds this assertion after it authenticates the request at the edge. */
const ASSERTION_HEADER = 'cf-access-jwt-assertion';
const TEAM_DOMAIN = /^[a-z0-9-]+\.cloudflareaccess\.com$/;
const keySets = new Map<string, JWTVerifyGetKey>();

export type AccessConfig = {
	ACCESS_TEAM_DOMAIN?: string;
	ACCESS_AUD?: string;
};

export function configuredAccess(config: AccessConfig): boolean {
	return Boolean(config.ACCESS_TEAM_DOMAIN && config.ACCESS_AUD);
}

/**
 * Verify the signature and application claims before treating an Access
 * identity as a login. Static Assets use an internal router that does not
 * propagate ctx.access, so the signed assertion is the reliable contract.
 */
export async function accessEmail(
	headers: Headers,
	config: AccessConfig,
	getKey?: JWTVerifyGetKey
): Promise<string | null> {
	const teamDomain = config.ACCESS_TEAM_DOMAIN?.trim().toLowerCase() ?? '';
	const audience = config.ACCESS_AUD?.trim() ?? '';
	const assertion = headers.get(ASSERTION_HEADER);
	if (!TEAM_DOMAIN.test(teamDomain) || !audience || !assertion) return null;
	try {
		let keys = getKey ?? keySets.get(teamDomain);
		if (!keys) {
			keys = createRemoteJWKSet(new URL(`https://${teamDomain}/cdn-cgi/access/certs`), {
				timeoutDuration: 5_000
			});
			keySets.set(teamDomain, keys);
		}
		const { payload } = await jwtVerify(assertion, keys, {
			issuer: `https://${teamDomain}`,
			audience,
			algorithms: ['RS256']
		});
		if (payload.type !== 'app' || typeof payload.email !== 'string') return null;
		const email = payload.email.trim().toLowerCase();
		return email && email.length <= 254 ? email : null;
	} catch {
		return null;
	}
}
