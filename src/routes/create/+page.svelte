<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { ArrowRight, Film, Link, Type, Check } from '@lucide/svelte';
	let { data } = $props();
	let lane = $state<'idea' | 'links' | 'product'>('idea');
	let brief = $state('');
	let title = $state('');
	let links = $state('');
	let scenes = $state(['', '', '']);
	let rights = $state(false);
	let approved = $state(false);
	let busy = $state(false);
	let problem = $state('');
	let jobId = $state('');
	let stage = $state('');
	let detail = $state('');
	let draftId = $state('');
	let timer: ReturnType<typeof setInterval> | undefined;
	let polling = false;
	function moveLane(event: KeyboardEvent) {
		if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
		event.preventDefault();
		const ids = ['idea', 'links', 'product'] as const;
		const index = ids.indexOf(lane);
		const next =
			event.key === 'Home'
				? 0
				: event.key === 'End'
					? 2
					: (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3;
		lane = ids[next];
		problem = '';
		approved = false;
		(event.currentTarget as HTMLElement).parentElement
			?.querySelectorAll<HTMLButtonElement>('button')
			[next]?.focus();
	}
	const examples = [
		{
			id: 'survive',
			title: 'How companies survive technology',
			description: 'Three conversations, one argument about technology and progress.',
			duration: 47,
			sources: 3,
			tags: ['technology', 'companies', 'business', 'progress', 'growth']
		},
		{
			id: 'operators',
			title: 'Ideas are not enough',
			description: 'A concise cut on turning inventions into useful products.',
			duration: 13,
			sources: 1,
			tags: ['ideas', 'product', 'startup', 'operators', 'invention']
		}
	];
	const ranked = $derived(
		[...examples].sort(
			(a, b) =>
				b.tags.filter((tag) => brief.toLowerCase().includes(tag)).length -
				a.tags.filter((tag) => brief.toLowerCase().includes(tag)).length
		)
	);
	async function post(path: string, body: unknown) {
		problem = '';
		busy = true;
		try {
			const response = await fetch(path, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(body)
			});
			const result = await response.json();
			if (!response.ok) throw new Error(result.error || 'Could not start this video.');
			return result;
		} catch (error) {
			problem = error instanceof Error ? error.message : 'Check your connection and try again.';
		} finally {
			busy = false;
		}
	}
	async function useExample(exampleId: string) {
		const example = examples.find((item) => item.id === exampleId)!;
		const result = await post('/api/rehearsal/mashup/import', {
			exampleId,
			brief: brief.trim() || example.description
		});
		if (result) await goto(`/review/${result.draftId}`);
	}
	function draftScenes() {
		const lines = brief
			.split(/[.!?\n]+/)
			.map((line) => line.trim())
			.filter(Boolean);
		scenes = [
			(lines[0] || 'Describe the problem your product solves').slice(0, 180),
			(lines[1] || `Meet ${title.trim() || 'your product'}. ${brief}`).slice(0, 180),
			(lines[2] || `Try ${title.trim() || 'your product'}.`).slice(0, 180)
		];
		approved = false;
	}
	function productExample() {
		lane = 'product';
		title = 'Fleet Social';
		brief =
			'Great videos deserve a plan. Fleet Social brings Mashup videos, approval, and your publishing calendar together. Review a video and choose its release date.';
		draftScenes();
		rights = true;
	}
	function linkExample() {
		lane = 'links';
		title = 'From ideas to progress';
		brief =
			'Technology changes companies. Inventions need operators who can scale, maintain and improve them.';
		links =
			'https://mashup.highsignal.app/media/operators-final.mp4\nhttps://mashup.highsignal.app/media/survive-technology-final.mp4';
		rights = true;
		approved = false;
	}
	async function poll() {
		if (!jobId || polling) return;
		polling = true;
		try {
			const response = await fetch(`/api/rehearsal/mashup/jobs/${jobId}`);
			const result = await response.json();
			if (!response.ok) throw new Error(result.error || 'Could not read render progress');
			stage = result.stage;
			detail = result.detail;
			if (result.state === 'failed') {
				problem = result.error;
				clearInterval(timer);
				sessionStorage.removeItem('fleet-social-mashup-job');
				jobId = '';
			}
			if (result.state === 'completed') {
				draftId = result.draftId;
				clearInterval(timer);
				sessionStorage.removeItem('fleet-social-mashup-job');
			}
		} catch (error) {
			problem =
				error instanceof Error
					? error.message
					: 'Progress is temporarily unavailable. Your render remains on disk.';
		} finally {
			polling = false;
		}
	}
	function watch() {
		void poll();
		clearInterval(timer);
		timer = setInterval(() => void poll(), 1800);
	}
	async function render() {
		const result = await post('/api/rehearsal/mashup/render', {
			mode: lane === 'product' ? 'product' : 'links',
			title: title.trim(),
			brief: brief.trim(),
			...(lane === 'product'
				? { scenes }
				: {
						links: links
							.split(/\n/)
							.map((link) => link.trim())
							.filter(Boolean)
					}),
			rightsConfirmed: rights,
			planApproved: approved
		});
		if (result?.draftId) {
			clearInterval(timer);
			sessionStorage.removeItem('fleet-social-mashup-job');
			draftId = result.draftId;
			jobId = 'saved';
			stage = 'Ready for review';
			detail =
				'Reused the verified video for this same approved plan. A new draft is ready for your approval.';
			return;
		}
		if (result) {
			draftId = '';
			jobId = result.jobId;
			stage = 'Starting';
			detail = 'The local Mashup renderer is starting.';
			sessionStorage.setItem('fleet-social-mashup-job', jobId);
			watch();
		}
	}
	onMount(() => {
		jobId = sessionStorage.getItem('fleet-social-mashup-job') || '';
		if (jobId) watch();
		return () => clearInterval(timer);
	});
</script>

<svelte:head><title>Create with Mashup · Fleet Social</title></svelte:head>
<div class="create-heading">
	<div>
		<p class="studio-muted">Mashup → Review → Calendar</p>
		<h1>A video starts here.</h1>
		<p class="studio-muted">Bring an idea, a few clips, or a product worth explaining.</p>
	</div>
	<a class="studio-secondary" href="/?view=review">View review queue <ArrowRight size={14} /></a>
</div>
{#if !data.rehearsal}<div class="studio-notice">
		Fresh Mashup rendering runs on your local computer. Open the documented rehearsal workspace to
		create a video, or <a href="/compose">upload a finished Mashup export</a> to this instance.
	</div>{/if}
<div class="creation-workspace">
	<div class="creation-main">
		<div class="creation-tabs" role="tablist" aria-label="Video starting point">
			{#each [{ id: 'idea', label: 'An idea', icon: Film }, { id: 'links', label: 'Video links', icon: Link }, { id: 'product', label: 'A product', icon: Type }] as tab (tab.id)}<button
					role="tab"
					id={`creation-tab-${tab.id}`}
					aria-controls="creation-content"
					tabindex={lane === tab.id ? 0 : -1}
					onkeydown={moveLane}
					aria-selected={lane === tab.id}
					onclick={() => {
						lane = tab.id as typeof lane;
						problem = '';
						approved = false;
					}}
					class:active={lane === tab.id}><tab.icon size={16} />{tab.label}</button
				>{/each}
		</div>
		{#if problem}<p role="alert" class="studio-error">{problem}</p>{/if}
		{#if jobId}<section class="render-progress" aria-live="polite">
				<span class="studio-status" class:approved={!!draftId}
					>{draftId ? '✓ Finished video' : '● Local render'}</span
				>
				<h2>{stage || 'Working on your video'}</h2>
				<p>{detail}</p>
				{#if draftId}<a class="studio-primary" href={`/review/${draftId}`}
						>Watch and review <ArrowRight size={15} /></a
					>{:else}<p class="studio-muted">
						You can leave this page and return. Progress and artifacts stay local.
					</p>{/if}
			</section>{/if}
		<div id="creation-content" role="tabpanel" aria-labelledby={`creation-tab-${lane}`}>
			{#if lane === 'idea'}
				<section class="brief-panel">
					<h2>What’s the angle?</h2>
					<label for="idea">Your idea</label><textarea
						id="idea"
						bind:value={brief}
						placeholder="Why great ideas still need people who can build them…"
						rows="3"
						maxlength="600"></textarea>
					<p class="studio-muted">
						Choose a prepared Mashup cut below for an instant demo. Your idea becomes its editable
						post caption. These are existing exports, ready to review.
					</p>
					<button
						class="studio-secondary"
						style="margin-top:16px"
						disabled={!brief.trim()}
						onclick={() => {
							lane = 'product';
							title = brief.trim().split(/\s+/).slice(0, 6).join(' ');
							draftScenes();
							rights = false;
						}}>Make a fresh story from my idea <ArrowRight size={14} /></button
					>
				</section>
				<div class="prepared-heading">
					<h2>Ready in one click</h2>
					<span>Verified Mashup exports</span>
				</div>
				<div class="example-grid">
					{#each ranked as example (example.id)}<article class="example">
							<img src={`/api/rehearsal/poster/rehearsal-${example.id}`} alt={example.title} />
							<div>
								<p class="studio-muted">
									{example.duration}s · {example.sources} source {example.sources === 1
										? 'conversation'
										: 'conversations'} · 9:16
								</p>
								<h3>{example.title}</h3>
								<p>{example.description}</p>
								<button
									class="studio-primary"
									disabled={busy || !data.rehearsal}
									onclick={() => useExample(example.id)}
									>Use this cut <ArrowRight size={14} /></button
								>
							</div>
						</article>{/each}
				</div>
			{:else}
				<form
					class="fresh-form"
					onsubmit={(event) => {
						event.preventDefault();
						void render();
					}}
				>
					<div class="form-intro">
						<h2>
							{lane === 'product'
								? 'A product story, in your words.'
								: 'Make a cut from your footage.'}
						</h2>
						<p class="studio-muted">
							{lane === 'product'
								? 'Three readable text scenes with an original quiet soundtrack. Review and edit the wording before Mashup renders your vertical video.'
								: 'Paste one to three public video links. Captions help choose complete passages up to 24 seconds each, ranked by topic words. If no matching passage fits, try a different brief or source. Sources without captions use their first 10 seconds. Source order stays as shown. Review the finished cut.'}
						</p>
						<button
							type="button"
							class="studio-link"
							onclick={lane === 'product' ? productExample : linkExample}
							>Use a demo example →</button
						>
					</div>
					<label for="video-title"
						>{lane === 'product' ? 'Product name / video title' : 'Video title'}</label
					><input
						id="video-title"
						bind:value={title}
						oninput={() => (approved = false)}
						maxlength="100"
						placeholder={lane === 'product' ? 'Your product' : 'A title for this cut'}
						required
					/>
					<label for="brief"
						>{lane === 'product' ? 'Describe the product idea' : 'The idea behind this cut'}</label
					><textarea
						id="brief"
						bind:value={brief}
						maxlength="600"
						rows="3"
						required
						placeholder="What should someone understand after watching?"
						oninput={() => (approved = false)}></textarea>
					{#if lane === 'product'}<button
							type="button"
							class="studio-secondary draft-button"
							onclick={draftScenes}
							disabled={!brief.trim()}>Draft scenes from my words</button
						>
						<div class="scene-plan">
							{#each scenes as _scene, index (index)}<label for={`scene-${index}`}
									><span
										>Scene {index + 1} · {['Problem', 'Product', 'Next step'][index]} · 4–8 seconds to
										read</span
									><textarea
										id={`scene-${index}`}
										bind:value={scenes[index]}
										maxlength="180"
										rows="3"
										required
										oninput={() => (approved = false)}></textarea></label
								>{/each}
						</div>
						<p class="studio-muted">
							Original text and procedural motion. No synthetic speech or generated photographic
							footage.
						</p>
					{:else}<label for="source-links">Source video links · one per line</label><textarea
							id="source-links"
							bind:value={links}
							rows="4"
							required
							oninput={() => (approved = false)}
							placeholder="https://www.youtube.com/watch?v=…"></textarea>
						<p class="studio-muted">
							Public YouTube, Vimeo, Archive.org and Mashup proof links. Downloads and rendering can
							take minutes; source credits stay with the result.
						</p>{/if}
					<label class="approval-check"
						><input type="checkbox" bind:checked={rights} required /><span
							>{lane === 'product'
								? 'I can use the text in these scenes.'
								: 'I own this footage or have permission for this derivative edit.'}</span
						></label
					>
					<label class="approval-check"
						><input type="checkbox" bind:checked={approved} required /><span
							>I approve this {lane === 'product'
								? 'three-scene story'
								: 'source order and caption-based excerpt selection'} for local rendering. Publishing
							needs a separate video approval.</span
						></label
					>
					<button
						class="studio-primary"
						type="submit"
						disabled={busy || (!!jobId && !draftId) || !data.rehearsal}
						>{busy ? 'Starting…' : 'Render approved plan'} <ArrowRight size={15} /></button
					>
				</form>
			{/if}
		</div>
	</div>
	<aside class="creation-aside">
		<h2>From cut to calendar.</h2>
		<div>
			<Film size={19} />
			<h3>Make it</h3>
			<p>Reuse a prepared cut instantly, edit source footage, or render a product story.</p>
		</div>
		<div>
			<Check size={19} />
			<h3>Give it your eye</h3>
			<p>Watch the actual video. Check its caption, source credits and destinations.</p>
		</div>
		<div>
			<ArrowRight size={19} />
			<h3>Choose a release</h3>
			<p>Approve the saved version, pick a time, and see it on the calendar.</p>
		</div>
		<p class="aside-note">
			Mashup owns editing and media receipts. Fleet Social owns video approval and delivery.
		</p>
		<a class="studio-link" href="/accounts">View YouTube and Instagram accounts →</a>
	</aside>
</div>

<style>
	.create-heading {
		display: flex;
		justify-content: space-between;
		gap: 24px;
		align-items: end;
		margin-bottom: 30px;
	}
	h1 {
		font:
			400 38px/1.2 Georgia,
			serif;
		letter-spacing: -1px;
		margin: 10px 0;
	}
	.create-heading > div > p:first-child {
		font-size: 11px;
	}
	.creation-workspace {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 240px;
		gap: 40px;
		max-width: 1220px;
	}
	.creation-tabs {
		display: flex;
		gap: 22px;
		border-bottom: 1px solid var(--studio-line);
		padding-bottom: 14px;
		margin-bottom: 25px;
	}
	.creation-tabs button {
		display: flex;
		align-items: center;
		gap: 8px;
		color: var(--studio-muted);
		font-size: 13px;
		padding: 7px 0;
	}
	.creation-tabs .active {
		color: var(--studio-accent);
		font-weight: 650;
	}
	h2 {
		font:
			400 25px/1.25 Georgia,
			serif;
		margin-bottom: 13px;
	}
	label {
		font-size: 12px;
		font-weight: 550;
	}
	input:not([type='checkbox']),
	textarea {
		width: 100%;
		border: 1px solid var(--studio-line);
		border-radius: 6px;
		background: var(--studio-panel);
		padding: 12px;
		font: inherit;
		color: var(--studio-ink);
		margin: 7px 0 11px;
	}
	textarea {
		resize: vertical;
		line-height: 1.6;
	}
	.studio-muted {
		line-height: 1.65;
	}
	.brief-panel > p,
	.form-intro > p,
	.fresh-form > p {
		font-size: 12px;
	}
	.prepared-heading {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin: 28px 0 16px;
	}
	.prepared-heading h2 {
		font-size: 21px;
		margin: 0;
	}
	.prepared-heading span {
		font-size: 10px;
		color: var(--studio-muted);
	}
	.example-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 20px;
	}
	.example {
		border: 1px solid var(--studio-line);
		border-radius: 9px;
		overflow: hidden;
		background: var(--studio-panel);
	}
	.example img {
		width: 100%;
		height: 180px;
		object-fit: cover;
		object-position: center 32%;
	}
	.example > div {
		padding: 20px;
	}
	.example h3 {
		font:
			400 23px/1.25 Georgia,
			serif;
		margin: 10px 0;
	}
	.example p {
		font-size: 12px;
		line-height: 1.6;
	}
	.example .studio-muted {
		font-size: 10px;
	}
	.example button {
		margin-top: 20px;
	}
	.creation-aside {
		border-left: 1px solid var(--studio-line);
		padding-left: 25px;
		padding-top: 6px;
	}
	.creation-aside h2 {
		font-size: 23px;
		font-style: italic;
	}
	.creation-aside > div {
		padding: 17px 0;
	}
	.creation-aside :global(svg) {
		color: var(--studio-accent);
	}
	.creation-aside h3 {
		font-size: 13px;
		font-weight: 650;
		margin: 10px 0 6px;
	}
	.creation-aside p {
		font-size: 12px;
		color: var(--studio-muted);
		line-height: 1.65;
	}
	.aside-note {
		border-top: 1px solid var(--studio-line);
		padding-top: 19px;
		margin: 12px 0;
	}
	.creation-aside a {
		font-size: 12px;
	}
	.fresh-form {
		display: grid;
	}
	.form-intro {
		margin-bottom: 22px;
	}
	.form-intro .studio-link {
		margin-top: 14px;
		font-size: 12px;
	}
	.draft-button {
		justify-self: start;
		margin: 0 0 20px;
	}
	.scene-plan {
		display: grid;
		grid-template-columns: 1fr 1fr 1fr;
		gap: 13px;
	}
	.scene-plan span {
		font-size: 11px;
		color: var(--studio-muted);
	}
	.scene-plan textarea {
		font-size: 13px;
	}
	.approval-check {
		display: flex;
		align-items: start;
		gap: 10px;
		font-weight: 400;
		line-height: 1.6;
		margin-top: 15px;
	}
	[type='checkbox'] {
		accent-color: var(--studio-accent);
		width: 16px;
		height: 16px;
		flex-shrink: 0;
		margin-top: 3px;
	}
	.fresh-form > .studio-primary {
		justify-self: start;
		margin-top: 23px;
	}
	.render-progress {
		padding: 23px;
		border: 1px solid var(--studio-line);
		background: #eaf0e5;
		border-radius: 8px;
		margin: 0 0 24px;
	}
	.render-progress h2 {
		margin-top: 13px;
	}
	.render-progress p {
		line-height: 1.6;
		font-size: 12px;
	}
	.render-progress a {
		margin-top: 20px;
	}
	.render-progress .studio-muted {
		margin-top: 10px;
	}
	@media (max-width: 1000px) {
		.creation-workspace {
			grid-template-columns: minmax(0, 1fr);
		}
		.creation-aside {
			display: none;
		}
	}
	@media (max-width: 600px) {
		.create-heading {
			display: block;
		}
		.create-heading > a {
			margin-top: 17px;
		}
		h1 {
			font-size: 31px;
		}
		.example-grid,
		.scene-plan {
			grid-template-columns: 1fr;
		}
		.example img {
			height: 180px;
		}
		.creation-tabs {
			gap: 20px;
		}
		.creation-tabs button {
			font-size: 12px;
		}
		.prepared-heading span {
			display: none;
		}
	}
</style>
