<script lang="ts">
	import EventTable from '#lib/components/EventTable.svelte';
	import { safeUrl } from '#lib/links.ts';
	import { localize } from '#lib/localize.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const f = $derived(localize(data));
	const item = $derived(data.listing);
	const listing = $derived(item.listing);
	const status = $derived(
		item.status in f.t.listing.status
			? f.t.listing.status[item.status as keyof typeof f.t.listing.status]
			: item.status
	);
	const times = $derived(
		[
			[f.t.listing.firstSeen, item.first_seen],
			[f.t.listing.lastSeen, item.last_seen],
			[f.t.listing.missingSince, item.missing_since],
			[f.t.listing.soldOutSince, item.sold_out_since],
			[f.t.listing.goneSince, item.gone_since],
			[item.filtered ? f.t.listing.filteredOut : f.t.listing.notified, item.notified]
		].filter((entry): entry is [string, string] => entry[1] !== null)
	);
</script>

<svelte:head>
	<title>{listing.title} – {f.t.appName}</title>
</svelte:head>

<article class="listing">
	{#if safeUrl(listing.image_url)}
		<img src={safeUrl(listing.image_url)} alt="" referrerpolicy="no-referrer" />
	{/if}
	<div>
		<p class="muted">{f.t.listing.title(listing.listing_id)}</p>
		<h1>{listing.title}</h1>
		<p><span class="tag" data-status={item.status}>{status}</span></p>
		<dl>
			{#if listing.price}
				<dt>{f.t.listing.price}</dt>
				<dd>{f.money(listing.price)}</dd>
			{/if}
			{#if listing.quantity !== null && listing.quantity !== undefined}
				<dt>{f.t.listing.quantity}</dt>
				<dd>{listing.quantity}</dd>
			{/if}
			{#each times as [label, time] (label)}
				<dt>{label}</dt>
				<dd><time datetime={time}>{f.dateTime(time)}</time></dd>
			{/each}
			{#if item.pending}
				<dt>{f.t.listing.pending}</dt>
				<dd>{f.notice(item.pending)}</dd>
			{/if}
		</dl>
		{#if safeUrl(listing.url)}
			<p>
				<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- external link to Etsy -->
				<a href={safeUrl(listing.url)} rel="noopener noreferrer" target="_blank"
					>{f.t.listing.viewOnEtsy} ↗</a
				>
			</p>
		{/if}
	</div>
</article>

<h2>{f.t.listing.history}</h2>
<EventTable events={data.events} {f} showListing={false} />
