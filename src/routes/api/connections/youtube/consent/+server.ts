import type { RequestHandler } from './$types';
import { fail, handleError, ok } from '$lib/server/http';
import { requireSession } from '$lib/server/require';
import { hasCurrentYouTubeConsent, recordYouTubeConsent } from '$lib/server/youtube-consent';

export const POST: RequestHandler = async ({ request, locals }) => {
	try {
		const user = requireSession(locals.user, locals.authMethod);
		const body = await request.json().catch(() => null);
		if (body?.accepted !== true) return fail('Agree to the current privacy policy first', 400);
		await recordYouTubeConsent(locals.db, user.id);
		if (!(await hasCurrentYouTubeConsent(locals.db, user.id))) {
			return fail('Could not save privacy agreement', 503);
		}
		return ok({ accepted: true });
	} catch (err) {
		return handleError(err);
	}
};
