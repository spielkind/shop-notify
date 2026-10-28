<script lang="ts">
	import { resolve } from '$app/paths';
	import { localize } from '#lib/localize.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const f = $derived(localize(data));
</script>

<h1>{f.t.polls.title}</h1>

{#if data.polls.length}
	<table>
		<thead>
			<tr>
				<th scope="col">{f.t.columns.time}</th>
				<th scope="col">{f.t.polls.result}</th>
				<th scope="col" class="number">{f.t.polls.listings}</th>
				<th scope="col">{f.t.polls.changes}</th>
			</tr>
		</thead>
		<tbody>
			{#each data.polls as poll (poll.id)}
				{@const result = poll.error ? 'failed' : poll.complete ? 'ok' : 'incomplete'}
				<tr data-result={result}>
					<td class="time"><time datetime={poll.time}>{f.dateTime(poll.time)}</time></td>
					<td>
						{#if poll.error}
							<span class="tag" data-result="failed">{f.t.polls.failed}</span>
							<span class="details">{poll.error}</span>
						{:else if poll.complete}
							<span class="tag" data-result="ok">{f.t.polls.complete}</span>
						{:else}
							<span class="tag" data-result="incomplete">{f.t.polls.incomplete}</span>
						{/if}
					</td>
					<td class="number">
						{#if poll.received !== null}{poll.received}/{poll.reported}{/if}
					</td>
					<td class="details">{f.pollChanges(poll) || f.t.polls.noChanges}</td>
				</tr>
			{/each}
		</tbody>
	</table>
{:else}
	<p>{f.t.polls.none}</p>
{/if}

<p class="paging">
	{#if data.newest}<a href={resolve('/polls')}>← {f.t.polls.newest}</a>{/if}
	<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- only a query string for this page -->
	{#if data.older}<a href={data.older}>{f.t.polls.older} →</a>{/if}
</p>
