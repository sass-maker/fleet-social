/**
 * Accent colour per platform, shared by the editor and the posts list so the
 * two cannot drift (they already had two identical copies).
 */
export const PLATFORM_COLOR: Record<string, string> = {
	mastodon: 'text-indigo-500',
	bluesky: 'text-sky-500',
	linkedin: 'text-blue-600',
	threads: 'text-foreground',
	x: 'text-foreground'
};

export function platformColorClass(platform: string): string {
	return PLATFORM_COLOR[platform] ?? 'text-stone-500';
}
