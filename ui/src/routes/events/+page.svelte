<script lang="ts">
	import { resolve } from '$app/paths';
	import EventTable from '#lib/components/EventTable.svelte';
	import { EVENT_NAMES } from '#lib/i18n/types.ts';
	import { localize } from '#lib/localize.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const f = $derived(localize(data));
</script>

<h1>{f.t.events.title}</h1>

<form method="GET" class="filter">
	<fieldset>
		<legend>{f.t.events.eventType}</legend>
		<div class="choices">
			{#each EVENT_NAMES as name (name)}
				<label>
					<input
						type="checkbox"
						name="event"
						value={name}
						checked={data.filter.event.includes(name)}
					/>
					{f.eventName(name)}
				</label>
			{/each}
		</div>
	</fieldset>
	<label>
		{f.t.events.search}
		<input
			type="search"
			name="q"
			value={data.filter.q}
			maxlength="200"
			placeholder={f.t.events.searchPlaceholder}
		/>
	</label>
	<label>
		{f.t.events.listingId}
		<input
			type="text"
			name="listing_id"
			value={data.filter.listingId ?? ''}
			inputmode="numeric"
			pattern="[0-9]*"
		/>
	</label>
	<div class="buttons">
		<button type="submit">{f.t.events.apply}</button>
		<a href={resolve('/events')}>{f.t.events.reset}</a>
	</div>
</form>

{#if data.events.length}
	<EventTable events={data.events} {f} />
{:else}
	<p>{f.t.events.none}</p>
{/if}

<p class="paging">
	<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- only a query string for this page -->
	{#if data.newest}<a href={data.newest}>← {f.t.events.newest}</a>{/if}
	<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- only a query string for this page -->
	{#if data.older}<a href={data.older}>{f.t.events.older} →</a>{/if}
</p>
