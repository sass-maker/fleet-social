export function createAgentSurfaceHandler(options: { manifest: Record<string, unknown> }): (request: Request) => Promise<Response | null>;
export function discoveryLinkHeader(origin: string): string;
