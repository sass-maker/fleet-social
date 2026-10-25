import type { RequestHandler } from './$types';
import { claimSlots } from '$lib/server/generation';
import { fail, handleError, ok } from '$lib/server/http';
import { requireAnyScope, requireUser } from '$lib/server/require';

/** How a local generator, which the Worker cannot call, collects due slots. */
export const POST: RequestHandler = async ({ request, locals }) => {
	try {
		const user = requireUser(locals.user);
		requireAnyScope(locals, ['write', 'intake']);
		const body = (await request.json().catch(() => null)) as { generator?: unknown } | null;
		if (typeof body?.generator !== 'string' || !body.generator) return fail('generator required');
		const requests = await claimSlots(locals.db, user.id, body.generator, locals.env.APP_URL);
		return ok({ requests });
	} catch (err) {
		return handleError(err);
	}
};
