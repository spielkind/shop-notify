<script lang="ts">
	import { localize } from '#lib/localize.ts';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();
	const f = $derived(localize(data));
</script>

<svelte:head>
	<title>{f.t.login.title} – {f.t.appName}</title>
</svelte:head>

<div class="login">
	<h1>{f.t.appName}</h1>
	{#if data.noUsers}
		<p>{f.t.login.noUsers}</p>
		<pre><code>{f.t.login.noUsersCommand}</code></pre>
	{:else}
		<form method="POST">
			<h2>{f.t.login.title}</h2>
			{#if form?.invalid}
				<p class="notice error" role="alert">{f.t.login.invalid}</p>
			{:else if form?.minutes}
				<p class="notice error" role="alert">{f.t.login.tooManyAttempts(form.minutes)}</p>
			{/if}
			<label>
				{f.t.login.username}
				<input
					name="username"
					autocomplete="username"
					required
					maxlength="64"
					value={form?.username ?? ''}
				/>
			</label>
			<label>
				{f.t.login.password}
				<input
					name="password"
					type="password"
					autocomplete="current-password"
					required
					maxlength="1024"
				/>
			</label>
			<button type="submit">{f.t.login.submit}</button>
		</form>
	{/if}
</div>
