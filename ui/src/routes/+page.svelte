<script lang="ts">
	import { resolve } from '$app/paths';
	import EventTable from '#lib/components/EventTable.svelte';
	import { localize } from '#lib/localize.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const f = $derived(localize(data));
	const poll = $derived(data.health.last_poll);
</script>

<h1>{f.t.overview.title}</h1>

{#if data.health.status !== 'ok'}
	<p class="notice error">{f.t.errors.unavailable}</p>
{:else}
	<section>
		<h2>{f.t.overview.lastPoll}</h2>
		{#if !poll}
			<p>{f.t.overview.noPollYet}</p>
		{:else}
			<p
				class="poll"
				data-result={poll.error ? 'failed' : poll.complete ? 'ok' : 'incomplete'}
			>
				<time datetime={poll.time}>{f.dateTime(poll.time)}</time> –
				{#if poll.error}
					{f.t.overview.pollFailed}: {poll.error}
				{:else if poll.complete}
					{f.t.overview.pollOk(poll.received ?? 0)}
				{:else}
					{f.t.overview.pollIncomplete(poll.received ?? 0, poll.reported ?? 0)}
				{/if}
				{#if f.pollChanges(poll)}<span class="muted">({f.pollChanges(poll)})</span>{/if}
			</p>
		{/if}
	</section>

	<section>
		<h2>{f.t.overview.recentEvents}</h2>
		{#if data.events.length}
			<EventTable events={data.events} {f} />
			<p><a href={resolve('/events')}>{f.t.overview.allEvents} →</a></p>
		{:else}
			<p>{f.t.events.none}</p>
		{/if}
	</section>
{/if}
