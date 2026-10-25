import { loadDraftSummaries, loadQueueList } from './post-list';
import type { AppDb } from './db/client';
import { approvalSnapshot } from './draft-approval';
import { listSlots } from './generation';
import { slotDisplayState } from '$lib/domain/generation';

const SLOT_LABEL = {
	planned: 'awaiting generation',
	requested: 'generating',
	ready: 'ready for review',
	failed: 'generation failed',
	late: 'generation late',
	cancelled: 'cancelled'
} as const;

export async function loadPlanner(db: AppDb, userId: string) {
	const [drafts, queue, slots] = await Promise.all([
		loadDraftSummaries(db, userId, 500),
		loadQueueList(db, userId, 500),
		listSlots(db, userId)
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
	// A slot holds the date until its media is scheduled; after that the
	// delivery itself is on the calendar.
	const scheduledDrafts = new Set([...calendar.values()].map((item) => item.draftId));
	const now = new Date();
	for (const slot of slots) {
		if (slot.draftId && scheduledDrafts.has(slot.draftId)) continue;
		const state = slotDisplayState(slot, now);
		calendar.set(`slot:${slot.id}`, {
			id: `slot:${slot.id}`,
			title: slot.title || slot.brief.slice(0, 60),
			start: slot.plannedFor.toISOString(),
			url:
				slot.draftId && state === 'ready' ? `/review/${slot.draftId}` : `/generate#slot-${slot.id}`,
			status: SLOT_LABEL[state],
			platforms: [],
			draftId: slot.draftId ?? ''
		});
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
