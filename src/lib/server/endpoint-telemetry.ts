import { createAppHealthClient } from '@saas-maker/app-health';
import type { RequestEvent } from '@sveltejs/kit';

const INGEST_ENDPOINT = 'https://ingest.sassmaker.com/v1/ingest';

export function createEndpointTelemetry(
	env: Pick<Env, 'APP_HEALTH_INGEST_KEY' | 'APP_HEALTH_ENVIRONMENT'>,
	fetchImpl: typeof fetch = fetch
) {
	if (!env.APP_HEALTH_INGEST_KEY) return null;
	try {
		const client = createAppHealthClient({
			key: env.APP_HEALTH_INGEST_KEY,
			endpoint: INGEST_ENDPOINT,
			environment: env.APP_HEALTH_ENVIRONMENT,
			release: __APP_VERSION__,
			runtime: 'worker',
			maxQueueSize: 1,
			maxBatchSize: 1,
			requestTimeoutMs: 1000,
			maxRetries: 1,
			disableTimer: true,
			fetch: fetchImpl
		});
		return (event: { method: string; route: string; status_code: number; duration_ms: number }) => {
			client.record(event);
			return client.flush();
		};
	} catch {
		return null;
	}
}

export function recordEndpointRequest(
	event: RequestEvent,
	status: number,
	durationMs: number
): void {
	const route = event.route.id;
	const platform = event.platform;
	if (!route || !platform) return;
	const record = createEndpointTelemetry(platform.env);
	if (!record) return;
	const delivery = record({
		method: event.request.method,
		route,
		status_code: status,
		duration_ms: durationMs
	}).catch(() => {});
	try {
		platform.ctx.waitUntil(delivery);
	} catch {
		void delivery;
	}
}
