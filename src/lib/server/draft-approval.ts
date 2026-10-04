import { and, eq, inArray } from 'drizzle-orm';
import { isFleetProjectId } from '$lib/domain/fleet-projects';
import { first, type AppDb } from './db/client';
import { connections, draftMedia, drafts, draftVariants } from './db/schema';

function selectedIds(raw: string | null): string[] {
	try {
		const value: unknown = JSON.parse(raw ?? 'null');
		return Array.isArray(value) && value.every((id) => typeof id === 'string')
			? [...new Set(value)].sort()
			: [];
	} catch {
		return [];
	}
}

function sameIds(a: string[], b: string[]): boolean {
	return a.length === b.length && a.every((id, index) => id === b[index]);
}

export async function approvalSnapshot(db: AppDb, draftId: string) {
	const draft = await first(db.select().from(drafts).where(eq(drafts.id, draftId)));
	if (!draft || !isFleetProjectId(draft.projectId)) return null;
	const destinations = selectedIds(draft.selectedConnectionIds);
	if (!destinations.length) return null;
	const [variants, media] = await Promise.all([
		db.select().from(draftVariants).where(eq(draftVariants.draftId, draftId)),
		db.select().from(draftMedia).where(eq(draftMedia.draftId, draftId))
	]);
	const payload = JSON.stringify({
		projectId: draft.projectId,
		title: draft.title,
		body: draft.baseBody,
		destinations,
		variants: variants
			.map((variant) => ({
				platform: variant.platform,
				body: variant.body,
				options: variant.optionsJson
			}))
			.sort((a, b) => a.platform.localeCompare(b.platform)),
		media: media
			.map((item) => ({
				key: item.storageKey,
				mime: item.mime,
				size: item.size,
				altText: item.altText,
				order: item.sortOrder,
				segment: item.segmentIndex
			}))
			.sort((a, b) => a.segment - b.segment || a.order - b.order || a.key.localeCompare(b.key))
	});
	const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(payload));
	const hash = Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, '0')).join(
		''
	);
	return { draft, destinations, hash };
}

export async function approvalProblem(
	db: AppDb,
	draftId: string,
	connectionIds: string[],
	mode: 'exact' | 'subset' = 'exact'
): Promise<string | null> {
	const snapshot = await approvalSnapshot(db, draftId);
	if (!snapshot) return 'Choose a Fleet project and at least one destination before approval';
	if (!snapshot.draft.approvedAt || snapshot.draft.approvalHash !== snapshot.hash) {
		return 'Review and approve this saved draft before publishing';
	}
	const requested = [...new Set(connectionIds)].sort();
	if (
		(mode === 'exact' && !sameIds(requested, snapshot.destinations)) ||
		(mode === 'subset' && requested.some((id) => !snapshot.destinations.includes(id)))
	) {
		return 'Destinations changed since approval';
	}
	return null;
}

export async function approveDraft(db: AppDb, draftId: string, userId: string) {
	const snapshot = await approvalSnapshot(db, draftId);
	if (!snapshot || snapshot.draft.userId !== userId) return null;
	const active = await db
		.select({ id: connections.id })
		.from(connections)
		.where(
			and(
				eq(connections.userId, userId),
				eq(connections.status, 'active'),
				inArray(connections.id, snapshot.destinations)
			)
		);
	if (active.length !== snapshot.destinations.length) return null;
	const approvedAt = new Date();
	await db
		.update(drafts)
		.set({ approvalHash: snapshot.hash, approvedAt })
		.where(and(eq(drafts.id, draftId), eq(drafts.userId, userId)));
	return approvedAt;
}
