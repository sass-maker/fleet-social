import type { PageServerLoad } from './$types';
import { listConnections } from '$lib/server/connection-list';
import { requireUser } from '$lib/server/require';
import { hasCurrentYouTubeConsent } from '$lib/server/youtube-consent';

export const load: PageServerLoad = async ({ locals }) => {
	const user = requireUser(locals.user);
	try {
		const [connectionData, youtubeConsent] = await Promise.all([
			listConnections(locals.db, locals.env, user.id),
			hasCurrentYouTubeConsent(locals.db, user.id)
		]);
		return {
			...connectionData,
			youtubeConsent,
			rehearsal: locals.rehearsal === true,
			loadFailed: false
		};
	} catch (err) {
		// A transient D1 error must not turn the page into a 500: the client
		// keeps its retry path, which this flag arms.
		console.error('[accounts] list failed', err);
		return {
			connections: [],
			configured: { linkedin: false, threads: false, x: false, youtube: false, instagram: false },
			secrets: {},
			appUrl: locals.env.APP_URL,
			youtubeConsent: false,
			rehearsal: locals.rehearsal === true,
			loadFailed: true
		};
	}
};
