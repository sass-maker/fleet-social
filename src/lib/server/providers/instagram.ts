import { MAX_VIDEO_BYTES } from '$lib/domain/media-limits';
import { mediaByteLength, ProviderError } from './types';
import { providerFetch } from './timed-fetch';
import type {
	ConnectionCredentials,
	FetchLike,
	InstagramUploadState,
	PlatformProvider,
	PublishResult
} from './types';

export const INSTAGRAM_SCOPES = ['instagram_business_basic', 'instagram_business_content_publish'];
const GRAPH = 'https://graph.instagram.com/v26.0';

export class InstagramProcessingPending extends ProviderError {
	constructor(
		message = 'Instagram is processing the Reel; its saved container will be checked in one minute'
	) {
		super(message, { code: 'upstream' });
		this.name = 'InstagramProcessingPending';
	}
}

export class InstagramUploadUncertain extends Error {
	constructor(
		message = 'Instagram delivery outcome is uncertain; check the account before reconciling'
	) {
		super(message);
		this.name = 'InstagramUploadUncertain';
	}
}

function redirectUri(appUrl: string) {
	return `${appUrl.replace(/\/+$/, '')}/api/connections/instagram/callback`;
}

export function instagramAuthorizeUrl(clientId: string, appUrl: string, state: string) {
	const url = new URL('https://www.instagram.com/oauth/authorize');
	url.search = new URLSearchParams({
		client_id: clientId,
		redirect_uri: redirectUri(appUrl),
		response_type: 'code',
		scope: INSTAGRAM_SCOPES.join(','),
		state,
		enable_fb_login: 'false',
		force_reauth: 'true'
	}).toString();
	return url.toString();
}

async function apiJson(res: Response, operation: string): Promise<Record<string, unknown>> {
	const body = await res.json().catch(() => ({}));
	if (!res.ok || body.error) {
		const metaCode = body.error?.code;
		const code =
			metaCode === 190 || res.status === 401
				? 'auth'
				: metaCode === 10 || metaCode === 200 || res.status === 403
					? 'forbidden'
					: res.status === 429 || metaCode === 4 || metaCode === 32
						? 'rate_limited'
						: 'upstream';
		throw new ProviderError(
			`Instagram ${operation} failed (${res.status}${typeof metaCode === 'number' ? `, code ${metaCode}` : ''})`,
			{ status: res.status, code }
		);
	}
	return body;
}

function numericId(value: unknown): string {
	if (typeof value === 'string' && /^\d+$/.test(value)) return value;
	if (typeof value === 'number' && Number.isSafeInteger(value) && value > 0) return String(value);
	throw new Error('Instagram did not return a usable account or media ID');
}

function token(body: Record<string, unknown>) {
	if (
		typeof body.access_token !== 'string' ||
		!body.access_token ||
		typeof body.expires_in !== 'number' ||
		body.expires_in <= 0 ||
		!Number.isFinite(body.expires_in)
	)
		throw new Error('Instagram token response is missing its token or expiry');
	return {
		accessToken: body.access_token,
		expiresAt: Date.now() + body.expires_in * 1000,
		tokenIssuedAt: Date.now()
	};
}

export async function instagramExchangeCode(input: {
	clientId: string;
	clientSecret: string;
	code: string;
	appUrl: string;
	fetchImpl?: FetchLike;
}): Promise<ConnectionCredentials> {
	const fetchImpl = input.fetchImpl ?? providerFetch;
	const form = new FormData();
	for (const [key, value] of Object.entries({
		client_id: input.clientId,
		client_secret: input.clientSecret,
		grant_type: 'authorization_code',
		redirect_uri: redirectUri(input.appUrl),
		code: input.code
	}))
		form.set(key, value);
	const exchanged = await apiJson(
		await fetchImpl('https://api.instagram.com/oauth/access_token', { method: 'POST', body: form }),
		'code exchange'
	);
	const entries = exchanged.data;
	if (
		entries !== undefined &&
		(!Array.isArray(entries) || entries.length !== 1 || exchanged.access_token !== undefined)
	)
		throw new Error('Instagram returned an ambiguous token response');
	const short = (Array.isArray(entries) ? entries[0] : exchanged) as Record<string, unknown>;
	const scopes =
		typeof short.permissions === 'string'
			? short.permissions.split(/[ ,]+/).filter(Boolean)
			: Array.isArray(short.permissions)
				? short.permissions.filter((scope): scope is string => typeof scope === 'string')
				: [];
	if (
		typeof short.access_token !== 'string' ||
		!INSTAGRAM_SCOPES.every((scope) => scopes.includes(scope))
	)
		throw new Error('Instagram basic and content publishing permissions are required');
	const userId = numericId(short.user_id);
	const url = new URL('https://graph.instagram.com/access_token');
	url.search = new URLSearchParams({
		grant_type: 'ig_exchange_token',
		client_secret: input.clientSecret,
		access_token: short.access_token
	}).toString();
	const long = token(await apiJson(await fetchImpl(url.toString()), 'long-lived token exchange'));
	return { ...long, scopes, instagramUserId: userId };
}

export async function instagramRefresh(
	creds: ConnectionCredentials,
	fetchImpl: FetchLike = providerFetch
): Promise<ConnectionCredentials> {
	if (!creds.accessToken || !creds.expiresAt || creds.expiresAt <= Date.now())
		throw new ProviderError('Instagram token expired — reconnect the account', { code: 'auth' });
	if (
		creds.expiresAt > Date.now() + 7 * 24 * 60 * 60_000 ||
		(creds.tokenIssuedAt ?? 0) > Date.now() - 24 * 60 * 60_000
	)
		return creds;
	const url = new URL('https://graph.instagram.com/refresh_access_token');
	url.search = new URLSearchParams({
		grant_type: 'ig_refresh_token',
		access_token: creds.accessToken
	}).toString();
	return { ...creds, ...token(await apiJson(await fetchImpl(url.toString()), 'token refresh')) };
}

export async function instagramAccount(
	creds: ConnectionCredentials,
	fetchImpl: FetchLike = providerFetch
) {
	if (!creds.accessToken) throw new ProviderError('Instagram token missing', { code: 'auth' });
	const response = await apiJson(
		await fetchImpl(`${GRAPH}/me?fields=user_id,username`, {
			headers: { Authorization: `Bearer ${creds.accessToken}` }
		}),
		'account lookup'
	);
	if (response.data !== undefined && (!Array.isArray(response.data) || response.data.length !== 1))
		throw new Error('Instagram returned an ambiguous account response');
	const body = (Array.isArray(response.data) ? response.data[0] : response) as Record<
		string,
		unknown
	>;
	const id = numericId(body.user_id);
	if (typeof body.username !== 'string' || !body.username)
		throw new Error('Instagram account username is missing');
	return { id, username: body.username };
}

export const instagramProvider: PlatformProvider = {
	id: 'instagram',
	capabilities: {
		maxImages: 0,
		maxImageBytes: 0,
		supportsCW: false,
		supportsVisibility: false,
		supportsThreads: false
	},
	validate(content) {
		const issues = [];
		if (content.text.length > 2200)
			issues.push({
				field: 'text',
				message: 'Instagram caption is too long (max 2,200 characters)'
			});
		if (content.thread?.length && content.thread.length > 1)
			issues.push({ field: 'thread', message: 'Instagram Reels accept one video, not a thread' });
		const media = content.media?.[0];
		if (
			content.media?.length !== 1 ||
			media?.mime !== 'video/mp4' ||
			mediaByteLength(media) <= 0 ||
			mediaByteLength(media) > MAX_VIDEO_BYTES
		)
			issues.push({
				field: 'media',
				message: 'Instagram Reels require exactly one MP4 video up to 95 MB'
			});
		return issues;
	},
	refreshIfNeeded: instagramRefresh,
	refreshImpossibleReason(creds) {
		return !creds.accessToken || !creds.expiresAt || creds.expiresAt <= Date.now()
			? 'Instagram needs a fresh account connection'
			: null;
	},
	async publish(content, creds, _meta, fetchImpl = providerFetch, opts): Promise<PublishResult> {
		const upload = opts?.instagram;
		const media = content.media?.[0];
		if (
			!upload ||
			!opts?.mediaUrlFor ||
			!media?.storageKey ||
			!creds.accessToken ||
			!creds.instagramUserId
		)
			throw new Error('Instagram requires a connected professional account and stored MP4');
		const userId = numericId(creds.instagramUserId);
		const headers = {
			Authorization: `Bearer ${creds.accessToken}`,
			'Content-Type': 'application/json'
		};
		let state = upload.state;
		if (
			state &&
			(state.approvalHash !== upload.approvalHash ||
				state.storageKey !== media.storageKey ||
				state.userId !== userId)
		)
			throw new InstagramUploadUncertain(
				'The approved Reel or destination changed after delivery started'
			);
		const save = async (next: InstagramUploadState) => {
			await upload.saveState(next);
			state = next;
		};
		const receipt = async (mediaId: string) => {
			const body = await apiJson(
				await fetchImpl(`${GRAPH}/${numericId(mediaId)}?fields=id,permalink,owner`, { headers }),
				'published Reel lookup'
			);
			if (
				numericId(body.id) !== mediaId ||
				numericId(
					typeof body.owner === 'object' && body.owner !== null
						? (body.owner as { id?: string }).id
						: body.owner
				) !== userId
			)
				throw new InstagramUploadUncertain(
					'The returned Reel was not verified on the connected account'
				);
			const url = typeof body.permalink === 'string' ? new URL(body.permalink) : null;
			if (
				!url ||
				url.protocol !== 'https:' ||
				!['www.instagram.com', 'instagram.com'].includes(url.hostname)
			)
				throw new InstagramProcessingPending(
					'Instagram saved the Reel ID; its permalink is not available yet'
				);
			return { remotePostId: mediaId, remoteUrl: url.toString(), visibility: 'public' };
		};
		if (state?.mediaId) {
			try {
				return await receipt(state.mediaId);
			} catch (err) {
				if (
					err instanceof InstagramUploadUncertain ||
					(err instanceof ProviderError && ['auth', 'forbidden'].includes(err.code ?? ''))
				)
					throw err;
				throw new InstagramProcessingPending(
					'Instagram saved the Reel ID; its receipt lookup will be retried'
				);
			}
		}
		if (state?.publishStarted) throw new InstagramUploadUncertain();
		if (!state) {
			const mediaUrl = new URL(await opts.mediaUrlFor(media.storageKey));
			if (
				mediaUrl.protocol !== 'https:' ||
				mediaUrl.hostname === 'localhost' ||
				mediaUrl.hostname.endsWith('.localhost') ||
				/^127\./.test(mediaUrl.hostname)
			)
				throw new Error('Instagram needs an HTTPS media URL it can fetch from the public internet');
			let created: Record<string, unknown>;
			try {
				created = await apiJson(
					await fetchImpl(`${GRAPH}/${userId}/media`, {
						method: 'POST',
						headers,
						body: JSON.stringify({
							media_type: 'REELS',
							video_url: mediaUrl.toString(),
							caption: content.text,
							share_to_feed: true
						})
					}),
					'Reel container creation'
				);
			} catch (err) {
				if (err instanceof ProviderError && err.status && err.status < 500) throw err;
				throw new InstagramProcessingPending(
					'Instagram container creation was interrupted; no publish request was sent'
				);
			}
			await save({
				containerId: numericId(created.id),
				approvalHash: upload.approvalHash,
				storageKey: media.storageKey,
				userId
			});
		}
		const containerId = numericId(state!.containerId);
		if (state!.lastPollAt && Date.now() - state!.lastPollAt < 60_000)
			throw new InstagramProcessingPending();
		await save({ ...state!, lastPollAt: Date.now() });
		let progress: Record<string, unknown>;
		try {
			progress = await apiJson(
				await fetchImpl(`${GRAPH}/${containerId}?fields=status_code,status`, { headers }),
				'Reel processing status'
			);
		} catch (err) {
			if (err instanceof ProviderError && ['auth', 'forbidden'].includes(err.code ?? '')) throw err;
			throw new InstagramProcessingPending(
				'Instagram processing status is temporarily unavailable'
			);
		}
		if (progress.status_code === 'IN_PROGRESS') throw new InstagramProcessingPending();
		if (progress.status_code === 'PUBLISHED') throw new InstagramUploadUncertain();
		if (progress.status_code === 'ERROR' || progress.status_code === 'EXPIRED')
			throw new ProviderError(
				`Instagram media container ${progress.status_code.toLowerCase()} — check the video before retrying`,
				{ code: 'forbidden' }
			);
		if (progress.status_code !== 'FINISHED')
			throw new InstagramUploadUncertain('Instagram returned an unknown processing state');
		await save({ ...state!, publishStarted: true });
		let published: Record<string, unknown>;
		try {
			published = await apiJson(
				await fetchImpl(`${GRAPH}/${userId}/media_publish`, {
					method: 'POST',
					headers,
					body: JSON.stringify({ creation_id: containerId })
				}),
				'Reel publish'
			);
		} catch {
			throw new InstagramUploadUncertain();
		}
		let mediaId: string;
		try {
			mediaId = numericId(published.id);
		} catch {
			throw new InstagramUploadUncertain();
		}
		try {
			await save({ ...state!, mediaId });
		} catch {
			throw new InstagramUploadUncertain(
				'Instagram returned a Reel ID, but its receipt could not be saved; reconcile before retrying'
			);
		}
		try {
			return await receipt(mediaId);
		} catch (err) {
			if (err instanceof InstagramUploadUncertain) throw err;
			throw new InstagramProcessingPending(
				'Instagram saved the Reel ID; its receipt lookup will be retried'
			);
		}
	}
};
