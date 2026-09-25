import { eq } from 'drizzle-orm';
import type { AppDb } from '$lib/server/db/client';
import { drafts, publishTargets } from '$lib/server/db/schema';
import { approvalSnapshot, approveDraft } from '$lib/server/draft-approval';

/** Prepare a legacy publish fixture for Fleet's project and review gate. */
export async function approveTestTargets(db: AppDb, draftId: string, connectionIds: string[]) {
	const [draft] = await db.select().from(drafts).where(eq(drafts.id, draftId));
	if (!draft) throw new Error('Missing test draft');
	await db
		.update(drafts)
		.set({
			projectId: 'codevetter',
			selectedConnectionIds: JSON.stringify([...new Set(connectionIds)])
		})
		.where(eq(drafts.id, draftId));
	if (!(await approveDraft(db, draftId, draft.userId))) {
		// Some provider recovery tests deliberately begin with an expired account.
		const snapshot = await approvalSnapshot(db, draftId);
		if (!snapshot) throw new Error('Could not prepare test draft');
		await db
			.update(drafts)
			.set({ approvalHash: snapshot.hash, approvedAt: new Date() })
			.where(eq(drafts.id, draftId));
	}
}

export async function approveTestTarget(db: AppDb, targetId: string) {
	const [target] = await db.select().from(publishTargets).where(eq(publishTargets.id, targetId));
	if (!target) throw new Error('Missing test target');
	await approveTestTargets(db, target.draftId, [target.connectionId]);
}
