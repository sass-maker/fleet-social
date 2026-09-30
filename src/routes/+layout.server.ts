import type { LayoutServerLoad } from './$types';
import { readStoredAppName } from '$lib/server/app-settings';
import { readStudioProjectId } from '$lib/domain/studio-footer';

export const load: LayoutServerLoad = async ({ locals, platform }) => {
	// Name and photo ride along on the session read in hooks, so this load
	// does not query the user again.
	const storedAppName = await readStoredAppName(locals.db);
	return {
		user: locals.user,
		displayName: locals.user?.displayName ?? null,
		profilePictureUrl: locals.user?.profilePictureUrl ?? '',
		appName: storedAppName ?? locals.env.APP_NAME,
		studioProjectId: readStudioProjectId(
			platform ? Reflect.get(platform.env, 'SAAS_MAKER_PROJECT_ID') : undefined
		)
	};
};
