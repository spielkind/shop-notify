<script lang="ts">
	import { resolve } from '$app/paths';
	import type { Event } from '#lib/api-types.ts';
	import type { Formatter } from '#lib/format.ts';

	let {
		events,
		f,
		showListing = true
	}: { events: Event[]; f: Formatter; showListing?: boolean } = $props();
</script>

<table>
	<thead>
		<tr>
			<th scope="col">{f.t.columns.time}</th>
			{#if showListing}<th scope="col">{f.t.columns.listing}</th>{/if}
			<th scope="col">{f.t.columns.event}</th>
			<th scope="col">{f.t.columns.details}</th>
		</tr>
	</thead>
	<tbody>
		{#each events as event (event.id)}
			<tr>
				<td class="time"><time datetime={event.time}>{f.dateTime(event.time)}</time></td>
				{#if showListing}
					<td
						><a href={resolve('/listings/[id]', { id: String(event.listing_id) })}
							>{event.title}</a
						></td
					>
				{/if}
				<td><span class="tag" data-event={event.event}>{f.eventName(event.event)}</span></td
				>
				<td class="details">{f.details(event)}</td>
			</tr>
		{/each}
	</tbody>
</table>
