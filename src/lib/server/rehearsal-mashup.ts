import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, stat, copyFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { eq } from 'drizzle-orm';
import { json } from '@sveltejs/kit';
import { z } from 'zod';
import type { AppDb } from './db/client';
import { appSettings, drafts, draftMedia } from './db/schema';
import type { MediaStore } from './media';

type State = { db: AppDb; media: MediaStore; directory: string };
type SourceCredit = {
	title: string;
	creator?: string;
	sourceUrl: string;
	license: string;
	licenseUrl: string;
	upstreamSources?: SourceCredit[];
};
type Receipt = {
	schema: string;
	artifactId: string;
	sources: SourceCredit[];
	output: {
		video: { sha256: string; bytes: number };
		durationSeconds: number;
		width: number;
		height: number;
	};
	approval: { status: string };
	operation?: { state: string };
};
const examples = [
	{
		id: 'survive',
		title: 'How companies survive technology',
		description: 'Three conversations become one argument about technology and progress.',
		duration: 47,
		sources: 3,
		tags: ['technology', 'companies', 'growth', 'business', 'progress']
	},
	{
		id: 'operators',
		title: 'Ideas are not enough',
		description: 'A concise podcast cut about turning inventions into useful products.',
		duration: 13,
		sources: 1,
		tags: ['ideas', 'product', 'startup', 'operators', 'invention']
	}
];
let activeJob: string | null = null;
const imports = new Map<string, Promise<string>>();

async function verifiedReceipt(path: string, video: string): Promise<Receipt> {
	const receipt = JSON.parse(await readFile(path, 'utf8')) as Receipt;
	const bytes = await readFile(video);
	if (
		receipt.schema !== 'fleet.mashup-media-receipt.v1' ||
		receipt.approval?.status !== 'approved' ||
		!receipt.sources?.length ||
		receipt.output?.video?.bytes !== bytes.length ||
		bytes.length > 95_000_000 ||
		receipt.output.video.sha256 !== createHash('sha256').update(bytes).digest('hex')
	)
		throw new Error('Mashup receipt does not match the finished approved MP4');
	return receipt;
}

async function importVideo(
	state: State,
	userId: string,
	input: {
		id: string;
		title: string;
		brief: string;
		video: string;
		poster: string;
		receiptPath: string;
	}
) {
	const key = `${state.directory}:${input.id}`;
	const existing = await state.db
		.select()
		.from(appSettings)
		.where(eq(appSettings.key, `mashup-import:${input.id}`))
		.limit(1);
	if (existing[0]) return existing[0].value;
	if (imports.has(key)) return imports.get(key)!;
	const task = (async () => {
		const receipt = await verifiedReceipt(input.receiptPath, input.video);
		const id = crypto.randomUUID();
		const storageKey = `${Date.now()}-${crypto.randomUUID().replaceAll('-', '').slice(0, 16)}.mp4`;
		await state.media.put(storageKey, await readFile(input.video), 'video/mp4');
		await copyFile(input.poster, resolve(state.directory, 'uploads', `${id}.jpg`));
		const attributed = receipt.sources.flatMap((source) =>
			source.upstreamSources?.length ? source.upstreamSources : [source]
		);
		const publicSources = [
			...new Map(
				attributed
					.filter((source) => source.sourceUrl.startsWith('https://'))
					.map((source) => [source.sourceUrl, source])
			).values()
		];
		const credits = publicSources.length
			? publicSources
					.map(
						(source) =>
							`${source.creator ? `${source.creator} — ` : ''}${source.title}\n${source.sourceUrl}\n${source.license} · ${source.licenseUrl}`
					)
					.join('\n\n')
			: receipt.sources.some((source) =>
						source.sourceUrl.startsWith('urn:fleet:owner-procedural-audio:')
				  )
				? 'Original text, procedural motion and soundtrack created for this video.'
				: 'Original text and procedural motion created for this video.';
		const now = new Date();
		await state.db.insert(drafts).values({
			id,
			userId,
			title: input.title.slice(0, 100),
			projectId: 'mashup',
			baseBody: `${input.brief}\n\nEdited with Mashup.\n\n${credits}`,
			sourceRef: `mashup-receipt:${input.id}`,
			selectedConnectionIds: JSON.stringify(['rehearsal-youtube', 'rehearsal-instagram']),
			status: 'draft',
			createdAt: now,
			updatedAt: now
		});
		await state.db.insert(draftMedia).values({
			id: crypto.randomUUID(),
			draftId: id,
			storageKey,
			mime: 'video/mp4',
			size: receipt.output.video.bytes,
			width: receipt.output.width,
			height: receipt.output.height,
			createdAt: now
		});
		await state.db.insert(appSettings).values([
			{ key: `mashup-import:${input.id}`, value: id, updatedAt: now },
			{
				key: `mashup-receipt:${id}`,
				value: JSON.stringify({ ...receipt, localAdapter: true, brief: input.brief }),
				updatedAt: now
			}
		]);
		return id;
	})();
	imports.set(key, task);
	try {
		return await task;
	} finally {
		imports.delete(key);
	}
}

const renderRequest = z
	.object({
		mode: z.enum(['product', 'links']),
		title: z.string().trim().min(1).max(100),
		brief: z.string().trim().min(1).max(600),
		scenes: z.array(z.string().trim().min(1).max(180)).length(3).optional(),
		links: z.array(z.url()).min(1).max(3).optional(),
		rightsConfirmed: z.literal(true),
		planApproved: z.literal(true)
	})
	.strict();

export async function handleMashup(
	request: Request,
	pathname: string,
	state: State,
	userId: string
): Promise<Response | null> {
	try {
		if (pathname === '/api/rehearsal/mashup' && request.method === 'GET')
			return json({
				examples,
				renderAvailable: Boolean(process.env.FLEET_SOCIAL_MASHUP_ROOT),
				activeJob
			});
		if (pathname === '/api/rehearsal/mashup/import' && request.method === 'POST') {
			const input = z
				.object({
					exampleId: z.enum(['survive', 'operators']),
					brief: z.string().trim().min(1).max(600)
				})
				.strict()
				.parse(await request.json());
			const example = examples.find((item) => item.id === input.exampleId)!;
			const draftId = await importVideo(state, userId, {
				id: `prepared-${example.id}-${crypto.randomUUID()}`,
				title: example.title,
				brief: input.brief,
				video: resolve(state.directory, `${example.id}.mp4`),
				poster: resolve(state.directory, `${example.id}.jpg`),
				receiptPath: resolve(state.directory, `${example.id}.receipt.json`)
			});
			return json({ draftId, reused: true, newlyRendered: false });
		}
		if (pathname === '/api/rehearsal/mashup/render' && request.method === 'POST') {
			const input = renderRequest.parse(await request.json());
			if ((input.mode === 'product' && !input.scenes) || (input.mode === 'links' && !input.links))
				return json(
					{ error: 'Provide three scenes or one to three source links.' },
					{ status: 400 }
				);
			if (!process.env.FLEET_SOCIAL_MASHUP_ROOT)
				return json(
					{ error: 'Start the rehearsal with the local Mashup repository configured.' },
					{ status: 409 }
				);
			const previousJobs = await readdir(resolve(state.directory, 'jobs')).catch(() => []);
			const renderRecipe = createHash('sha256')
				.update(await readFile(resolve('scripts/mashup-render.py')))
				.update(await readFile(resolve('scripts/mashup_quality.py')))
				.digest('hex');
			for (const previousId of previousJobs.filter((id) => /^[a-f0-9-]{36}$/.test(id))) {
				const previousDirectory = resolve(state.directory, 'jobs', previousId);
				if (!(await stat(resolve(previousDirectory, 'complete.json')).catch(() => null))) continue;
				const previous = renderRequest.safeParse(
					await readFile(resolve(previousDirectory, 'request.json'), 'utf8')
						.then(JSON.parse)
						.then((request) => {
							if (request.renderRecipe !== renderRecipe) return null;
							delete request.approvedBy;
							delete request.renderRecipe;
							return request;
						})
						.catch(() => null)
				);
				if (!previous.success || JSON.stringify(previous.data) !== JSON.stringify(input)) continue;
				const draftId = await importVideo(state, userId, {
					id: `${previousId}-reuse-${crypto.randomUUID()}`,
					title: input.title,
					brief: input.brief,
					video: resolve(previousDirectory, 'final.mp4'),
					poster: resolve(previousDirectory, 'poster.jpg'),
					receiptPath: resolve(previousDirectory, 'media-receipt.json')
				});
				return json({ draftId, reused: true, newlyRendered: false });
			}
			if (activeJob)
				return json(
					{ error: 'One video is already rendering. Let it finish before starting another.' },
					{ status: 409 }
				);
			const id = crypto.randomUUID();
			const directory = resolve(state.directory, 'jobs', id);
			await mkdir(directory, { recursive: true });
			await writeFile(
				resolve(directory, 'request.json'),
				JSON.stringify({
					...input,
					renderRecipe,
					approvedBy: 'Owner approved the scene plan in Fleet Social local rehearsal'
				})
			);
			await writeFile(
				resolve(directory, 'progress.json'),
				JSON.stringify({ stage: 'Starting', detail: 'Starting the local Mashup renderer' })
			);
			activeJob = id;
			const child = spawn(
				resolve('.fleet-local/mashup-runtime/bin/python'),
				[resolve('scripts/mashup-render.py'), directory],
				{
					cwd: directory,
					env: {
						PATH: `/opt/homebrew/opt/ffmpeg-full/bin:${process.env.PATH ?? ''}`,
						LANG: 'en_US.UTF-8',
						PYTHON_DOTENV_DISABLED: '1'
					},
					stdio: ['ignore', 'ignore', 'pipe']
				}
			);
			let stderr = '';
			child.stderr.on('data', (chunk) => {
				stderr = (stderr + String(chunk)).slice(-1200);
			});
			child.once('error', async () => {
				activeJob = null;
				await writeFile(
					resolve(directory, 'failed.json'),
					JSON.stringify({
						error:
							'The local Mashup Python runtime is unavailable. Run the documented rehearsal setup.'
					})
				);
			});
			child.once('exit', async (code) => {
				activeJob = null;
				if (code && !(await stat(resolve(directory, 'failed.json')).catch(() => null)))
					await writeFile(
						resolve(directory, 'failed.json'),
						JSON.stringify({
							error: stderr || 'The local render stopped before producing a verified MP4.'
						})
					);
			});
			return json({ jobId: id }, { status: 202 });
		}
		const job = /^\/api\/rehearsal\/mashup\/jobs\/([a-f0-9-]{36})$/.exec(pathname);
		if (job && request.method === 'GET') {
			const directory = resolve(state.directory, 'jobs', job[1]);
			const progress = await readFile(resolve(directory, 'progress.json'), 'utf8')
				.then(JSON.parse)
				.catch(() => null);
			if (!progress) return json({ error: 'Render not found' }, { status: 404 });
			const failed = await readFile(resolve(directory, 'failed.json'), 'utf8')
				.then(JSON.parse)
				.catch(() => null);
			if (failed) return json({ state: 'failed', ...progress, error: failed.error });
			if (await stat(resolve(directory, 'complete.json')).catch(() => null)) {
				const input = JSON.parse(await readFile(resolve(directory, 'request.json'), 'utf8'));
				const draftId = await importVideo(state, userId, {
					id: job[1],
					title: input.title,
					brief: input.brief,
					video: resolve(directory, 'final.mp4'),
					poster: resolve(directory, 'poster.jpg'),
					receiptPath: resolve(directory, 'media-receipt.json')
				});
				return json({ state: 'completed', draftId, ...progress, newlyRendered: true });
			}
			return json({ state: 'running', ...progress });
		}
		const poster = /^\/api\/rehearsal\/mashup\/poster\/([a-f0-9-]{36})$/.exec(pathname);
		if (poster && request.method === 'GET') {
			const bytes = await readFile(resolve(state.directory, 'uploads', `${poster[1]}.jpg`)).catch(
				() => null
			);
			return bytes
				? new Response(bytes, { headers: { 'Content-Type': 'image/jpeg' } })
				: json({ error: 'Poster not found' }, { status: 404 });
		}
		return null;
	} catch (error) {
		return json(
			{
				error:
					error instanceof z.ZodError
						? 'Check the brief, sources, and plan approval.'
						: error instanceof Error
							? error.message
							: 'Mashup could not complete this step.'
			},
			{ status: 400 }
		);
	}
}
