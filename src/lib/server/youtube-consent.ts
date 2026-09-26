import { eq } from 'drizzle-orm';
import { first, type AppDb } from './db/client';
import { appSettings } from './db/schema';

export const YOUTUBE_POLICY_VERSION = '2026-09-26';

function consentKey(userId: string): string {
	return `youtube_policy_consent:${userId}`;
}

export async function hasCurrentYouTubeConsent(db: AppDb, userId: string): Promise<boolean> {
	const row = await first(
		db
			.select({ value: appSettings.value })
			.from(appSettings)
			.where(eq(appSettings.key, consentKey(userId)))
	);
	return row?.value === YOUTUBE_POLICY_VERSION;
}

export async function recordYouTubeConsent(db: AppDb, userId: string): Promise<void> {
	const now = new Date();
	await db
		.insert(appSettings)
		.values({ key: consentKey(userId), value: YOUTUBE_POLICY_VERSION, updatedAt: now })
		.onConflictDoUpdate({
			target: appSettings.key,
			set: { value: YOUTUBE_POLICY_VERSION, updatedAt: now }
		});
}

export async function deleteYouTubeConsent(db: AppDb, userId: string): Promise<void> {
	await db.delete(appSettings).where(eq(appSettings.key, consentKey(userId)));
}
