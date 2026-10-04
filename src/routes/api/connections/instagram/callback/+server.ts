import { createOAuthCallback } from '$lib/server/oauth-callback';
import { decryptSecret } from '$lib/server/crypto';
import { instagramAccount, instagramExchangeCode } from '$lib/server/providers';

export const GET = createOAuthCallback({
	platform: 'instagram',
	pendingMarker: 'instagram',
	accountIdKey: 'instagramUserId',
	async complete({ pending, code, env }) {
		const credentials = await instagramExchangeCode({
			clientId: pending.clientId,
			clientSecret: await decryptSecret(pending.clientSecretEnc, env.APP_ENCRYPTION_KEY),
			code,
			appUrl: env.APP_URL
		});
		const account = await instagramAccount(credentials);
		return {
			credentials: {
				...credentials,
				instagramUserId: account.id,
				instagramUsername: account.username
			},
			displayName: account.username,
			handle: `@${account.username}`,
			meta: { instagramUserId: account.id }
		};
	}
});
