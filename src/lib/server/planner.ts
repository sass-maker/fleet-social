import { loadDraftSummaries, loadQueueList } from './post-list';
import type { AppDb } from './db/client';
import { approvalSnapshot } from './draft-approval';

export async function loadPlanner(db: AppDb, userId: string) {
	const [drafts, queue] = await Promise.all([
		loadDraftSummaries(db, userId, 500),
		loadQueueList(db, userId, 500)
	]);
	const review = drafts.drafts.filter((draft) => draft.status === 'draft' && !draft.approvedAt);
	const calendar = new Map<
		string,
		{
			id: string;
			title: string;
			start: string;
			url: string;
			status: string;
			platforms: string[];
			draftId: string;
		}
	>();
	for (const target of queue.targets) {
		const when = target.scheduledFor ?? (target.status === 'published' ? target.updatedAt : null);
		if (!when) continue;
		const key = `${target.draft.id}:${when.toISOString()}`;
		const item = calendar.get(key);
		if (item) {
			item.platforms.push(target.connection.platform);
			if (target.status === 'failed' || target.status === 'uncertain') item.status = target.status;
		} else {
			calendar.set(key, {
				id: key,
				title: target.draft.title || target.draft.baseBody.slice(0, 60) || 'Untitled post',
				start: when.toISOString(),
				url: `/review/${target.draft.id}`,
				status: target.status,
				platforms: [target.connection.platform],
				draftId: target.draft.id
			});
		}
	}
	return {
		drafts: drafts.drafts,
		review,
		events: [...calendar.values()],
		hasMore: drafts.hasMore || queue.hasMore
	};
}

export async function currentApproval(db: AppDb, id: string) {
	const snapshot = await approvalSnapshot(db, id);
	return Boolean(snapshot?.draft.approvedAt && snapshot.draft.approvalHash === snapshot.hash);
}
