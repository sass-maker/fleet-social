import { describe, expect, it } from 'vitest';
import {
	instagramAuthorizeUrl,
	instagramExchangeCode,
	instagramAccount,
	instagramProvider,
	InstagramProcessingPending,
	InstagramUploadUncertain
} from '$lib/server/providers/instagram';
import type { InstagramUploadState, NormalizedPost } from '$lib/server/providers/types';

const content: NormalizedPost = {
	text: 'An approved Reel',
	media: [{ storageKey: 'video.mp4', mime: 'video/mp4', size: 10 }]
};
const creds = { accessToken: 'test-token', instagramUserId: '12345' };
function harness(state: InstagramUploadState | null = null) {
	const saved: InstagramUploadState[] = [];
	return {
		saved,
		opts: {
			mediaUrlFor: () => 'https://social.example/api/media/public/video.mp4?sig=test',
			instagram: {
				state,
				approvalHash: 'revision-one',
				saveState: async (value: InstagramUploadState) => {
					saved.push(value);
				}
			}
		}
	};
}

describe('Instagram native login', () => {
	it('requests only basic and content-publishing permissions, with bound state and the exact callback', () => {
		const url = new URL(instagramAuthorizeUrl('client', 'https://social.example/', 'bound-state'));
		expect(url.origin + url.pathname).toBe('https://www.instagram.com/oauth/authorize');
		expect(url.searchParams.get('scope')).toBe(
			'instagram_business_basic,instagram_business_content_publish'
		);
		expect(url.searchParams.get('state')).toBe('bound-state');
		expect(url.searchParams.get('redirect_uri')).toBe(
			'https://social.example/api/connections/instagram/callback'
		);
	});
	it('handles the current wrapped token and account responses and stores a real professional account ID', async () => {
		const values = [
			{
				data: [
					{
						access_token: 'short',
						user_id: 'app-scoped-id',
						permissions: 'instagram_business_basic,instagram_business_content_publish'
					}
				]
			},
			{ access_token: 'long', expires_in: 5_184_000 },
			{ data: [{ user_id: '12345', username: 'creator' }] }
		];
		values[0].data![0].user_id = '11111';
		const fetchImpl = async () => Response.json(values.shift());
		const exchanged = await instagramExchangeCode({
			clientId: 'client',
			clientSecret: 'secret',
			code: 'code',
			appUrl: 'https://social.example',
			fetchImpl
		});
		const account = await instagramAccount(exchanged, fetchImpl);
		expect(exchanged.accessToken).toBe('long');
		expect(account).toEqual({ id: '12345', username: 'creator' });
	});
	it('rejects partial permissions before the long-lived token exchange', async () => {
		await expect(
			instagramExchangeCode({
				clientId: 'client',
				clientSecret: 'secret',
				code: 'code',
				appUrl: 'https://social.example',
				fetchImpl: async () =>
					Response.json({
						data: [
							{ access_token: 'short', user_id: '12345', permissions: 'instagram_business_basic' }
						]
					})
			})
		).rejects.toThrow('content publishing permissions');
	});
});

describe('Instagram Reel delivery', () => {
	it('reuses a saved processing container, then records a verified media ID and permalink', async () => {
		const first = harness();
		const requests: string[] = [];
		await expect(
			instagramProvider.publish(
				content,
				creds,
				undefined,
				async (url) => {
					requests.push(String(url));
					return Response.json(
						String(url).includes('/media') ? { id: '22222' } : { status_code: 'IN_PROGRESS' }
					);
				},
				first.opts
			)
		).rejects.toBeInstanceOf(InstagramProcessingPending);
		const resumed = harness({ ...first.saved.at(-1)!, lastPollAt: Date.now() - 61_000 });
		const result = await instagramProvider.publish(
			content,
			creds,
			undefined,
			async (url, init) => {
				requests.push(String(url));
				if (String(url).includes('status_code')) return Response.json({ status_code: 'FINISHED' });
				if (String(url).includes('media_publish')) {
					expect(resumed.saved.at(-1)?.publishStarted).toBe(true);
					expect(JSON.parse(String(init?.body))).toEqual({ creation_id: '22222' });
					return Response.json({ id: '33333' });
				}
				return Response.json({
					id: '33333',
					owner: { id: '12345' },
					permalink: 'https://www.instagram.com/reel/confirmed/'
				});
			},
			resumed.opts
		);
		expect(result).toEqual({
			remotePostId: '33333',
			remoteUrl: 'https://www.instagram.com/reel/confirmed/',
			visibility: 'public'
		});
		expect(requests.filter((url) => url.endsWith('/media')).length).toBe(1);
	});
	it('parks a lost publish response and refuses to publish its saved container twice', async () => {
		const first = harness({
			containerId: '22222',
			approvalHash: 'revision-one',
			storageKey: 'video.mp4',
			userId: '12345'
		});
		await expect(
			instagramProvider.publish(
				content,
				creds,
				undefined,
				async (url) => {
					if (String(url).includes('status_code'))
						return Response.json({ status_code: 'FINISHED' });
					throw new Error('response lost after publish');
				},
				first.opts
			)
		).rejects.toBeInstanceOf(InstagramUploadUncertain);
		const resumed = harness(first.saved.at(-1)!);
		await expect(
			instagramProvider.publish(
				content,
				creds,
				undefined,
				async () => {
					throw new Error('A second provider call must not happen');
				},
				resumed.opts
			)
		).rejects.toBeInstanceOf(InstagramUploadUncertain);
	});
	it('refuses an upload state from another approved revision or account', async () => {
		const h = harness({
			containerId: '22222',
			approvalHash: 'old-revision',
			storageKey: 'video.mp4',
			userId: '12345'
		});
		await expect(
			instagramProvider.publish(
				content,
				creds,
				undefined,
				async () => {
					throw new Error('No provider request expected');
				},
				h.opts
			)
		).rejects.toThrow('changed after delivery started');
	});
});
