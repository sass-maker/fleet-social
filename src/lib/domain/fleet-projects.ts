import projects from './fleet-projects.json';

export const fleetProjects: ReadonlyArray<{ id: string; name: string }> = projects;
const projectIds = new Set(projects.map((project) => project.id));

export function isFleetProjectId(value: unknown): value is string {
	return typeof value === 'string' && projectIds.has(value);
}
