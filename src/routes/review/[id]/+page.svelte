<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { platformName, accountLabel } from '$lib/domain/platforms';
	import { fleetProjects } from '$lib/domain/fleet-projects';
	import { ArrowLeft, Check, CalendarDays, ExternalLink } from '@lucide/svelte';
	let { data } = $props();
	let busy = $state(false);
	let problem = $state('');
	let notice = $state('');
	let showSchedule = $state(false);
	let showDelivery = $state(false);
	let runAt = $state('');
	const video = $derived(data.draft.media.find((item) => item.mime.startsWith('video/')));
	const destinations = $derived(
		data.connections.filter((connection) =>
			data.draft.selectedConnectionIds?.includes(connection.id)
		)
	);
	const project = $derived(
		fleetProjects.find((item) => item.id === data.draft.projectId)?.name ?? 'Choose a project'
	);
	const delivered = $derived(data.draft.targets.some((target) => target.status === 'published'));
	const sourceSplit = $derived(data.draft.baseBody.indexOf('\n\nEdited with Mashup.\n\n'));
	const caption = $derived(
		sourceSplit >= 0 ? data.draft.baseBody.slice(0, sourceSplit) : data.draft.baseBody
	);
	const sourceCredits = $derived(
		sourceSplit >= 0
			? data.draft.baseBody.slice(sourceSplit + '\n\nEdited with Mashup.\n\n'.length)
			: ''
	);
	async function request(path: string, body?: unknown) {
		busy = true;
		problem = '';
		notice = '';
		try {
			const response = await fetch(path, {
				method: 'POST',
				headers: body ? { 'Content-Type': 'application/json' } : undefined,
				body: body ? JSON.stringify(body) : undefined
			});
			const result = await response.json();
			if (!response.ok) throw new Error(result.error || 'Could not save. Try again.');
			await invalidateAll();
			return result;
		} catch (error) {
			problem = error instanceof Error ? error.message : 'Check your connection and try again.';
		} finally {
			busy = false;
		}
	}
	async function approve() {
		if (await request(`/api/drafts/${data.draft.id}/approval`))
			notice = 'Saved revision approved. Choose a release date or upload now.';
	}
	async function schedule() {
		if (!runAt) {
			problem = 'Choose a release date and time.';
			return;
		}
		if (
			await request(`/api/drafts/${data.draft.id}/schedule`, {
				connectionIds: destinations.map((item) => item.id),
				runAt: new Date(runAt).toISOString()
			})
		) {
			showSchedule = false;
			notice = data.rehearsal
				? 'Scheduled locally for rehearsal. This server does not send to providers.'
				: 'Release scheduled. You can see it on the calendar.';
		}
	}
	async function deliver() {
		const result = await request(
			data.rehearsal
				? `/api/rehearsal/deliver/${data.draft.id}`
				: `/api/drafts/${data.draft.id}/publish`,
			{ connectionIds: destinations.map((item) => item.id) }
		);
		if (result) {
			showDelivery = false;
			notice = result.rehearsal
				? 'Upload rehearsal complete. Approval was checked and a local receipt saved. Nothing was sent to YouTube or Instagram.'
				: 'Delivery started. Check each destination below for its confirmed result.';
		}
	}
</script>

<svelte:head><title>{data.draft.title || 'Video review'} · Fleet Social</title></svelte:head>
<a href="/" class="studio-back"><ArrowLeft size={14} /> Calendar</a>
<div class="review-heading">
	<h1>The final look.</h1>
	<p class="studio-muted">Play it through. Approve what goes out.</p>
</div>
<div class="video-review">
	<div class="video-column">
		{#if video}<video
				controls
				playsinline
				preload="metadata"
				poster={data.rehearsal && data.draft.id.startsWith('rehearsal-')
					? `/api/rehearsal/poster/${data.draft.id}`
					: data.rehearsal && data.draft.sourceRef?.startsWith('mashup-receipt:')
						? `/api/rehearsal/mashup/poster/${data.draft.id}`
						: undefined}
				><source
					src={`/api/media/${encodeURIComponent(video.storageKey)}`}
					type={video.mime}
				/><track
					kind="captions"
					src={data.rehearsal && data.draft.id.startsWith('rehearsal-')
						? `/api/rehearsal/captions/${data.draft.id}`
						: undefined}
					srclang="en"
					label="English"
				/></video
			>
		{:else if data.draft.media[0]}<img
				src={`/api/media/${encodeURIComponent(data.draft.media[0].storageKey)}`}
				alt={data.draft.media[0].altText || 'Draft media'}
			/>
		{:else}<div class="text-preview">{data.draft.baseBody}</div>{/if}
		<p class="media-caption">
			{project} · {video ? 'Vertical video' : 'Saved post'}
			{video ? `· ${(video.size / 1_000_000).toFixed(1)} MB` : ''}
		</p>
	</div>
	<div class="review-detail">
		<span class="studio-status" class:approved={data.approved}
			>{data.approved ? '✓ Approved revision' : '● Awaiting approval'}</span
		>
		<h2 class="post-title">{data.draft.title || 'Untitled post'}</h2>
		<section>
			<h3>Caption</h3>
			<p class="caption">{caption || 'No caption yet.'}</p>
			{#if sourceCredits}<details class="source-credits">
					<summary>Source credits · included with the caption</summary>
					<p class="caption">{'Edited with Mashup.\n\n' + sourceCredits}</p>
				</details>{/if}
		</section>
		<section>
			<h3>Destinations to approve</h3>
			{#each destinations as destination (destination.id)}
				<div class="destination">
					<span class="destination-check"><Check size={12} /></span>
					<div>
						<strong>{platformName(destination.platform)}</strong>
						<p class="studio-muted">
							{accountLabel(destination.displayName, destination.handle)}{data.rehearsal
								? ' · sample destination'
								: ''}
						</p>
					</div>
					<span class="destination-note"
						>{destination.platform === 'youtube'
							? 'Private upload'
							: destination.platform === 'instagram'
								? 'Reel · public'
								: 'Post'}</span
					>
				</div>
			{:else}<p class="studio-muted">
					Choose connected destinations in the editor before approval.
				</p>
				<a href="/accounts" class="studio-link">Connect accounts →</a>{/each}
		</section>
		<section>
			<h3>Saved revision</h3>
			<p>
				{project} · {data.draft.sourceRef?.startsWith('interview-rehearsal:')
					? 'Mashup proof video · CC BY 3.0'
					: data.draft.sourceRef?.startsWith('mashup-receipt:')
						? 'Verified Mashup export · sources retained'
						: data.draft.sourceRef || 'Owner-created draft'}
			</p>
			<p class="studio-muted">
				Approval covers this saved media, caption and destination set. Editing any of them requires
				a new approval.
			</p>
		</section>
		{#if problem}<p class="studio-error" role="alert">{problem}</p>{/if}
		{#if notice}<p class="studio-notice" role="status">{notice}</p>{/if}
		<div class="review-actions">
			{#if !data.approved}<button
					class="studio-primary"
					onclick={approve}
					disabled={busy || !destinations.length}>{busy ? 'Saving…' : 'Approve video'}</button
				>
			{:else if !delivered}<button
					class="studio-primary"
					onclick={() => {
						showSchedule = !showSchedule;
						showDelivery = false;
					}}
					disabled={busy}><CalendarDays size={16} /> Schedule release</button
				><button
					class="studio-secondary"
					onclick={() => {
						showDelivery = !showDelivery;
						showSchedule = false;
					}}
					disabled={busy}>{data.rehearsal ? 'Rehearse upload' : 'Upload now'}</button
				>{/if}
			<a href={`/compose?id=${data.draft.id}`} class="studio-secondary"
				>{data.approved ? 'Edit saved draft' : 'Request changes / edit'}</a
			>
		</div>
		{#if showSchedule}<form
				class="release-panel"
				onsubmit={(event) => {
					event.preventDefault();
					void schedule();
				}}
			>
				<label for="release-date"
					>Release date and time <span class="studio-muted"
						>({Intl.DateTimeFormat().resolvedOptions().timeZone})</span
					></label
				><input id="release-date" type="datetime-local" bind:value={runAt} required /><button
					class="studio-primary"
					type="submit"
					disabled={busy}>{busy ? 'Scheduling…' : 'Confirm schedule'}</button
				>
			</form>{/if}
		{#if showDelivery}<div class="release-panel">
				<h3>{data.rehearsal ? 'Rehearse this upload' : 'Upload this approved revision'}</h3>
				<p>
					{data.rehearsal
						? 'Check approval and save a local rehearsal receipt. No provider request is made.'
						: 'YouTube uploads stay private. Instagram Reels are published to the selected account. Confirm after checking the video and destinations above.'}
				</p>
				<button class="studio-primary" onclick={deliver} disabled={busy}
					>{busy ? 'Working…' : data.rehearsal ? 'Run upload rehearsal' : 'Confirm upload'}</button
				>
			</div>{/if}
		{#if data.draft.targets.length}<div class="delivery-results">
				<h3>Delivery status</h3>
				{#each data.draft.targets as target (target.id)}<div class="delivery-row">
						<span>{platformName(target.connection?.platform ?? '')}</span><strong
							>{target.status === 'published' && target.connection?.platform === 'youtube'
								? 'Uploaded · private'
								: target.status}{target.scheduledFor
								? ` · ${new Date(target.scheduledFor).toLocaleString()}`
								: ''}</strong
						>{#if target.remoteUrl}<a
								href={target.remoteUrl}
								target="_blank"
								rel="noreferrer"
								aria-label="Open confirmed provider result"><ExternalLink size={14} /></a
							>{/if}
					</div>
					{#if target.errorMessage}<p class="studio-error">{target.errorMessage}</p>{/if}{/each}
			</div>{/if}
	</div>
</div>

<style>
	.post-title {
		text-transform: none;
	}
	.source-credits {
		margin-top: 16px;
		font-size: 12px;
		color: var(--studio-muted);
	}
	.source-credits summary {
		cursor: pointer;
	}
	.source-credits p {
		margin-top: 13px;
	}
	.review-heading {
		margin: 24px 0 30px;
	}
	h1 {
		font:
			750 38px/1.2 var(--font-display),
			sans-serif;
		letter-spacing: -1px;
	}
	.review-heading p {
		margin-top: 8px;
	}
	.video-review {
		display: grid;
		grid-template-columns: 300px minmax(0, 1fr);
		gap: 42px;
		max-width: 1130px;
	}
	video,
	.video-column img {
		width: 100%;
		aspect-ratio: 9/16;
		object-fit: contain;
		background: #121414;
		border-radius: var(--radius);
	}
	.media-caption {
		color: var(--studio-muted);
		font-size: 11px;
		margin-top: 13px;
	}
	.review-detail {
		padding-top: 8px;
	}
	h2 {
		font:
			750 32px/1.2 var(--font-display),
			sans-serif;
		letter-spacing: -0.6px;
		margin: 14px 0 18px;
		max-width: 600px;
	}
	section {
		padding: 17px 0;
		border-bottom: 1px solid var(--studio-line);
	}
	h3 {
		color: var(--studio-muted);
		font-size: 11px;
		font-weight: 500;
		margin-bottom: 9px;
	}
	.caption {
		white-space: pre-wrap;
		line-height: 1.65;
	}
	section > .studio-muted {
		margin-top: 7px;
		font-size: 11px;
		line-height: 1.6;
	}
	.destination {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 9px 0;
	}
	.destination-check {
		display: grid;
		place-items: center;
		width: 17px;
		height: 17px;
		border-radius: 4px;
		background: var(--studio-accent);
		color: white;
	}
	.destination p {
		font-size: 11px;
		margin-top: 2px;
	}
	.destination-note {
		margin-left: auto;
		font-size: 11px;
		color: var(--studio-muted);
	}
	.review-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 9px;
		margin-top: 24px;
	}
	.release-panel {
		background: var(--studio-panel);
		border: 1px solid var(--studio-line);
		border-radius: var(--radius);
		padding: 19px;
		margin-top: 20px;
		display: grid;
		gap: 13px;
	}
	.release-panel label {
		font-size: 12px;
	}
	input {
		border: 1px solid var(--studio-line);
		padding: 10px;
		border-radius: var(--radius);
		background: white;
	}
	.release-panel button {
		justify-self: start;
	}
	.delivery-results {
		margin-top: 27px;
	}
	.delivery-row {
		display: flex;
		gap: 13px;
		align-items: center;
		padding: 10px 0;
		border-bottom: 1px solid var(--studio-line);
		font-size: 11px;
	}
	.delivery-row strong {
		font-weight: 500;
	}
	.text-preview {
		padding: 25px;
		border: 1px solid var(--studio-line);
		background: white;
		white-space: pre-wrap;
	}
	@media (max-width: 700px) {
		.review-heading {
			margin-top: 20px;
		}
		h1 {
			font-size: 31px;
		}
		.video-review {
			grid-template-columns: 1fr;
			gap: 22px;
		}
		.video-column {
			max-width: 260px;
			margin: auto;
		}
		h2 {
			font-size: 29px;
		}
		.destination {
			flex-wrap: wrap;
		}
		.destination-note {
			margin-left: 27px;
		}
	}
</style>
