import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { loadOwnedDraft } from '$lib/server/draft-record';
import { listConnections } from '$lib/server/connection-list';
import { currentApproval } from '$lib/server/planner';
import { requireSession } from '$lib/server/require';

export const load: PageServerLoad = async ({ locals, params }) => {
	const user = requireSession(locals.user, locals.authMethod);
	const draft = await loadOwnedDraft(locals.db, params.id, user.id);
	if (!draft) error(404, 'Draft not found');
	const [listed, approved] = await Promise.all([
		listConnections(locals.db, locals.env, user.id),
		currentApproval(locals.db, params.id)
	]);
	return { draft, connections: listed.connections, approved, rehearsal: locals.rehearsal === true };
};
