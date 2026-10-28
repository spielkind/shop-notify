<script lang="ts">
	import { resolve } from '$app/paths';
	import '../app.css';
	import { page } from '$app/state';
	import { localize } from '#lib/localize.ts';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();
	const f = $derived(localize(data));
	const links = $derived([
		{ href: resolve('/'), label: f.t.nav.overview },
		{ href: resolve('/events'), label: f.t.nav.events },
		{ href: resolve('/polls'), label: f.t.nav.polls }
	]);
</script>

<svelte:head>
	<title>{f.t.appName}</title>
</svelte:head>

{#if data.user}
	<header>
		<nav>
			<a class="brand" href={resolve('/')}>{f.t.appName}</a>
			{#each links as link (link.href)}
				<a
					href={link.href}
					aria-current={page.url.pathname === link.href ? 'page' : undefined}
					>{link.label}</a
				>
			{/each}
		</nav>
		<form method="POST" action={resolve('/logout')}>
			<span class="muted">{f.t.nav.signedInAs(data.user)}</span>
			<button type="submit">{f.t.nav.logout}</button>
		</form>
	</header>
{/if}

<main>
	{@render children()}
</main>
