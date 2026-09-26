import { MAX_VIDEO_BYTES } from '$lib/domain/media-limits';
import { mediaByteLength, ProviderError } from './types';
import { providerFetch } from './timed-fetch';
import type {
	ConnectionCredentials,
	FetchLike,
	NormalizedPost,
	PlatformProvider,
	PublishResult,
	ValidationIssue
} from './types';

export const YOUTUBE_UPLOAD_SCOPE = 'https://www.googleapis.com/auth/youtube.upload';
export const YOUTUBE_READ_SCOPE = 'https://www.googleapis.com/auth/youtube.readonly';
export const YOUTUBE_CHUNK_BYTES = 16 * 1024 * 1024;

export class YoutubeUploadInterrupted extends Error {
	constructor(
		message = 'YouTube upload interrupted; the saved session will be checked before retry'
	) {
		super(message);
		this.name = 'YoutubeUploadInterrupted';
	}
}

export class YoutubeUploadUncertain extends Error {
	constructor(message = 'YouTube upload outcome is uncertain; review the channel before retrying') {
		super(message);
		this.name = 'YoutubeUploadUncertain';
	}
}

function redirectUri(appUrl: string): string {
	return `${appUrl.replace(/\/+$/, '')}/api/connections/youtube/callback`;
}

export function youtubeAuthorizeUrl(clientId: string, appUrl: string, state: string): string {
	const url = new URL('https://accounts.google.com/o/oauth2/v2/auth');
	url.searchParams.set('client_id', clientId);
	url.searchParams.set('redirect_uri', redirectUri(appUrl));
	url.searchParams.set('response_type', 'code');
	url.searchParams.set('scope', `${YOUTUBE_UPLOAD_SCOPE} ${YOUTUBE_READ_SCOPE}`);
	url.searchParams.set('access_type', 'offline');
	url.searchParams.set('prompt', 'consent select_account');
	url.searchParams.set('state', state);
	return url.toString();
}

type GoogleToken = {
	access_token?: string;
	refresh_token?: string;
	expires_in?: number;
	scope?: string;
	error?: string;
};

async function tokenResponse(res: Response): Promise<GoogleToken> {
	const body = (await res.json().catch(() => ({}))) as GoogleToken;
	if (!res.ok) {
		throw new ProviderError(
			`Google token request failed (${res.status}): ${body.error || 'unknown error'}`,
			{ status: res.status, code: res.status === 400 || res.status === 401 ? 'auth' : 'upstream' }
		);
	}
	if (!body.access_token || !Number.isFinite(body.expires_in)) {
		throw new Error('Google token response is missing an access token or expiry');
	}
	return body;
}

export async function youtubeExchangeCode(input: {
	clientId: string;
	clientSecret: string;
	code: string;
	appUrl: string;
	fetchImpl?: FetchLike;
}): Promise<ConnectionCredentials> {
	const res = await (input.fetchImpl ?? providerFetch)('https://oauth2.googleapis.com/token', {
		method: 'POST',
		headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
		body: new URLSearchParams({
			client_id: input.clientId,
			client_secret: input.clientSecret,
			code: input.code,
			redirect_uri: redirectUri(input.appUrl),
			grant_type: 'authorization_code'
		})
	});
	const body = await tokenResponse(res);
	const scopes = (body.scope ?? '').split(' ').filter(Boolean);
	if (
		!body.refresh_token ||
		!scopes.includes(YOUTUBE_UPLOAD_SCOPE) ||
		!scopes.includes(YOUTUBE_READ_SCOPE)
	) {
		throw new Error('YouTube upload and read permissions plus offline access are required');
	}
	return {
		accessToken: body.access_token,
		refreshToken: body.refresh_token,
		expiresAt: Date.now() + (body.expires_in ?? 3600) * 1000,
		scopes,
		clientId: input.clientId,
		clientSecret: input.clientSecret
	};
}

export async function youtubeRefresh(
	creds: ConnectionCredentials,
	fetchImpl: FetchLike = providerFetch
): Promise<ConnectionCredentials> {
	if (!creds.refreshToken || !creds.clientId || !creds.clientSecret) {
		throw new ProviderError('YouTube refresh credentials are missing; reconnect the channel', {
			code: 'auth'
		});
	}
	if (creds.accessToken && (creds.expiresAt ?? 0) > Date.now() + 60_000) return creds;
	const res = await fetchImpl('https://oauth2.googleapis.com/token', {
		method: 'POST',
		headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
		body: new URLSearchParams({
			client_id: creds.clientId,
			client_secret: creds.clientSecret,
			refresh_token: creds.refreshToken,
			grant_type: 'refresh_token'
		})
	});
	const body = await tokenResponse(res);
	return {
		...creds,
		accessToken: body.access_token,
		refreshToken: body.refresh_token ?? creds.refreshToken,
		expiresAt: Date.now() + (body.expires_in ?? 3600) * 1000
	};
}

export async function youtubeChannel(
	creds: ConnectionCredentials,
	fetchImpl: FetchLike = providerFetch
): Promise<{ id: string; title: string; avatarUrl?: string }> {
	if (!creds.accessToken) throw new ProviderError('YouTube access token missing', { code: 'auth' });
	const res = await fetchImpl(
		'https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true',
		{
			headers: { Authorization: `Bearer ${creds.accessToken}` }
		}
	);
	if (!res.ok) throw await youtubeApiError('YouTube channel lookup', res);
	const body = (await res.json()) as {
		items?: Array<{
			id?: string;
			snippet?: { title?: string; thumbnails?: { default?: { url?: string } } };
		}>;
	};
	const channel = body.items?.[0];
	if (body.items?.length !== 1 || !channel?.id) {
		throw new Error('Select a Google account with exactly one YouTube channel');
	}
	return {
		id: channel.id,
		title: channel.snippet?.title || channel.id,
		avatarUrl: channel.snippet?.thumbnails?.default?.url
	};
}

async function youtubeApiError(prefix: string, res: Response): Promise<ProviderError> {
	const body = (await res.text()).slice(0, 400);
	const code =
		res.status === 401
			? 'auth'
			: res.status === 403
				? 'forbidden'
				: res.status === 429
					? 'rate_limited'
					: 'upstream';
	return new ProviderError(`${prefix} failed (${res.status})`, {
		status: res.status,
		code,
		detail: body
	});
}

function sessionUrl(raw: string | null): string {
	if (!raw) throw new Error('YouTube did not return a resumable upload URL');
	const url = new URL(raw);
	if (
		url.protocol !== 'https:' ||
		url.hostname !== 'www.googleapis.com' ||
		!url.pathname.startsWith('/upload/youtube/v3/videos')
	) {
		throw new Error('YouTube returned an invalid resumable upload URL');
	}
	return url.toString();
}

function confirmedBytes(res: Response, total: number): number {
	const range = res.headers.get('Range');
	if (!range) return 0;
	const match = /^bytes=0-(\d+)$/.exec(range);
	if (!match) throw new YoutubeUploadUncertain('YouTube returned an invalid upload range');
	const next = Number(match[1]) + 1;
	if (!Number.isSafeInteger(next) || next < 0 || next > total) {
		throw new YoutubeUploadUncertain('YouTube returned an out-of-bounds upload range');
	}
	return next;
}

async function completedVideo(res: Response): Promise<{ id: string; visibility: string }> {
	const body = (await res.json().catch(() => ({}))) as {
		id?: string;
		status?: { privacyStatus?: string };
	};
	if (!body.id || !/^[A-Za-z0-9_-]{1,64}$/.test(body.id)) {
		throw new YoutubeUploadUncertain('YouTube completed an upload without a usable video ID');
	}
	return { id: body.id, visibility: body.status?.privacyStatus || 'unknown' };
}

async function verifiedVideo(
	id: string,
	channelId: string,
	token: string,
	fetchImpl: FetchLike
): Promise<{ id: string; visibility: string }> {
	const url = new URL('https://www.googleapis.com/youtube/v3/videos');
	url.searchParams.set('part', 'snippet,status');
	url.searchParams.set('id', id);
	const res = await fetchImpl(url.toString(), { headers: { Authorization: `Bearer ${token}` } });
	if (!res.ok) throw await youtubeApiError('YouTube video lookup', res);
	const body = (await res.json()) as {
		items?: Array<{
			id?: string;
			snippet?: { channelId?: string };
			status?: { privacyStatus?: string };
		}>;
	};
	const video = body.items?.find((item) => item.id === id && item.snippet?.channelId === channelId);
	if (!video)
		throw new YoutubeUploadUncertain(
			'The uploaded video ID was not found on the connected channel'
		);
	return { id, visibility: video.status?.privacyStatus || 'unknown' };
}

function result(video: { id: string; visibility: string }): PublishResult {
	return {
		remotePostId: video.id,
		remoteUrl: `https://www.youtube.com/watch?v=${encodeURIComponent(video.id)}`,
		visibility: video.visibility
	};
}

export const youtubeProvider: PlatformProvider = {
	id: 'youtube',
	capabilities: {
		maxImages: 0,
		maxImageBytes: 0,
		supportsCW: false,
		supportsVisibility: true,
		supportsThreads: false
	},
	validate(content: NormalizedPost): ValidationIssue[] {
		const issues: ValidationIssue[] = [];
		if (!content.title?.trim() || content.title.trim().length > 100) {
			issues.push({ field: 'title', message: 'YouTube title must be 1–100 characters' });
		}
		if (content.text.length > 5000)
			issues.push({
				field: 'text',
				message: 'YouTube description is too long (max 5,000 characters)'
			});
		if (content.thread?.length && content.thread.length > 1) {
			issues.push({ field: 'thread', message: 'YouTube accepts one video, not a thread' });
		}
		if (
			content.media?.length !== 1 ||
			content.media[0].mime !== 'video/mp4' ||
			mediaByteLength(content.media[0]) > MAX_VIDEO_BYTES ||
			mediaByteLength(content.media[0]) <= 0
		) {
			issues.push({
				field: 'media',
				message: 'YouTube requires exactly one MP4 video up to 95 MB'
			});
		}
		if (content.options?.visibility !== 'private') {
			issues.push({
				field: 'visibility',
				message: 'YouTube uploads are private until the API project is audited'
			});
		}
		return issues;
	},
	refreshImpossibleReason(creds) {
		return creds.refreshToken && creds.clientId && creds.clientSecret
			? null
			: 'YouTube needs a fresh channel connection';
	},
	refreshIfNeeded: youtubeRefresh,
	async publish(content, creds, _meta, fetchImpl = providerFetch, opts): Promise<PublishResult> {
		const upload = opts?.youtube;
		const media = content.media?.[0];
		if (!upload || !media?.storageKey || !creds.accessToken || !creds.youtubeChannelId) {
			throw new Error('YouTube upload requires a connected channel and stored MP4');
		}
		const total = mediaByteLength(media);
		let state = upload.state;
		if (
			state &&
			(state.approvalHash !== upload.approvalHash ||
				state.storageKey !== media.storageKey ||
				state.totalBytes !== total)
		) {
			throw new YoutubeUploadUncertain(
				'The approved video changed after its upload began; review the channel before retrying'
			);
		}
		if (state?.videoId) {
			try {
				return result(
					await verifiedVideo(state.videoId, creds.youtubeChannelId, creds.accessToken, fetchImpl)
				);
			} catch (err) {
				if (
					err instanceof YoutubeUploadUncertain ||
					(err instanceof ProviderError && (err.code === 'auth' || err.code === 'forbidden'))
				)
					throw err;
				throw new YoutubeUploadInterrupted('YouTube video lookup is temporarily unavailable');
			}
		}
		if (!state) {
			const res = await fetchImpl(
				'https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status&notifySubscribers=false',
				{
					method: 'POST',
					headers: {
						Authorization: `Bearer ${creds.accessToken}`,
						'Content-Type': 'application/json; charset=UTF-8',
						'X-Upload-Content-Length': String(total),
						'X-Upload-Content-Type': 'video/mp4'
					},
					body: JSON.stringify({
						snippet: { title: content.title!.trim(), description: content.text, categoryId: '22' },
						status: { privacyStatus: 'private' }
					})
				}
			);
			if (!res.ok) throw await youtubeApiError('YouTube upload start', res);
			state = {
				sessionUrl: sessionUrl(res.headers.get('Location')),
				approvalHash: upload.approvalHash,
				storageKey: media.storageKey,
				totalBytes: total,
				confirmedBytes: 0
			};
			try {
				await upload.saveState(state);
			} catch {
				throw new YoutubeUploadInterrupted('Could not save the new YouTube upload session');
			}
		} else {
			let res: Response;
			try {
				res = await fetchImpl(sessionUrl(state.sessionUrl), {
					method: 'PUT',
					headers: {
						Authorization: `Bearer ${creds.accessToken}`,
						'Content-Length': '0',
						'Content-Range': `bytes */${total}`
					}
				});
			} catch {
				throw new YoutubeUploadInterrupted();
			}
			if (res.status === 200 || res.status === 201) {
				const video = await completedVideo(res);
				try {
					await upload.saveState({ ...state, videoId: video.id, confirmedBytes: total });
				} catch {
					throw new YoutubeUploadInterrupted('Could not save the YouTube video receipt');
				}
				return result(video);
			}
			if (res.status === 404 || res.status === 410) throw new YoutubeUploadUncertain();
			if (res.status >= 500 || res.status === 429) throw new YoutubeUploadInterrupted();
			if (res.status !== 308) throw await youtubeApiError('YouTube upload status', res);
			state = { ...state, confirmedBytes: confirmedBytes(res, total) };
			try {
				await upload.saveState(state);
			} catch {
				throw new YoutubeUploadInterrupted('Could not save YouTube upload progress');
			}
		}

		while (state.confirmedBytes < total) {
			const start = state.confirmedBytes;
			const end = Math.min(start + YOUTUBE_CHUNK_BYTES, total) - 1;
			const bytes = await upload.mediaStore.getRange?.(media.storageKey, start, end);
			if (!bytes || bytes.length !== end - start + 1)
				throw new Error('Stored MP4 range is missing or incomplete');
			let res: Response;
			try {
				res = await fetchImpl(sessionUrl(state.sessionUrl), {
					method: 'PUT',
					headers: {
						Authorization: `Bearer ${creds.accessToken}`,
						'Content-Type': 'video/mp4',
						'Content-Length': String(bytes.length),
						'Content-Range': `bytes ${start}-${end}/${total}`
					},
					body: bytes.buffer.slice(
						bytes.byteOffset,
						bytes.byteOffset + bytes.byteLength
					) as ArrayBuffer
				});
			} catch {
				throw new YoutubeUploadInterrupted();
			}
			if (res.status === 200 || res.status === 201) {
				const video = await completedVideo(res);
				try {
					await upload.saveState({ ...state, videoId: video.id, confirmedBytes: total });
				} catch {
					throw new YoutubeUploadInterrupted('Could not save the YouTube video receipt');
				}
				return result(video);
			}
			if (res.status === 308) {
				const next = confirmedBytes(res, total);
				if (next <= start)
					throw new YoutubeUploadInterrupted('YouTube did not acknowledge the latest video chunk');
				state = { ...state, confirmedBytes: next };
				try {
					await upload.saveState(state);
				} catch {
					throw new YoutubeUploadInterrupted('Could not save YouTube upload progress');
				}
				continue;
			}
			if (res.status >= 500 || res.status === 429) throw new YoutubeUploadInterrupted();
			if (res.status === 404 || res.status === 410) throw new YoutubeUploadUncertain();
			throw await youtubeApiError('YouTube video chunk', res);
		}
		throw new YoutubeUploadUncertain('YouTube reported all bytes received without a video ID');
	}
};
