<script lang="ts">
	import { page } from '$app/state';
	import PlannerCalendar from '$lib/components/PlannerCalendar.svelte';
	let { data } = $props();
	const reviewView = $derived(page.url.searchParams.get('view') === 'review');
	const events = $derived(
		data.events.map((event) => ({
			...event,
			extendedProps: {
				status: event.status,
				platforms: event.platforms,
				posterSrc: poster(event.draftId)
			}
		}))
	);
	const pending = $derived(data.review);
	const approvedDrafts = $derived(
		data.drafts.filter((draft) => draft.status === 'draft' && draft.approvedAt)
	);
	function poster(id: string) {
		return data.rehearsal
			? id.startsWith('rehearsal-')
				? '/api/rehearsal/poster/' + id
				: '/api/rehearsal/mashup/poster/' + id
			: undefined;
	}
</script>

<svelte:head><title>{reviewView ? 'Video review' : 'Calendar'} · Fleet Social</title></svelte:head>
{#snippet videoCard(draft: (typeof data.review)[number])}
	<a class="review-card" href={'/review/' + draft.id}>
		<div class="preview-frame">
			{#if poster(draft.id)}<img
					src={poster(draft.id)}
					alt={draft.title || 'Video frame'}
				/>{:else if draft.media[0]?.mime.startsWith('video/')}<video
					muted
					playsinline
					preload="metadata"
					><source
						src={'/api/media/' + encodeURIComponent(draft.media[0].storageKey)}
						type={draft.media[0].mime}
					/><track kind="captions" /></video
				>{:else if draft.media[0]}<img
					src={'/api/media/' + encodeURIComponent(draft.media[0].storageKey)}
					alt={draft.media[0].altText || 'Draft media'}
				/>{:else}<p class="text-card">{draft.baseBody.slice(0, 130)}</p>{/if}
		</div>
		<div class="review-card-text">
			<span class="studio-status">{draft.approvedAt ? '✓ Approved' : '● Awaiting approval'}</span>
			<h3 class="post-title">{draft.title || draft.baseBody.slice(0, 60) || 'Untitled post'}</h3>
			<p class="studio-muted">Saved revision · {draft.projectId || 'Choose a project'}</p>
		</div>
		<div class="review-card-footer">
			<span
				>{draft.selectedConnectionIds ? JSON.parse(draft.selectedConnectionIds).length : 0} destinations</span
			><strong>Review →</strong>
		</div>
	</a>
{/snippet}
{#if reviewView}
	<div class="review-page-head">
		<div>
			<h1>Ready for your eye.</h1>
			<p class="studio-muted">Watch the actual cut. Approve the saved version.</p>
		</div>
		<span class="studio-count">{pending.length}</span>
	</div>
	<div class="review-gallery">
		{#each pending as draft (draft.id)}{@render videoCard(draft)}{:else}<div class="studio-empty">
				<h2>All caught up.</h2>
				<p>No videos are waiting for approval.</p>
				<a class="studio-primary" href="/create">Create a video</a>
			</div>{/each}
	</div>
{:else}
	<div class="planner-workspace">
		<div class="planner-main">
			<PlannerCalendar {events} />
			<p class="calendar-footnote">
				{data.rehearsal
					? 'Sample releases are local rehearsal data. No publishing runs on this server.'
					: 'Only posts with a saved release date appear on the calendar.'}
			</p>
			{#if data.hasMore}<p class="studio-notice">
					Showing the latest 500 drafts and deliveries. Open Library for the full workflow.
				</p>{/if}
		</div>
		<aside class="approval-rail">
			<div class="rail-heading">
				<h2>Ready for your eye.</h2>
				<span class="studio-count">{pending.length}</span>
			</div>
			{#each pending.slice(0, 3) as draft (draft.id)}{@render videoCard(draft)}{:else}<div
					class="studio-empty"
				>
					<p>No videos awaiting approval.</p>
					<a href="/create" class="studio-link">Create a video →</a>
				</div>{/each}{#if pending.length > 3}<a href="/?view=review" class="studio-link"
					>Review all {pending.length} videos →</a
				>{/if}{#if approvedDrafts.length}<div class="approved-list">
					<h3>Approved, ready to plan</h3>
					{#each approvedDrafts as draft (draft.id)}<a href={'/review/' + draft.id}
							>{draft.title || 'Approved post'} →</a
						>{/each}
				</div>{/if}
		</aside>
	</div>
{/if}

<style>
	.post-title {
		text-transform: none;
	}
	.planner-workspace {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 270px;
		gap: 28px;
	}
	.planner-main {
		min-width: 0;
	}
	.approval-rail {
		padding-top: 6px;
	}
	.rail-heading {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 18px;
	}
	.rail-heading h2,
	.review-page-head h1 {
		font:
			750 25px/1.2 var(--font-display),
			sans-serif;
		letter-spacing: -0.6px;
	}
	.rail-heading h2 {
		font-style: italic;
	}
	.review-card {
		display: block;
		border: 1px solid var(--studio-line);
		border-radius: var(--radius);
		overflow: hidden;
		background: var(--studio-panel);
		margin-bottom: 17px;
		transition: border-color 0.15s;
	}
	.review-card:hover {
		border-color: var(--input);
	}
	.preview-frame {
		height: 140px;
		background: #242922;
		overflow: hidden;
	}
	.preview-frame img,
	.preview-frame video {
		width: 100%;
		height: 100%;
		object-fit: cover;
		object-position: center 32%;
	}
	.review-card-text {
		padding: 14px;
	}
	.review-card-text h3 {
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
		margin: 10px 0 7px;
	}
	.review-card-text p {
		font-size: 10px;
	}
	.review-card-footer {
		display: flex;
		justify-content: space-between;
		padding: 11px 14px;
		border-top: 1px solid var(--studio-line);
		font-size: 11px;
		color: var(--studio-muted);
	}
	.review-card-footer strong {
		color: var(--studio-accent);
		font-weight: 600;
	}
	.calendar-footnote {
		font-size: 10px;
		line-height: 1.6;
		color: var(--studio-muted);
		margin-top: 15px;
	}
	.approved-list {
		margin-top: 26px;
		border-top: 1px solid var(--studio-line);
		padding-top: 16px;
	}
	.approved-list h3 {
		font-size: 11px;
		color: var(--studio-muted);
		margin-bottom: 12px;
	}
	.approved-list a {
		display: block;
		font-size: 12px;
		padding: 10px 0;
	}
	.review-page-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 25px;
	}
	.review-page-head h1 {
		font-size: 38px;
		margin-bottom: 9px;
	}
	.review-gallery {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 23px;
		max-width: 1050px;
	}
	.review-gallery .preview-frame {
		height: 290px;
	}
	.review-gallery .review-card-text {
		padding: 20px;
	}
	.review-gallery h3 {
		font-size: 18px;
	}
	.text-card {
		padding: 18px;
		color: white;
		font-size: 13px;
	}
	.studio-empty a {
		margin-top: 18px;
	}
	@media (max-width: 1100px) {
		.planner-workspace {
			grid-template-columns: minmax(0, 1fr) 235px;
			gap: 22px;
		}
		.review-gallery {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	@media (max-width: 900px) {
		.planner-workspace {
			grid-template-columns: 1fr;
		}
		.approval-rail {
			display: grid;
			grid-template-columns: repeat(2, minmax(0, 1fr));
			gap: 17px;
			margin-top: 22px;
		}
		.rail-heading,
		.approved-list {
			grid-column: 1 / -1;
		}
	}
	@media (max-width: 700px) {
		.review-page-head h1 {
			font-size: 31px;
		}
		.review-gallery {
			gap: 14px;
		}
		.review-gallery .preview-frame {
			height: 205px;
		}
		.review-gallery .review-card-text {
			padding: 12px;
		}
		.review-gallery h3 {
			font-size: 14px;
		}
		.review-card-footer {
			font-size: 10px;
			padding: 10px;
		}
	}
</style>
