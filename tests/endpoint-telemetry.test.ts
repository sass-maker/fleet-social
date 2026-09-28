import { describe, expect, it, vi } from 'vitest';
import { createEndpointTelemetry } from '../src/lib/server/endpoint-telemetry';

describe('App Health endpoint telemetry', () => {
	it('sends only a normalized route summary through bounded Worker delivery', async () => {
		let body = '';
		const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
			body = String(init?.body ?? '');
			return new Response(null, { status: 202 });
		});
		const record = createEndpointTelemetry(
			{ APP_HEALTH_INGEST_KEY: 'test-key', APP_HEALTH_ENVIRONMENT: 'production' },
			fetch
		);

		await record?.({
			method: 'get',
			route: '/api/drafts/[id]',
			status_code: 200,
			duration_ms: 12.4
		});

		const batch = JSON.parse(body) as {
			runtime: string;
			events: Array<Record<string, unknown>>;
		};
		expect(batch.runtime).toBe('worker');
		expect(batch.events).toHaveLength(1);
		expect(batch.events[0]).toMatchObject({
			method: 'GET',
			route: '/api/drafts/[id]',
			status_code: 200,
			duration_ms: 12
		});
		expect(Object.keys(batch.events[0]).sort()).toEqual(
			['duration_ms', 'event_id', 'method', 'release', 'route', 'status_code', 'timestamp'].sort()
		);
		expect(fetch).toHaveBeenCalledOnce();
	});

	it('disables collection when the ingest key is absent', () => {
		expect(createEndpointTelemetry({ APP_HEALTH_ENVIRONMENT: 'production' })).toBeNull();
	});
});
