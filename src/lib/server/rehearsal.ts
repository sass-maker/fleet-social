import { mkdir, readFile, writeFile, stat } from 'node:fs/promises';
import { resolve } from 'node:path';
import { createClient } from '@libsql/client';
import { drizzle } from 'drizzle-orm/libsql';
import { eq } from 'drizzle-orm';
import { json, type RequestEvent } from '@sveltejs/kit';
import type { AppDb } from './db/client';
import * as schema from './db/schema';
import { INIT_SQL } from './db/init-sql';
import { readAppEnv } from './env';
import { approveDraft, approvalProblem } from './draft-approval';
import { assertSafeStorageKey, type MediaStore } from './media';
import { SubrequestBudget } from './budget';
import { handleMashup } from './rehearsal-mashup';
import { hasAllowedMutationOrigin } from '$lib/domain/bearer';

const userId = 'rehearsal-owner';
const connectionIds = ['rehearsal-youtube', 'rehearsal-instagram'];
const attribution =
	'\n\nEdited with Mashup.\n\nSource: Conversations with Tyler. CC BY 3.0. Original source receipts retained in the Mashup proof.';
const fixtures = [
	{
		id: 'survive',
		title: 'How companies survive technology',
		file: 'survive.mp4',
		poster: 'survive.jpg',
		key: '1728000000000-1000000000000001.mp4',
		caption:
			'Technology is a choice. Progress takes people who build, scale and maintain it.' +
			attribution
	},
	{
		id: 'operators',
		title: 'Ideas are not enough',
		file: 'operators.mp4',
		poster: 'operators.jpg',
		key: '1728000000000-1000000000000002.mp4',
		caption: 'An idea becomes useful when someone does the work.' + attribution
	},
	{
		id: 'planned',
		title: 'Ideas are not enough · scheduled cut',
		file: 'operators.mp4',
		poster: 'operators.jpg',
		key: '1728000000000-1000000000000003.mp4',
		caption: 'Good ideas need great operators. Rehearsal copy of the 13-second proof.' + attribution
	}
];
let current: Promise<{ db: AppDb; media: MediaStore; directory: string }> | undefined;

async function createState(fresh = false) {
	const dir = resolve(process.env.FLEET_SOCIAL_REHEARSAL_DIR ?? '.fleet-local/rehearsal');
	await mkdir(dir, { recursive: true });
	let database = 'rehearsal.db';
	if (fresh) {
		database = `rehearsal-${Date.now()}.db`;
		await writeFile(resolve(dir, 'current-database.txt'), database);
	} else {
		database = await readFile(resolve(dir, 'current-database.txt'), 'utf8').catch(() => database);
		if (!/^rehearsal(?:-\d+)?\.db$/.test(database)) throw new Error('Invalid rehearsal database');
	}
	const client = createClient({ url: `file:${resolve(dir, database)}` });
	await client.executeMultiple(INIT_SQL);
	const db = drizzle(client, { schema }) as unknown as AppDb;
	const now = new Date();
	const configuredHandle = (
		await readFile(resolve(dir, 'instagram-handle.txt'), 'utf8').catch(() => '')
	).trim();
	const instagramHandle = /^[a-zA-Z0-9._]{1,30}$/.test(configuredHandle)
		? `${configuredHandle} (rehearsal)`
		: 'Demo Creator';
	const owner = await db.select().from(schema.users).where(eq(schema.users.id, userId)).limit(1);
	if (!owner.length) {
		await db.insert(schema.users).values({
			id: userId,
			email: 'demo@localhost',
			displayName: 'Creator Studio',
			timezone: 'Asia/Kolkata',
			passwordHash: 'rehearsal-no-password-login',
			totpEnabled: true,
			createdAt: now,
			updatedAt: now
		});
		for (const platform of ['youtube', 'instagram']) {
			await db.insert(schema.connections).values({
				id: `rehearsal-${platform}`,
				userId,
				platform,
				displayName: platform === 'youtube' ? 'Sample YouTube channel' : 'Sample Instagram Creator',
				handle: platform === 'youtube' ? 'Demo channel' : instagramHandle,
				credentialsEncrypted: 'rehearsal-no-provider-credentials',
				metaJson: JSON.stringify({ rehearsal: true }),
				status: 'active',
				createdAt: now,
				updatedAt: now
			});
		}
		for (const fixture of fixtures) {
			const id = `rehearsal-${fixture.id}`;
			await db.insert(schema.drafts).values({
				id,
				userId,
				title: fixture.title,
				baseBody:
					fixture.caption +
					'\n\n' +
					(
						JSON.parse(
							await readFile(
								resolve(dir, `${fixture.id === 'survive' ? 'survive' : 'operators'}.receipt.json`),
								'utf8'
							)
						).sources as { title: string; sourceUrl: string }[]
					)
						.map((source) => `${source.title}\n${source.sourceUrl}`)
						.join('\n\n'),
				projectId: 'mashup',
				sourceRef: `interview-rehearsal:${fixture.id}`,
				selectedConnectionIds: JSON.stringify(connectionIds),
				createdAt: now,
				updatedAt: now
			});
			const file = await stat(resolve(dir, fixture.file));
			await db.insert(schema.draftMedia).values({
				id: `media-${fixture.id}`,
				draftId: id,
				storageKey: fixture.key,
				mime: 'video/mp4',
				size: file.size,
				width: 1080,
				height: 1920,
				createdAt: now
			});
			if (fixture.id === 'planned') {
				await approveDraft(db, id, userId);
				const runAt = new Date(now);
				runAt.setDate(now.getDate() + ((8 - now.getDay()) % 7 || 7) + 3);
				runAt.setHours(18, 0, 0, 0);
				for (const connectionId of connectionIds)
					await db.insert(schema.publishTargets).values({
						id: `${id}-${connectionId}`,
						draftId: id,
						connectionId,
						status: 'scheduled',
						scheduledFor: runAt,
						createdAt: now,
						updatedAt: now
					});
				await db.update(schema.drafts).set({ status: 'scheduled' }).where(eq(schema.drafts.id, id));
			}
		}
	}
	const mediaDir = resolve(dir, 'uploads');
	await mkdir(mediaDir, { recursive: true });
	const fileFor = (key: string) => {
		assertSafeStorageKey(key);
		const fixture = fixtures.find((item) => item.key === key);
		return fixture ? resolve(dir, fixture.file) : resolve(mediaDir, key);
	};
	const media: MediaStore = {
		async get(key) {
			return readFile(fileFor(key)).catch(() => null);
		},
		async getRange(key, start, end) {
			return (await this.get(key))?.slice(start, end + 1) ?? null;
		},
		async size(key) {
			return (await stat(fileFor(key)).catch(() => null))?.size ?? null;
		},
		async put(key, bytes) {
			await writeFile(fileFor(key), bytes);
		},
		async putBlob(key, blob) {
			await writeFile(fileFor(key), new Uint8Array(await blob.arrayBuffer()));
		},
		async delete() {
			/* Retain rehearsal bytes for reset and recovery. */
		}
	};
	return { db, media, directory: dir };
}

export async function handleRehearsal(
	event: RequestEvent,
	resolveEvent: (event: RequestEvent) => Response | Promise<Response>
) {
	if (
		!import.meta.env.DEV ||
		process.env.FLEET_SOCIAL_REHEARSAL !== '1' ||
		event.url.hostname !== '127.0.0.1'
	)
		return json({ error: 'Local rehearsal only' }, { status: 404 });
	const path = event.url.pathname;
	if (
		!['GET', 'HEAD', 'OPTIONS'].includes(event.request.method) &&
		!hasAllowedMutationOrigin(event.request, event.url)
	)
		return json({ error: 'Origin mismatch' }, { status: 403 });
	current ??= createState();
	const state = await current;
	Object.assign(event.locals, {
		db: state.db,
		media: state.media,
		queue: null,
		rehearsal: true,
		budget: new SubrequestBudget(1000),
		env: readAppEnv({
			APP_URL: event.url.origin,
			APP_ENCRYPTION_KEY: 'feedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedface',
			ENABLE_VIDEO_UPLOAD: '1'
		}),
		user: {
			id: userId,
			email: 'demo@localhost',
			displayName: 'Creator Studio',
			timezone: 'Asia/Kolkata',
			totpEnabled: true,
			mfaVerified: true
		},
		authMethod: 'session',
		apiKeyScopes: null
	});
	if (path.startsWith('/api/rehearsal/mashup')) {
		const response = await handleMashup(event.request, path, state, userId);
		if (response) return response;
	}
	if (path === '/api/rehearsal/reset' && event.request.method === 'POST') {
		current = createState(true);
		await current;
		return json({ reset: true, rehearsal: true });
	}
	const captions = /^\/api\/rehearsal\/captions\/(rehearsal-(survive|operators|planned))$/.exec(
		path
	);
	if (captions && event.request.method === 'GET') {
		const file = captions[2] === 'survive' ? 'survive.vtt' : 'operators.vtt';
		return new Response(await readFile(resolve(state.directory, file)), {
			headers: { 'Content-Type': 'text/vtt', 'Cache-Control': 'private, max-age=3600' }
		});
	}
	const poster = /^\/api\/rehearsal\/poster\/(rehearsal-(survive|operators|planned))$/.exec(path);
	if (poster && event.request.method === 'GET') {
		const fixture = fixtures.find((item) => `rehearsal-${item.id}` === poster[1])!;
		return new Response(await readFile(resolve(state.directory, fixture.poster)), {
			headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'private, max-age=3600' }
		});
	}
	const delivery = /^\/api\/rehearsal\/deliver\/([^/]+)$/.exec(path);
	if (delivery && event.request.method === 'POST') {
		const id = delivery[1];
		const owned = await state.db
			.select()
			.from(schema.drafts)
			.where(eq(schema.drafts.id, id))
			.limit(1);
		if (!owned.length || owned[0].userId !== userId)
			return json({ error: 'Not found' }, { status: 404 });
		const destinations = JSON.parse(owned[0].selectedConnectionIds ?? '[]') as string[];
		const problem = await approvalProblem(state.db, id, destinations);
		if (problem) return json({ error: problem }, { status: 409 });
		const receipt = {
			rehearsal: true,
			sentToProvider: false,
			draftId: id,
			destinations,
			approvedRevision: owned[0].approvalHash,
			rehearsedAt: new Date().toISOString()
		};
		await state.db.insert(schema.appSettings).values({
			key: `rehearsal-receipt:${id}:${Date.now()}`,
			value: JSON.stringify(receipt),
			updatedAt: new Date()
		});
		return json(receipt);
	}
	if (
		path.startsWith('/api/internal/') ||
		path.startsWith('/api/targets/') ||
		(path.startsWith('/api/connections/') && event.request.method !== 'GET') ||
		/\/publish\/?$/.test(path)
	) {
		return json(
			{
				error:
					'Rehearsal does not contact social platforms. Use video review to rehearse an upload, or connect accounts on your live instance.'
			},
			{ status: 409 }
		);
	}
	return resolveEvent(event);
}
