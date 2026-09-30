export function readStudioProjectId(value: unknown): string | null {
	if (typeof value !== 'string') return null;
	const projectId = value.trim();
	return /^[a-z0-9][a-z0-9-]{0,63}$/.test(projectId) ? projectId : null;
}
