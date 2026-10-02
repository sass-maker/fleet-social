import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	createEndpointTelemetry,
	recordEndpointRequest
} from '../src/lib/server/endpoint-telemetry';

afterEach(() => vi.unstubAllGlobals());

describe('App Health endpoint telemetry', () => {
	it('waits for the SDK auto-flush and bounded retry rather than resolving early', async () => {
		const bodies: string[] = [];
		let complete: (response: Response) => void = () => {};
		const pending = new Promise<Response>((resolve) => {
			complete = resolve;
		});
		const record = createEndpointTelemetry(
			{ APP_HEALTH_INGEST_KEY: 'synthetic-key' },
			async (_input, init) => {
				bodies.push(String(init?.body));
				return bodies.length === 1 ? pending : new Response(null, { status: 202 });
			}
		);
		let settled = false;
		const delivery = record?.({
			method: 'GET',
			route: '/api/drafts',
			status_code: 200,
			duration_ms: 1
		});
		void delivery?.then(() => {
			settled = true;
		});
		await Promise.resolve();
		expect(settled).toBe(false);
		complete(new Response(null, { status: 503 }));
		await delivery;
		expect(settled).toBe(true);
		expect(bodies).toHaveLength(2);
		expect(bodies[1]).toBe(bodies[0]);
	});

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

	it('uses the matched template and omits concrete request data at the Worker boundary', async () => {
		let body = '';
		vi.stubGlobal('fetch', async (_input: RequestInfo | URL, init?: RequestInit) => {
			body = String(init?.body ?? '');
			return new Response(null, { status: 202 });
		});
		const deliveries: Promise<unknown>[] = [];
		recordEndpointRequest(
			{
				route: { id: '/api/drafts/[id]' },
				request: new Request('http://localhost/api/drafts/private-id?secret=private-query', {
					method: 'POST',
					headers: { cookie: 'session=private-cookie', 'x-user': 'private-user' },
					body: 'private-content'
				}),
				platform: {
					env: { APP_HEALTH_INGEST_KEY: 'synthetic-key' },
					ctx: { waitUntil: (delivery: Promise<unknown>) => deliveries.push(delivery) }
				}
			} as never,
			409,
			8.7
		);
		await Promise.all(deliveries);
		expect(JSON.parse(body).events[0]).toMatchObject({
			method: 'POST',
			route: '/api/drafts/[id]',
			status_code: 409,
			duration_ms: 9
		});
		expect(body).not.toMatch(/private-(id|query|cookie|user|content)/);
	});

	it('excludes unmatched requests before creating a delivery', () => {
		const deliveries: Promise<unknown>[] = [];
		recordEndpointRequest(
			{
				route: { id: null },
				platform: {
					env: { APP_HEALTH_INGEST_KEY: 'synthetic-key' },
					ctx: { waitUntil: (delivery: Promise<unknown>) => deliveries.push(delivery) }
				}
			} as never,
			404,
			1
		);
		expect(deliveries).toEqual([]);
	});
});
