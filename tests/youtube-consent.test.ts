import { describe, expect, it } from 'vitest';
import { newId } from '$lib/server/db/client';
import { createTestDb, TEST_ENV } from '$lib/server/db/test';
import { users } from '$lib/server/db/schema';
import { hasCurrentYouTubeConsent } from '$lib/server/youtube-consent';
import { POST as consentPOST } from '../src/routes/api/connections/youtube/consent/+server';
import { POST as youtubePOST } from '../src/routes/api/connections/youtube/+server';

describe('YouTube policy consent', () => {
	it('blocks OAuth until a session owner explicitly accepts the current policy', async () => {
		const { db, close } = await createTestDb();
		try {
			const userId = newId();
			const now = new Date();
			await db.insert(users).values({
				id: userId,
				email: 'youtube-consent@localhost',
				passwordHash: 'x',
				timezone: 'UTC',
				createdAt: now,
				updatedAt: now
			});
			const locals = {
				db,
				env: { ...TEST_ENV, YOUTUBE_CLIENT_ID: 'client', YOUTUBE_CLIENT_SECRET: 'secret' },
				user: {
					id: userId,
					email: 'youtube-consent@localhost',
					timezone: 'UTC',
					totpEnabled: true,
					mfaVerified: true
				},
				authMethod: 'session'
			};
			const connect = () =>
				youtubePOST({ locals, cookies: { get: () => 'session-token' } } as never);
			expect((await connect()).status).toBe(409);
			expect(await hasCurrentYouTubeConsent(db, userId)).toBe(false);
			const reject = await consentPOST({
				locals,
				request: new Request('http://localhost/api/connections/youtube/consent', {
					method: 'POST',
					body: JSON.stringify({ accepted: false })
				})
			} as never);
			expect(reject.status).toBe(400);
			expect(await hasCurrentYouTubeConsent(db, userId)).toBe(false);
			const accept = await consentPOST({
				locals,
				request: new Request('http://localhost/api/connections/youtube/consent', {
					method: 'POST',
					body: JSON.stringify({ accepted: true })
				})
			} as never);
			expect(accept.status).toBe(200);
			expect(await hasCurrentYouTubeConsent(db, userId)).toBe(true);
			expect((await connect()).status).toBe(200);
		} finally {
			close();
		}
	});
});
