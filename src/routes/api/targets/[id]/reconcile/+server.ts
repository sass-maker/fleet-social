import { and, eq } from 'drizzle-orm';
import type { RequestHandler } from './$types';
import { first } from '$lib/server/db/client';
import { drafts, publishTargets } from '$lib/server/db/schema';
import { fail, handleError, ok } from '$lib/server/http';
import { refreshDraftStatus } from '$lib/server/publish';
import { requireSession } from '$lib/server/require';

/** Owner-attested reconciliation after checking the destination account. */
export const POST: RequestHandler = async ({ params, request, locals }) => {
	try {
		const user = requireSession(locals.user, locals.authMethod);
		const target = await first(
			locals.db.select().from(publishTargets).where(eq(publishTargets.id, params.id))
		);
		if (!target) return fail('Not found', 404);
		const draft = await first(locals.db.select().from(drafts).where(eq(drafts.id, target.draftId)));
		if (!draft || draft.userId !== user.id) return fail('Not found', 404);
		if (target.status !== 'uncertain') return fail('This outcome is no longer uncertain', 409);
		const body = await request.json().catch(() => null);
		if (!body || typeof body !== 'object') return fail('Invalid JSON body', 400);
		const action = (body as Record<string, unknown>).action;
		const now = new Date();
		if (action === 'published') {
			const remoteUrl = (body as Record<string, unknown>).remoteUrl;
			if (typeof remoteUrl !== 'string' || remoteUrl.length > 2048)
				return fail('Post URL required', 400);
			try {
				if (new URL(remoteUrl).protocol !== 'https:') return fail('Use the HTTPS post URL', 400);
			} catch {
				return fail('Use the HTTPS post URL', 400);
			}
			const [updated] = await locals.db
				.update(publishTargets)
				.set({
					status: 'published',
					remoteUrl,
					errorMessage: null,
					updatedAt: now
				})
				.where(and(eq(publishTargets.id, params.id), eq(publishTargets.status, 'uncertain')))
				.returning({ id: publishTargets.id });
			if (!updated) return fail('This outcome changed; refresh Posts', 409);
		} else if (action === 'not_published') {
			if ((body as Record<string, unknown>).confirmation !== 'I checked the destination account') {
				return fail('Confirm that you checked the destination account', 400);
			}
			const [updated] = await locals.db
				.update(publishTargets)
				.set({
					status: 'failed',
					errorMessage: 'Owner confirmed no post exists. Retry when ready.',
					updatedAt: now
				})
				.where(and(eq(publishTargets.id, params.id), eq(publishTargets.status, 'uncertain')))
				.returning({ id: publishTargets.id });
			if (!updated) return fail('This outcome changed; refresh Posts', 409);
		} else {
			return fail('action must be published or not_published', 400);
		}
		await refreshDraftStatus(locals.db, target.draftId);
		return ok({ status: action === 'published' ? 'published' : 'failed' });
	} catch (error) {
		return handleError(error);
	}
};
