import { and, eq } from 'drizzle-orm';
import type { RequestHandler } from './$types';
import { approvalProblem, approveDraft, approvalSnapshot } from '$lib/server/draft-approval';
import { first } from '$lib/server/db/client';
import { drafts, publishTargets } from '$lib/server/db/schema';
import { fail, handleError, ok } from '$lib/server/http';
import { requireSession } from '$lib/server/require';

export const GET: RequestHandler = async ({ params, locals }) => {
	try {
		const user = requireSession(locals.user, locals.authMethod);
		const draft = await first(
			locals.db
				.select()
				.from(drafts)
				.where(and(eq(drafts.id, params.id), eq(drafts.userId, user.id)))
		);
		if (!draft) return fail('Not found', 404);
		const snapshot = await approvalSnapshot(locals.db, params.id);
		const problem = snapshot
			? await approvalProblem(locals.db, params.id, snapshot.destinations)
			: 'Choose a Fleet project and at least one destination before approval';
		return ok({
			approved: problem === null,
			approvedAt: problem ? null : draft.approvedAt,
			problem
		});
	} catch (error) {
		return handleError(error);
	}
};

export const POST: RequestHandler = async ({ params, locals }) => {
	try {
		const user = requireSession(locals.user, locals.authMethod);
		const draft = await first(
			locals.db
				.select()
				.from(drafts)
				.where(and(eq(drafts.id, params.id), eq(drafts.userId, user.id)))
		);
		if (!draft) return fail('Not found', 404);
		const inFlight = await locals.db
			.select({ id: publishTargets.id })
			.from(publishTargets)
			.where(and(eq(publishTargets.draftId, params.id), eq(publishTargets.status, 'publishing')))
			.limit(1);
		if (inFlight.length) return fail('Publishing in progress — try again shortly', 409);
		const approvedAt = await approveDraft(locals.db, params.id, user.id);
		if (!approvedAt) return fail('Choose an active Fleet project and connected destinations', 409);
		return ok({ approved: true, approvedAt });
	} catch (error) {
		return handleError(error);
	}
};
