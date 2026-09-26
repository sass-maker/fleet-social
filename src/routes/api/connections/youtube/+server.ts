import type { RequestHandler } from './$types';
import { randomHex } from '$lib/domain/bytes';
import { OAUTH_PENDING_TTL_MS } from '$lib/domain/oauth-pending';
import { SESSION_COOKIE } from '$lib/server/auth';
import { encryptSecret } from '$lib/server/crypto';
import { oauthPending } from '$lib/server/db/schema';
import { fail, handleError, ok } from '$lib/server/http';
import { bindOAuthState } from '$lib/server/oauth-state';
import { platformNotConfigured } from '$lib/server/platform-setup';
import { youtubeAuthorizeUrl } from '$lib/server/providers';
import { requireSession } from '$lib/server/require';
import { hasCurrentYouTubeConsent } from '$lib/server/youtube-consent';

export const POST: RequestHandler = async ({ locals, cookies }) => {
	try {
		const user = requireSession(locals.user, locals.authMethod);
		if (!(await hasCurrentYouTubeConsent(locals.db, user.id))) {
			return fail('Agree to the current privacy policy in Accounts before connecting YouTube', 409);
		}
		const clientId = locals.env.YOUTUBE_CLIENT_ID;
		const clientSecret = locals.env.YOUTUBE_CLIENT_SECRET;
		if (!clientId || !clientSecret) return platformNotConfigured('youtube');
		const state = randomHex(16);
		const sessionId = cookies.get(SESSION_COOKIE) ?? `machine:${user.id}`;
		const bound = await bindOAuthState({
			secret: locals.env.AUTH_SECRET,
			pendingId: state,
			sessionId
		});
		await locals.db.insert(oauthPending).values({
			id: state,
			userId: user.id,
			instanceUrl: 'youtube',
			clientId,
			clientSecretEnc: await encryptSecret(clientSecret, locals.env.APP_ENCRYPTION_KEY),
			expiresAt: new Date(Date.now() + OAUTH_PENDING_TTL_MS),
			createdAt: new Date()
		});
		return ok({ authorizeUrl: youtubeAuthorizeUrl(clientId, locals.env.APP_URL, bound) });
	} catch (err) {
		return handleError(err);
	}
};
