import { describe, expect, it, vi } from 'vitest';
import { eq } from 'drizzle-orm';
import { encryptJson } from '$lib/server/crypto';
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

async function fixture() {
	const test = await createTestDb();
	const now = new Date();
	await test.db.insert(users).values({
		id: 'owner',
		email: 'instagram@localhost',
		passwordHash: 'test',
		timezone: 'UTC',
		createdAt: now,
		updatedAt: now
	});
	await test.db.insert(drafts).values({
		id: 'draft',
		userId: 'owner',
		title: 'An approved Reel',
		baseBody: 'Creator-owned demo',
		projectId: 'fleet-social',
		createdAt: now,
		updatedAt: now
	});
	await test.db.insert(connections).values({
		id: 'account',
		userId: 'owner',
		platform: 'instagram',
		status: 'active',
		credentialsEncrypted: await encryptJson(
			{ accessToken: 'test', expiresAt: Date.now() + 30 * 86400_000, instagramUserId: '12345' },
			TEST_ENV.APP_ENCRYPTION_KEY
		),
		createdAt: now,
		updatedAt: now
	});
	await test.db.insert(draftMedia).values({
		id: 'media',
		draftId: 'draft',
		storageKey: 'video.mp4',
		size: 10,
		mime: 'video/mp4',
		width: 1080,
		height: 1920,
		createdAt: now
	});
	await test.db.insert(publishTargets).values({
		id: 'target',
		draftId: 'draft',
		connectionId: 'account',
		status: 'pending',
		createdAt: now,
		updatedAt: now
	});
	await approveTestTarget(test.db, 'target');
	return test;
}
const env = { ...TEST_ENV, APP_URL: 'https://social.example', instagramUploadEnabled: true };

describe('Instagram publish pipeline', () => {
	it('checkpoints processing state encrypted, then resumes that container and saves the verified result', async () => {
		const test = await fixture();
		try {
			const first = await publishTarget(test.db, env, createTestMedia(), 'target', {
				fetchImpl: async (url) =>
					Response.json(
						String(url).endsWith('/media') ? { id: '22222' } : { status_code: 'IN_PROGRESS' }
					)
			});
			expect(first.status).toBe('scheduled');
			const [attempt] = await test.db.select().from(publishAttempts);
			expect(attempt.responseSummary).toContain('instagramUpload');
			expect(attempt.responseSummary).not.toContain('22222');
			vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 61_000);
			const seen: string[] = [];
			const result = await publishTarget(test.db, env, createTestMedia(), 'target', {
				now: new Date(Date.now()),
				fetchImpl: async (url) => {
					seen.push(String(url));
					if (String(url).includes('status_code'))
						return Response.json({ status_code: 'FINISHED' });
					if (String(url).includes('media_publish')) return Response.json({ id: '33333' });
					return Response.json({
						id: '33333',
						owner: { id: '12345' },
						permalink: 'https://www.instagram.com/reel/confirmed/'
					});
				}
			});
			expect(result.status).toBe('published');
			expect(seen.some((url) => url.endsWith('/media'))).toBe(false);
			const [target] = await test.db
				.select()
				.from(publishTargets)
				.where(eq(publishTargets.id, 'target'));
			expect(target.remotePostId).toBe('33333');
			expect(target.remoteUrl).toBe('https://www.instagram.com/reel/confirmed/');
		} finally {
			vi.restoreAllMocks();
			test.close();
		}
	});
	it('parks a lost publish response as uncertain and makes no second publish attempt', async () => {
		const test = await fixture();
		try {
			const result = await publishTarget(test.db, env, createTestMedia(), 'target', {
				fetchImpl: async (url) => {
					if (String(url).endsWith('/media')) return Response.json({ id: '22222' });
					if (String(url).includes('status_code'))
						return Response.json({ status_code: 'FINISHED' });
					throw new Error('response lost');
				}
			});
			expect(result.status).toBe('uncertain');
			await publishTarget(test.db, env, createTestMedia(), 'target', {
				fetchImpl: async () => {
					throw new Error('An uncertain result must not call Instagram again');
				}
			});
			const [target] = await test.db
				.select()
				.from(publishTargets)
				.where(eq(publishTargets.id, 'target'));
			expect(target.status).toBe('uncertain');
			expect(target.remotePostId).toBeNull();
		} finally {
			test.close();
		}
	});
});
