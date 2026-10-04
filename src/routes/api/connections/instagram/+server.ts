import type { RequestHandler } from './$types';
import { randomHex } from '$lib/domain/bytes';
import { OAUTH_PENDING_TTL_MS } from '$lib/domain/oauth-pending';
import { SESSION_COOKIE } from '$lib/server/auth';
import { encryptSecret } from '$lib/server/crypto';
import { oauthPending } from '$lib/server/db/schema';
import { handleError, ok } from '$lib/server/http';
import { bindOAuthState } from '$lib/server/oauth-state';
import { platformNotConfigured } from '$lib/server/platform-setup';
import { instagramAuthorizeUrl } from '$lib/server/providers';
import { requireSession } from '$lib/server/require';

export const POST: RequestHandler = async ({ locals, cookies }) => {
	try {
		const user = requireSession(locals.user, locals.authMethod);
		const clientId = locals.env.INSTAGRAM_APP_ID;
		const clientSecret = locals.env.INSTAGRAM_APP_SECRET;
		if (!clientId || !clientSecret) return platformNotConfigured('instagram');
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
			instanceUrl: 'instagram',
			clientId,
			clientSecretEnc: await encryptSecret(clientSecret, locals.env.APP_ENCRYPTION_KEY),
			expiresAt: new Date(Date.now() + OAUTH_PENDING_TTL_MS),
			createdAt: new Date()
		});
		return ok({ authorizeUrl: instagramAuthorizeUrl(clientId, locals.env.APP_URL, bound) });
	} catch (err) {
		return handleError(err);
	}
};
