import { createOAuthCallback } from '$lib/server/oauth-callback';
import { decryptSecret } from '$lib/server/crypto';
import { youtubeChannel, youtubeExchangeCode } from '$lib/server/providers';

export const GET = createOAuthCallback({
	platform: 'youtube',
	pendingMarker: 'youtube',
	accountIdKey: 'youtubeChannelId',
	async complete({ pending, code, env }) {
		const clientSecret = await decryptSecret(pending.clientSecretEnc, env.APP_ENCRYPTION_KEY);
		const credentials = await youtubeExchangeCode({
			clientId: pending.clientId,
			clientSecret,
			code,
			appUrl: env.APP_URL
		});
		const channel = await youtubeChannel(credentials);
		return {
			credentials: { ...credentials, youtubeChannelId: channel.id },
			displayName: channel.title,
			handle: channel.id,
			avatarUrl: channel.avatarUrl,
			meta: { youtubeChannelId: channel.id }
		};
	}
});
