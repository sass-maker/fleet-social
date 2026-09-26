import { describe, expect, it } from 'vitest';
import { eq } from 'drizzle-orm';
import { encryptJson } from '$lib/server/crypto';
import { newId } from '$lib/server/db/client';
import { createTestDb, createTestMedia, TEST_ENV } from '$lib/server/db/test';
import {
	connections,
	draftMedia,
	drafts,
	publishAttempts,
	publishTargets,
	users
} from '$lib/server/db/schema';
import { publishTarget } from '$lib/server/publish';
import { approveTestTarget } from './fleet-approval';

describe('YouTube publish receipt', () => {
	it('resumes an interrupted upload from its encrypted session and stores one remote video ID', async () => {
		const { db, close } = await createTestDb();
		try {
			const userId = newId();
			const draftId = newId();
			const connectionId = newId();
			const targetId = newId();
			const now = new Date();
			const store = createTestMedia();
			await store.put('canary.mp4', new Uint8Array(10), 'video/mp4');
			await db.insert(users).values({
				id: userId,
				email: 'youtube@localhost',
				passwordHash: 'x',
				timezone: 'UTC',
				createdAt: now,
				updatedAt: now
			});
			await db.insert(drafts).values({
				id: draftId,
				userId,
				title: 'Private canary',
				baseBody: 'A private upload test',
				projectId: 'codevetter',
				status: 'draft',
				createdAt: now,
				updatedAt: now
			});
			await db.insert(connections).values({
				id: connectionId,
				userId,
				platform: 'youtube',
				displayName: 'Owner channel',
				handle: 'channel-one',
				credentialsEncrypted: await encryptJson(
					{
						accessToken: 'access',
						refreshToken: 'refresh',
						expiresAt: Date.now() + 3_600_000,
						clientId: 'client',
						clientSecret: 'secret',
						youtubeChannelId: 'channel-one'
					},
					TEST_ENV.APP_ENCRYPTION_KEY
				),
				metaJson: '{"youtubeChannelId":"channel-one"}',
				status: 'active',
				createdAt: now,
				updatedAt: now
			});
			await db.insert(draftMedia).values({
				id: newId(),
				draftId,
				storageKey: 'canary.mp4',
				mime: 'video/mp4',
				size: 10,
				sortOrder: 0,
				segmentIndex: 0,
				createdAt: now
			});
			await db.insert(publishTargets).values({
				id: targetId,
				draftId,
				connectionId,
				status: 'pending',
				attemptCount: 0,
				createdAt: now,
				updatedAt: now
			});
			await approveTestTarget(db, targetId);
			const env = { ...TEST_ENV, youtubeUploadEnabled: true };
			let starts = 0;
			const first = await publishTarget(db, env, store, targetId, {
				fetchImpl: async (_url, init) => {
					if (init?.method === 'POST') {
						starts++;
						return new Response(null, {
							status: 200,
							headers: {
								Location: 'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=one'
							}
						});
					}
					throw new Error('response lost after the upload');
				}
			});
			expect(first.status).toBe('scheduled');
			const attempts = await db
				.select()
				.from(publishAttempts)
				.where(eq(publishAttempts.publishTargetId, targetId));
			expect(attempts[0]?.responseSummary).toContain('youtubeUpload');
			expect(attempts[0]?.responseSummary).not.toContain('upload_id=one');

			const second = await publishTarget(db, env, store, targetId, {
				now: new Date(Date.now() + 120_000),
				fetchImpl: async (_url, init) => {
					expect(init?.method).toBe('PUT');
					expect(new Headers(init?.headers).get('Content-Range')).toBe('bytes */10');
					return Response.json(
						{ id: 'abcdefghijk', status: { privacyStatus: 'private' } },
						{ status: 201 }
					);
				}
			});
			expect(second.status).toBe('published');
			expect(starts).toBe(1);
			const [target] = await db
				.select()
				.from(publishTargets)
				.where(eq(publishTargets.id, targetId));
			expect(target.remotePostId).toBe('abcdefghijk');
			expect(target.remoteUrl).toBe('https://www.youtube.com/watch?v=abcdefghijk');
			const finalAttempts = await db
				.select()
				.from(publishAttempts)
				.where(eq(publishAttempts.publishTargetId, targetId));
			expect(
				finalAttempts.some((attempt) => attempt.responseSummary?.includes('"visibility":"private"'))
			).toBe(true);
		} finally {
			close();
		}
	});
});
