import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAppHealthClient } from '@saas-maker/app-health';
import { handle } from '../src/hooks.server';

vi.mock('@saas-maker/app-health', () => ({ createAppHealthClient: vi.fn() }));

afterEach(() => vi.resetAllMocks());

describe('endpoint telemetry fail-open request lifecycle', () => {
	it('returns the response while asynchronous delivery is still pending and absorbs rejection', async () => {
		let rejectDelivery: (error: Error) => void = () => {};
		const pending = new Promise<void>((_resolve, reject) => {
			rejectDelivery = reject;
		});
		vi.mocked(createAppHealthClient).mockReturnValue({
			record: () => {},
			flush: () => pending
		} as never);
		const deliveries: Promise<unknown>[] = [];
		const response = await handle({
			event: {
				url: new URL('http://localhost/api/drafts'),
				request: new Request('http://localhost/api/drafts', { method: 'OPTIONS' }),
				route: { id: '/api/drafts' },
				platform: {
					env: { APP_HEALTH_INGEST_KEY: 'synthetic-key' },
					ctx: { waitUntil: (delivery: Promise<unknown>) => deliveries.push(delivery) }
				}
			},
			resolve: () => new Response('unexpected')
		} as never);
		expect(response.status).toBe(204);
		expect(deliveries).toHaveLength(1);
		rejectDelivery(new Error('synthetic asynchronous SDK failure'));
		await expect(Promise.all(deliveries)).resolves.toEqual([undefined]);
	});

	for (const operation of ['record', 'flush'] as const) {
		it(`preserves the response when ${operation} throws synchronously`, async () => {
			vi.mocked(createAppHealthClient).mockReturnValue({
				record: () => {
					if (operation === 'record') throw new Error('synthetic SDK failure');
				},
				flush: () => {
					throw new Error('synthetic SDK failure');
				}
			} as never);
			const deliveries: Promise<unknown>[] = [];
			const response = await handle({
				event: {
					url: new URL('http://localhost/api/drafts'),
					request: new Request('http://localhost/api/drafts', { method: 'OPTIONS' }),
					route: { id: '/api/drafts' },
					platform: {
						env: { APP_HEALTH_INGEST_KEY: 'synthetic-key' },
						ctx: { waitUntil: (delivery: Promise<unknown>) => deliveries.push(delivery) }
					}
				},
				resolve: () => {
					throw new Error('OPTIONS must complete without resolving');
				}
			} as never);
			await Promise.all(deliveries);
			expect(response.status).toBe(204);
			expect(await response.text()).toBe('');
		});
	}
});
