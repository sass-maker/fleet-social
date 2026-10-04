import type { PageServerLoad } from './$types';
import { loadPlanner } from '$lib/server/planner';
import { requireUser } from '$lib/server/require';
export const load: PageServerLoad = async ({ locals }) => {
	const user = requireUser(locals.user);
	return { ...(await loadPlanner(locals.db, user.id)), rehearsal: locals.rehearsal === true };
};
