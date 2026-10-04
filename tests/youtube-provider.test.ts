import { describe, expect, it, vi } from 'vitest';
import {
	youtubeProvider,
	YoutubeUploadInterrupted,
	YoutubeUploadUncertain
} from '$lib/server/providers/youtube';
import type { NormalizedPost, YoutubeUploadState } from '$lib/server/providers/types';

const SESSION = 'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=session-one';
const VIDEO = { id: 'abcdefghijk', status: { privacyStatus: 'private' } };
const CONTENT: NormalizedPost = {
	title: 'Private canary',
	text: 'Description',
	media: [{ storageKey: 'video.mp4', mime: 'video/mp4', size: 10 }],
	options: { visibility: 'private' }
};
const CREDS = { accessToken: 'access', youtubeChannelId: 'channel-one' };

function harness(state: YoutubeUploadState | null = null) {
	const saved: YoutubeUploadState[] = [];
	const ranges: Array<[number, number]> = [];
	return {
		saved,
		ranges,
		opts: {
			youtube: {
				state,
				approvalHash: 'approved-revision',
				mediaStore: {
					get: vi.fn(async () => {
						throw new Error('YouTube must not hydrate the full MP4');
					}),
					getRange: async (_key: string, start: number, end: number) => {
						ranges.push([start, end]);
						return new Uint8Array(end - start + 1);
					},
					put: vi.fn(),
					delete: vi.fn()
				},
				saveState: async (next: YoutubeUploadState) => {
					saved.push(next);
				}
			}
		}
	};
}

describe('YouTube resumable publishing', () => {
	it('saves the session before sending a byte range and records the private video ID', async () => {
		const h = harness();
		const fetchImpl = vi.fn(async (_url: URL | RequestInfo, init?: RequestInit) => {
			if (init?.method === 'POST') {
				return new Response(null, { status: 200, headers: { Location: SESSION } });
			}
			expect(h.saved[0]?.sessionUrl).toBe(SESSION);
			expect(new Headers(init?.headers).get('Content-Range')).toBe('bytes 0-9/10');
			return Response.json(VIDEO, { status: 201 });
		});
		const result = await youtubeProvider.publish(CONTENT, CREDS, undefined, fetchImpl, h.opts);
		expect(result).toEqual({
			remotePostId: VIDEO.id,
			remoteUrl: `https://www.youtube.com/watch?v=${VIDEO.id}`,
			visibility: 'private'
		});
		expect(h.ranges).toEqual([[0, 9]]);
		expect(h.saved.at(-1)?.videoId).toBe(VIDEO.id);
		expect(fetchImpl).toHaveBeenCalledTimes(2);
	});

	it('probes a saved session after a lost final response and never uploads twice', async () => {
		const h = harness();
		const firstFetch = vi.fn(async (_url: URL | RequestInfo, init?: RequestInit) => {
			if (init?.method === 'POST')
				return new Response(null, { status: 200, headers: { Location: SESSION } });
			throw new Error('connection lost after YouTube accepted the last chunk');
		});
		await expect(
			youtubeProvider.publish(CONTENT, CREDS, undefined, firstFetch, h.opts)
		).rejects.toBeInstanceOf(YoutubeUploadInterrupted);
		const resume = harness(h.saved[0]);
		const probe = vi.fn(async () => Response.json(VIDEO, { status: 201 }));
		const result = await youtubeProvider.publish(CONTENT, CREDS, undefined, probe, resume.opts);
		expect(result.remotePostId).toBe(VIDEO.id);
		expect(resume.ranges).toEqual([]);
		expect(probe).toHaveBeenCalledTimes(1);
	});

	it('uses the server Range on resume, not the last locally saved offset', async () => {
		const h = harness({
			sessionUrl: SESSION,
			approvalHash: 'approved-revision',
			storageKey: 'video.mp4',
			totalBytes: 10,
			confirmedBytes: 0
		});
		const calls: string[] = [];
		const fetchImpl = vi.fn(async (_url: URL | RequestInfo, init?: RequestInit) => {
			const range = new Headers(init?.headers).get('Content-Range') || '';
			calls.push(range);
			return range === 'bytes */10'
				? new Response(null, { status: 308, headers: { Range: 'bytes=0-4' } })
				: Response.json(VIDEO, { status: 201 });
		});
		await youtubeProvider.publish(CONTENT, CREDS, undefined, fetchImpl, h.opts);
		expect(calls).toEqual(['bytes */10', 'bytes 5-9/10']);
		expect(h.ranges).toEqual([[5, 9]]);
	});

	it('parks an expired session for reconciliation instead of starting another upload', async () => {
		const h = harness({
			sessionUrl: SESSION,
			approvalHash: 'approved-revision',
			storageKey: 'video.mp4',
			totalBytes: 10,
			confirmedBytes: 0
		});
		const fetchImpl = vi.fn(async () => new Response(null, { status: 404 }));
		await expect(
			youtubeProvider.publish(CONTENT, CREDS, undefined, fetchImpl, h.opts)
		).rejects.toBeInstanceOf(YoutubeUploadUncertain);
		expect(fetchImpl).toHaveBeenCalledTimes(1);
	});
});
