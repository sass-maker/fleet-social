import type { PageServerLoad } from './$types';
import { requireSession } from '$lib/server/require';

export const load: PageServerLoad = async ({ locals }) => {
	requireSession(locals.user, locals.authMethod);
	return { rehearsal: locals.rehearsal === true };
};
