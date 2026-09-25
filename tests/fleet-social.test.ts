import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { eq } from 'drizzle-orm';
import { newId, type AppDb } from '$lib/server/db/client';
import { connections, drafts, publishTargets, users } from '$lib/server/db/schema';
import { createTestDb, createTestMedia, TEST_ENV } from '$lib/server/db/test';
import { approvalProblem } from '$lib/server/draft-approval';
import { loadDraftSummaries } from '$lib/server/post-list';
import { POST as intakePOST } from '../src/routes/api/drafts/+server';
import { POST as approvePOST } from '../src/routes/api/drafts/[id]/approval/+server';
import { POST as publishPOST } from '../src/routes/api/drafts/[id]/publish/+server';
import { POST as reconcilePOST } from '../src/routes/api/targets/[id]/reconcile/+server';

describe('Fleet draft intake and review authority', () => {
	let db: AppDb;
	let close: () => void;
	let userId: string;
	let connId: string;
	const user = () => ({
		id: userId,
		email: 'fleet@localhost',
		timezone: 'UTC',
		totpEnabled: true,
		mfaVerified: true
	});
	const media = createTestMedia();

	beforeAll(async () => {
		({ db, close } = await createTestDb());
		const now = new Date();
		userId = newId();
		connId = newId();
		await db.insert(users).values({
			id: userId,
			email: 'fleet@localhost',
			passwordHash: 'x',
			timezone: 'UTC',
			createdAt: now,
			updatedAt: now
		});
		await db.insert(connections).values({
			id: connId,
			userId,
			platform: 'mastodon',
			handle: 'fleet@test',
			credentialsEncrypted: 'enc',
			status: 'active',
			createdAt: now,
			updatedAt: now
		});
	});
	afterAll(() => close());

	async function intake(body: Record<string, unknown>, scopes = ['intake']) {
		return intakePOST({
			request: new Request('http://localhost/api/drafts', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(body)
			}),
			locals: { db, user: user(), apiKeyScopes: scopes }
		} as never) as Promise<Response>;
	}

	it('requires active project and stable source reference for intake', async () => {
		expect((await intake({ projectId: 'not-a-project', sourceRef: 'feed:bad' })).status).toBe(400);
		expect((await intake({ projectId: 'codevetter', baseBody: 'missing source' })).status).toBe(
			400
		);
	});

	it('deduplicates product feed drafts and keeps intake away from publishing', async () => {
		const body = {
			projectId: 'codevetter',
			sourceRef: 'feed:release-123',
			baseBody: 'Release note',
			selectedConnectionIds: [connId]
		};
		const first = await intake(body);
		const second = await intake({ ...body, baseBody: 'Changed upstream' });
		expect(first.status).toBe(201);
		expect(second.status).toBe(200);
		const created = (await first.json()).draft;
		const repeated = await second.json();
		expect(repeated).toMatchObject({
			existing: true,
			draft: { id: created.id, baseBody: 'Release note' }
		});
		const summaries = await loadDraftSummaries(db, userId);
		expect(summaries.drafts.find((draft) => draft.id === created.id)?.projectId).toBe('codevetter');
		const publish = (await publishPOST({
			params: { id: created.id },
			request: new Request('http://localhost/api/drafts/x/publish', {
				method: 'POST',
				body: JSON.stringify({ connectionIds: [connId] })
			}),
			locals: { db, user: user(), apiKeyScopes: ['intake'], env: TEST_ENV, media }
		} as never)) as Response;
		expect(publish.status).toBe(403);
		const bearerApproval = (await approvePOST({
			params: { id: created.id },
			locals: { db, user: user(), authMethod: 'bearer' }
		} as never)) as Response;
		expect(bearerApproval.status).toBe(401);
	});

	it('binds approval to saved content and destination', async () => {
		const response = await intake({
			projectId: 'codevetter',
			sourceRef: 'feed:review-456',
			baseBody: 'Review me',
			selectedConnectionIds: [connId]
		});
		const draftId = (await response.json()).draft.id as string;
		const before = (await publishPOST({
			params: { id: draftId },
			request: new Request('http://localhost/api/drafts/x/publish', {
				method: 'POST',
				body: JSON.stringify({ connectionIds: [connId] })
			}),
			locals: { db, user: user(), env: TEST_ENV, media }
		} as never)) as Response;
		expect(before.status).toBe(409);
		const approved = (await approvePOST({
			params: { id: draftId },
			locals: { db, user: user(), authMethod: 'session' }
		} as never)) as Response;
		expect(approved.status).toBe(200);
		expect(await approvalProblem(db, draftId, [connId])).toBeNull();
		await db.update(drafts).set({ baseBody: 'Changed after review' }).where(eq(drafts.id, draftId));
		expect(await approvalProblem(db, draftId, [connId])).toMatch(/approve/i);
	});

	it('requires owner reconciliation before an uncertain target can retry', async () => {
		const response = await intake({
			projectId: 'codevetter',
			sourceRef: 'feed:uncertain-789',
			baseBody: 'Check me',
			selectedConnectionIds: [connId]
		});
		const draftId = (await response.json()).draft.id as string;
		const now = new Date();
		const targetId = newId();
		await db.insert(publishTargets).values({
			id: targetId,
			draftId,
			connectionId: connId,
			status: 'uncertain',
			attemptCount: 1,
			errorMessage: 'Provider request timed out',
			createdAt: now,
			updatedAt: now
		});
		const call = (
			action: string,
			extra: Record<string, unknown>,
			authMethod: 'session' | 'bearer' = 'session'
		) =>
			reconcilePOST({
				params: { id: targetId },
				request: new Request('http://localhost/api/targets/x/reconcile', {
					method: 'POST',
					body: JSON.stringify({ action, ...extra })
				}),
				locals: { db, user: user(), authMethod }
			} as never) as Promise<Response>;
		expect(
			(await call('not_published', { confirmation: 'I checked the destination account' }, 'bearer'))
				.status
		).toBe(401);
		expect((await call('not_published', {})).status).toBe(400);
		expect((await call('published', { remoteUrl: 'http://example.com/post' })).status).toBe(400);
		expect((await call('published', { remoteUrl: 'https://example.com/post' })).status).toBe(200);
		const [target] = await db.select().from(publishTargets).where(eq(publishTargets.id, targetId));
		expect(target).toMatchObject({ status: 'published', remoteUrl: 'https://example.com/post' });
		expect(
			(await call('not_published', { confirmation: 'I checked the destination account' })).status
		).toBe(409);
	});
});
