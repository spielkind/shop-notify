<script lang="ts">
	import { resolve } from '$app/paths';
	import { safeUrl } from '#lib/links.ts';
	import { localize } from '#lib/localize.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const f = $derived(localize(data));
	const total = $derived(
		data.counts.listed + data.counts.missing + data.counts.sold_out + data.counts.gone
	);

	function label(status: string): string {
		return status === 'all'
			? f.t.listings.all
			: (f.t.listing.status[status as keyof typeof f.t.listing.status] ?? status);
	}

	function count(status: string): number {
		return status === 'all' ? total : data.counts[status as keyof typeof data.counts];
	}
</script>

<h1>{f.t.listings.title}</h1>

<nav class="tabs" aria-label={f.t.listings.status}>
	{#each data.statuses as status (status.name)}
		<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- only a query string for this page -->
		<a href={status.href} aria-current={data.filter.status === status.name ? 'page' : undefined}
			>{label(status.name)} <span class="muted">{count(status.name)}</span></a
		>
	{/each}
</nav>

<form method="GET" class="filter">
	<input type="hidden" name="status" value={data.filter.status} />
	<label>
		{f.t.listings.search}
		<input
			type="search"
			name="q"
			value={data.filter.q}
			maxlength="200"
			placeholder={f.t.events.searchPlaceholder}
		/>
	</label>
	<div class="buttons">
		<button type="submit">{f.t.listings.apply}</button>
	</div>
</form>

{#if data.listings.length}
	<ul class="cards">
		{#each data.listings as item (item.listing.listing_id)}
			{@const thumbnail = safeUrl(item.thumbnail_url)}
			{@const image = safeUrl(item.image_url)}
			<li class="card">
				<a href={resolve('/listings/[id]', { id: String(item.listing.listing_id) })}>
					{#if image}
						<img
							src={thumbnail ?? image}
							srcset={thumbnail ? `${thumbnail} 170w, ${image} 570w` : undefined}
							sizes="(max-width: 40rem) 45vw, 14rem"
							alt=""
							loading="lazy"
							referrerpolicy="no-referrer"
						/>
					{:else}
						<span class="no-image">{f.t.listings.noImage}</span>
					{/if}
					<span class="title">{item.listing.title}</span>
				</a>
				<p>
					{#if item.listing.price}<strong>{f.money(item.listing.price)}</strong>{/if}
					{#if item.listing.quantity !== null && item.listing.quantity !== undefined}
						<span class="muted">{f.t.listings.inStock(item.listing.quantity)}</span>
					{/if}
				</p>
				{#if item.status !== 'listed'}
					<p><span class="tag" data-status={item.status}>{label(item.status)}</span></p>
				{/if}
			</li>
		{/each}
	</ul>
{:else}
	<p>{f.t.listings.none}</p>
{/if}

<p class="paging">
	<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- only a query string for this page -->
	{#if data.newest}<a href={data.newest}>← {f.t.listings.newest}</a>{/if}
	<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- only a query string for this page -->
	{#if data.older}<a href={data.older}>{f.t.listings.older} →</a>{/if}
</p>
