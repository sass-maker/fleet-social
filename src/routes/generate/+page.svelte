<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { fleetProjects } from '$lib/domain/fleet-projects';
	import { slotDisplayState } from '$lib/domain/generation';
	import CopyButton from '$lib/components/CopyButton.svelte';
	import { ArrowLeft, ArrowRight } from '@lucide/svelte';
	let { data } = $props();
	let busy = $state(false);
	let problem = $state('');
	let notice = $state('');
	let secret = $state('');
	let projectId = $state(fleetProjects[0]?.id ?? '');
	let generatorId = $state('');
	let title = $state('');
	let brief = $state('');
	let plannedFor = $state('');
	let name = $state('');
	let kind = $state<'local' | 'cloud' | 'api'>('local');
	let endpointUrl = $state('');
	let leadHours = $state(6);
	const chosenGenerator = $derived(generatorId || data.generators[0]?.id || '');
	const now = new Date();
	const stateLabel = {
		planned: 'Awaiting generation',
		requested: 'Generating',
		ready: 'Ready for review',
		failed: 'Generation failed',
		late: 'Late · no media yet',
		cancelled: 'Cancelled'
	} as const;
	const kindLabel = { local: 'Local', cloud: 'Cloud', api: 'API' } as const;
	const projectName = (id: string) => fleetProjects.find((item) => item.id === id)?.name ?? id;
	async function send(method: string, path: string, body?: unknown) {
		busy = true;
		problem = '';
		notice = '';
		try {
			const response = await fetch(path, {
				method,
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
	async function planSlot() {
		if (!plannedFor) {
			problem = 'Choose a release date and time.';
			return;
		}
		const result = await send('POST', '/api/generation/slots', {
			generatorId: chosenGenerator,
			projectId,
			title,
			brief,
			plannedFor: new Date(plannedFor).toISOString()
		});
		if (result) {
			brief = '';
			title = '';
			notice = 'Release planned. The generator is asked ahead of the lead time.';
		}
	}
	async function addGenerator() {
		const result = await send('POST', '/api/generators', {
			name,
			kind,
			endpointUrl: kind === 'local' ? null : endpointUrl,
			leadMinutes: Math.round(leadHours * 60)
		});
		if (result) {
			secret = result.secret ?? '';
			notice = result.secret
				? 'Generator added. Copy its signing secret now; it is not shown again.'
				: 'Local generator added. It claims due slots with an intake key.';
			name = '';
			endpointUrl = '';
		}
	}
</script>

<svelte:head><title>Planned generation · Fleet Social</title></svelte:head>
<a href="/" class="studio-back"><ArrowLeft size={14} /> Calendar</a>
<div class="generate-heading">
	<h1>Planned generation.</h1>
	<p class="studio-muted">
		Pick a release date. A generator makes the media ahead of time; you still review it.
	</p>
</div>
{#if problem}<p class="studio-error" role="alert">{problem}</p>{/if}
{#if notice}<p class="studio-notice" role="status">{notice}</p>{/if}
{#if secret}<div class="secret">
		<code>{secret}</code><CopyButton value={secret} ariaLabel="Copy generator signing secret" />
	</div>{/if}
<div class="generate-grid">
	<div>
		<section>
			<h2>Plan a generated release</h2>
			{#if data.generators.length}<form
					onsubmit={(event) => {
						event.preventDefault();
						void planSlot();
					}}
				>
					<label for="slot-project">Project</label><select id="slot-project" bind:value={projectId}
						>{#each fleetProjects as project (project.id)}<option value={project.id}
								>{project.name}</option
							>{/each}</select
					>
					<label for="slot-generator">Generator</label><select
						id="slot-generator"
						value={chosenGenerator}
						onchange={(event) => (generatorId = event.currentTarget.value)}
						>{#each data.generators as generator (generator.id)}<option value={generator.id}
								>{generator.name} · {kindLabel[generator.kind as keyof typeof kindLabel]}</option
							>{/each}</select
					>
					<label for="slot-title">Title · optional</label><input
						id="slot-title"
						bind:value={title}
						maxlength="200"
					/>
					<label for="slot-brief">What should it make?</label><textarea
						id="slot-brief"
						bind:value={brief}
						rows="4"
						maxlength="4000"
						required
						placeholder="A 30-second clip on why release notes matter, from this week's changelog…"
					></textarea>
					<label for="slot-when"
						>Release date and time <span class="studio-muted"
							>({Intl.DateTimeFormat().resolvedOptions().timeZone})</span
						></label
					><input id="slot-when" type="datetime-local" bind:value={plannedFor} required />
					<button class="studio-primary" type="submit" disabled={busy || !brief.trim()}
						>{busy ? 'Saving…' : 'Plan release'}</button
					>
				</form>{:else}<p class="studio-muted">Add a generator first.</p>{/if}
		</section>
		<section>
			<h2>Upcoming</h2>
			{#each data.slots as slot (slot.id)}
				{@const state = slotDisplayState(slot, now)}
				<article class="slot" id={`slot-${slot.id}`}>
					<div>
						<span class="studio-status" class:approved={state === 'ready'}>{stateLabel[state]}</span
						>
						<h3>{slot.title || slot.brief.slice(0, 80)}</h3>
						<p class="studio-muted">
							{new Date(slot.plannedFor).toLocaleString(undefined, {
								dateStyle: 'medium',
								timeStyle: 'short'
							})} · {projectName(slot.projectId)} · {slot.generator}
						</p>
						{#if slot.lastError}<p class="studio-error">{slot.lastError}</p>{/if}
					</div>
					<div class="slot-actions">
						{#if slot.draftId && state === 'ready'}<a
								class="studio-secondary"
								href={`/review/${slot.draftId}`}>Review <ArrowRight size={14} /></a
							>{/if}
						{#if state === 'failed' || state === 'late'}<button
								class="studio-secondary"
								disabled={busy}
								onclick={() =>
									send('PATCH', `/api/generation/slots/${slot.id}`, { action: 'retry' })}
								>Ask again</button
							>{/if}
						{#if state !== 'ready'}<button
								class="studio-link"
								disabled={busy}
								onclick={() =>
									send('PATCH', `/api/generation/slots/${slot.id}`, { action: 'cancel' })}
								>Cancel</button
							>{/if}
					</div>
				</article>
			{:else}<p class="studio-muted">Nothing planned yet.</p>{/each}
		</section>
	</div>
	<aside>
		<section>
			<h2>Generators</h2>
			{#each data.generators as generator (generator.id)}<div class="generator">
					<div>
						<strong>{generator.name}</strong>
						<p class="studio-muted">
							{kindLabel[generator.kind as keyof typeof kindLabel]} · asked {generator.leadMinutes /
								60}h ahead{generator.endpointUrl
								? ` · ${new URL(generator.endpointUrl).host}`
								: ' · claims its work'}
						</p>
					</div>
					<button
						class="studio-link"
						disabled={busy}
						onclick={() => send('DELETE', `/api/generators/${generator.id}`)}>Remove</button
					>
				</div>{:else}<p class="studio-muted">No generators yet.</p>{/each}
			<form
				class="add-generator"
				onsubmit={(event) => {
					event.preventDefault();
					void addGenerator();
				}}
			>
				<h3>Add a generator</h3>
				<label for="gen-name">Name</label><input
					id="gen-name"
					bind:value={name}
					required
					pattern="[A-Za-z0-9][A-Za-z0-9._\-]*"
					maxlength="64"
					placeholder="mashup"
				/>
				<label for="gen-kind">Runs</label><select id="gen-kind" bind:value={kind}
					><option value="local">Locally · claims due slots</option><option value="cloud"
						>In the cloud · called by webhook</option
					><option value="api">Through an API · called by webhook</option></select
				>
				{#if kind !== 'local'}<label for="gen-endpoint">Webhook URL</label><input
						id="gen-endpoint"
						type="url"
						bind:value={endpointUrl}
						required
						placeholder="https://"
					/>{/if}
				<label for="gen-lead">Ask this many hours before release</label><input
					id="gen-lead"
					type="number"
					min="0"
					max="336"
					step="0.5"
					bind:value={leadHours}
				/>
				<button class="studio-secondary" type="submit" disabled={busy || !name.trim()}
					>Add generator</button
				>
			</form>
		</section>
	</aside>
</div>

<style>
	.generate-heading {
		margin: 24px 0 26px;
	}
	h1 {
		font:
			750 38px/1.2 var(--font-display),
			sans-serif;
		letter-spacing: -1px;
	}
	.generate-heading p {
		margin-top: 8px;
	}
	.generate-grid {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 300px;
		gap: 36px;
		max-width: 1130px;
	}
	section {
		padding: 17px 0 24px;
		border-bottom: 1px solid var(--studio-line);
	}
	h2 {
		font:
			750 22px/1.25 var(--font-display),
			sans-serif;
		margin-bottom: 13px;
	}
	h3 {
		font-size: 14px;
		font-weight: 650;
		margin: 8px 0 4px;
	}
	form {
		display: grid;
	}
	label {
		font-size: 12px;
		font-weight: 550;
	}
	input,
	select,
	textarea {
		width: 100%;
		border: 1px solid var(--studio-line);
		border-radius: var(--radius);
		background: var(--studio-panel);
		padding: 10px;
		font: inherit;
		color: var(--studio-ink);
		margin: 7px 0 11px;
	}
	textarea {
		resize: vertical;
		line-height: 1.6;
	}
	form > button {
		justify-self: start;
	}
	.slot {
		display: flex;
		justify-content: space-between;
		gap: 16px;
		padding: 14px 0;
		border-bottom: 1px solid var(--studio-line);
	}
	.slot:last-child {
		border-bottom: 0;
	}
	.slot p {
		font-size: 11px;
		margin-top: 3px;
	}
	.slot-actions {
		display: flex;
		align-items: center;
		gap: 10px;
		flex-shrink: 0;
	}
	.generator {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 10px;
		padding: 9px 0;
	}
	.generator p {
		font-size: 11px;
		margin-top: 2px;
	}
	.add-generator {
		margin-top: 18px;
		padding-top: 14px;
		border-top: 1px solid var(--studio-line);
	}
	.secret {
		display: flex;
		align-items: center;
		gap: 10px;
		margin: 10px 0 18px;
		padding: 10px 12px;
		border: 1px solid var(--studio-line);
		border-radius: var(--radius);
		background: var(--studio-panel);
		overflow-x: auto;
	}
	.secret code {
		font-size: 12px;
		white-space: nowrap;
	}
	@media (max-width: 800px) {
		h1 {
			font-size: 31px;
		}
		.generate-grid {
			grid-template-columns: 1fr;
			gap: 8px;
		}
		.slot {
			flex-direction: column;
		}
	}
</style>
