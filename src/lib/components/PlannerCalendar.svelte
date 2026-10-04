<script lang="ts">
	import { onMount } from 'svelte';
	import type { Calendar, EventInput } from '@fullcalendar/core';
	import { ChevronLeft, ChevronRight } from '@lucide/svelte';
	import { platformName } from '$lib/domain/platforms';
	let { events }: { events: EventInput[] } = $props();
	let host: HTMLDivElement;
	let calendar = $state<Calendar | null>(null);
	let title = $state(
		new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' }).format(new Date())
	);
	let view = $state('dayGridMonth');
	let range = $state({ start: new Date(0), end: new Date(8640000000000000) });
	const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
	const visibleEvents = $derived(
		events.filter((event) => {
			const when = new Date(event.start as string);
			return when >= range.start && when < range.end;
		})
	);
	onMount(() => {
		let disposed = false;
		void Promise.all([
			import('@fullcalendar/core'),
			import('@fullcalendar/daygrid'),
			import('@fullcalendar/timegrid')
		]).then(([core, month, week]) => {
			if (disposed) return;
			calendar = new core.Calendar(host, {
				plugins: [month.default, week.default],
				initialView: view,
				firstDay: 1,
				headerToolbar: false,
				height: 'auto',
				timeZone: 'local',
				nowIndicator: true,
				dayMaxEvents: 3,
				events,
				eventDisplay: 'block',
				eventTimeFormat: { hour: '2-digit', minute: '2-digit', hour12: false },
				datesSet(info) {
					title = info.view.title;
					range = { start: info.start, end: info.end };
				},
				eventClassNames(info) {
					return ['planner-event', `state-${info.event.extendedProps.status}`];
				},
				eventContent(info) {
					const wrapper = document.createElement('div');
					wrapper.className = 'planner-event-body';
					if (info.event.extendedProps.posterSrc) {
						const poster = document.createElement('img');
						poster.src = info.event.extendedProps.posterSrc;
						poster.alt = '';
						wrapper.append(poster);
					}
					const copy = document.createElement('div');
					const heading = document.createElement('strong');
					heading.textContent = info.event.title;
					const detail = document.createElement('span');
					detail.textContent = `${info.timeText} · ${(info.event.extendedProps.platforms ?? []).map(platformName).join(' + ')}`;
					copy.append(heading, detail);
					wrapper.append(copy);
					return { domNodes: [wrapper] };
				}
			});
			calendar.render();
		});
		return () => {
			disposed = true;
			calendar?.destroy();
		};
	});
	$effect(() => {
		if (calendar) {
			calendar.removeAllEvents();
			calendar.addEventSource(events);
		}
	});
	function changeView(next: string) {
		view = next;
		calendar?.changeView(next);
	}
</script>

<div class="planner-heading">
	<div>
		<h1>{title}</h1>
		<p class="studio-muted">A little planning. A final look. Then it goes out.</p>
	</div>
	<div class="calendar-navigation">
		<button
			class="studio-secondary icon-button"
			aria-label="Previous period"
			onclick={() => calendar?.prev()}><ChevronLeft size={17} /></button
		>
		<button class="studio-secondary" onclick={() => calendar?.today()}>Today</button>
		<button
			class="studio-secondary icon-button"
			aria-label="Next period"
			onclick={() => calendar?.next()}><ChevronRight size={17} /></button
		>
	</div>
</div>
<div class="calendar-toolbar">
	<div class="studio-tabs" aria-label="Calendar view">
		<button
			class:active={view === 'dayGridMonth'}
			aria-pressed={view === 'dayGridMonth'}
			onclick={() => changeView('dayGridMonth')}>Month</button
		>
		<button
			class:active={view === 'timeGridWeek'}
			aria-pressed={view === 'timeGridWeek'}
			onclick={() => changeView('timeGridWeek')}>Week</button
		>
	</div>
	<div class="calendar-legend">
		<span><i class="scheduled-dot"></i>Scheduled</span><span
			><i class="published-dot"></i>Delivered</span
		><span>{zone}</span>
	</div>
</div>
<div class="calendar-frame" bind:this={host}></div>
<div class="calendar-agenda">
	{#each visibleEvents as event (event.id)}
		<a class="agenda-row" href={event.url}
			><time datetime={event.start as string}
				>{new Date(event.start as string).toLocaleDateString(undefined, {
					month: 'short',
					day: 'numeric'
				})}</time
			>
			<div>
				<strong>{event.title}</strong>
				<p class="studio-muted">
					{new Date(event.start as string).toLocaleTimeString(undefined, {
						hour: '2-digit',
						minute: '2-digit'
					})} · {event.extendedProps?.status}
				</p>
			</div></a
		>
	{:else}<p class="studio-empty">
			No scheduled posts in this period. Approve a video to plan its release.
		</p>{/each}
</div>

<style>
	h1 {
		font:
			400 36px/1.2 Georgia,
			serif;
		letter-spacing: -1px;
		color: var(--studio-ink);
	}
	.planner-heading {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 24px;
	}
	.planner-heading p {
		margin-top: 8px;
	}
	.calendar-navigation {
		display: flex;
		gap: 6px;
	}
	.icon-button {
		padding: 8px;
	}
	.calendar-toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		margin: 26px 0 17px;
	}
	.calendar-legend {
		display: flex;
		gap: 16px;
		color: var(--studio-muted);
		font-size: 11px;
	}
	i {
		display: inline-block;
		width: 6px;
		height: 6px;
		border-radius: 50%;
		margin-right: 5px;
	}
	.scheduled-dot {
		background: #42765b;
	}
	.published-dot {
		background: #5472a1;
	}
	.calendar-frame {
		--fc-border-color: var(--studio-line);
		--fc-today-bg-color: #eef3e9;
		--fc-event-text-color: var(--studio-ink);
		background: var(--studio-panel);
		padding: 0;
		border: 1px solid var(--studio-line);
		border-radius: 10px;
		overflow: hidden;
		min-height: 520px;
	}
	.calendar-frame :global(.fc) {
		--fc-border-color: var(--studio-line);
		--fc-today-bg-color: #eef3e9;
		--fc-neutral-bg-color: #f0f1ea;
		--fc-small-font-size: 11px;
		color: var(--studio-ink);
		font-size: 12px;
	}
	.calendar-frame :global(.fc-theme-standard .fc-scrollgrid) {
		border: 0;
	}
	.calendar-frame :global(.fc-col-header-cell) {
		font-size: 10px;
		font-weight: 600;
		color: var(--studio-muted);
		padding: 9px 0;
		text-transform: uppercase;
		letter-spacing: 0.6px;
	}
	.calendar-frame :global(.fc-daygrid-day-frame) {
		min-height: 103px;
		padding: 3px;
	}
	.calendar-frame :global(.fc-daygrid-day-number) {
		padding: 6px 7px;
		font-size: 11px;
	}
	.calendar-frame :global(.planner-event) {
		background: #e6efe8;
		border: 0;
		border-left: 2px solid #42765b;
		border-radius: 4px;
		color: var(--studio-ink);
		padding: 6px;
		margin: 4px;
		cursor: pointer;
	}
	.calendar-frame :global(.planner-event strong) {
		display: block;
		font-size: 11px;
		line-height: 1.35;
		white-space: normal;
		font-weight: 600;
		overflow-wrap: anywhere;
	}
	.calendar-frame :global(.planner-event-body) {
		display: flex;
		gap: 6px;
		align-items: flex-start;
	}
	.calendar-frame :global(.planner-event-body > div) {
		min-width: 0;
	}
	.calendar-frame :global(.planner-event-body img) {
		width: 20px;
		height: 35px;
		object-fit: cover;
		border-radius: 2px;
		flex-shrink: 0;
	}
	@media (max-width: 1100px) {
		.calendar-frame :global(.planner-event-body img) {
			display: none;
		}
	}
	.calendar-frame :global(.planner-event span) {
		display: block;
		font-size: 9px;
		line-height: 1.5;
		white-space: normal;
		margin-top: 4px;
	}
	.calendar-frame :global(.state-published) {
		background: #e8eef6;
		border-color: #5472a1;
	}
	.calendar-frame :global(.state-failed),
	.calendar-frame :global(.state-uncertain) {
		background: #f9e8e3;
		border-color: #a43f35;
	}
	.calendar-frame :global(.fc-event:focus-visible) {
		outline: 2px solid var(--studio-accent);
		outline-offset: 2px;
	}
	.calendar-agenda {
		display: none;
	}
	.agenda-row {
		display: flex;
		gap: 18px;
		padding: 20px 0;
		border-bottom: 1px solid var(--studio-line);
	}
	time {
		width: 55px;
		color: var(--studio-accent);
		font-weight: 650;
	}
	@media (max-width: 700px) {
		h1 {
			font-size: 29px;
		}
		.planner-heading {
			align-items: flex-start;
			flex-direction: column;
			gap: 17px;
		}
		.calendar-toolbar {
			align-items: flex-start;
			flex-direction: column;
			margin-top: 18px;
		}
		.calendar-legend {
			gap: 12px;
			flex-wrap: wrap;
		}
		.calendar-frame {
			display: none;
		}
		.calendar-agenda {
			display: block;
		}
	}
</style>
