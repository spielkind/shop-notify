<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { isLanguage, messages } from '#lib/i18n/index.ts';

	const language = $derived(page.data.language);
	const t = $derived(
		messages(typeof language === 'string' && isLanguage(language) ? language : 'en')
	);
	const text = $derived(
		page.status === 404
			? t.errors.notFound
			: page.status === 503
				? t.errors.unavailable
				: t.errors.unexpected
	);
</script>

<svelte:head>
	<title>{t.errors.title(page.status)} – {t.appName}</title>
</svelte:head>

<h1>{t.errors.title(page.status)}</h1>
<p class="notice error">{text}</p>
<p><a href={resolve('/')}>{t.nav.overview}</a></p>
