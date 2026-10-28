import { defineConfig } from 'vitest/config';
import adapter from '@sveltejs/adapter-node';
import { sveltekit } from '@sveltejs/kit/vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			adapter: adapter(),
			// The pages are only rendered on the server (csr = false) and send no scripts
			csp: {
				mode: 'auto',
				directives: {
					'default-src': ['none'],
					'script-src': ['none'],
					'style-src': ['self'],
					'img-src': ['self', 'https://i.etsystatic.com'],
					'form-action': ['self'],
					'frame-ancestors': ['none'],
					'base-uri': ['none']
				}
			}
		})
	],
	test: {
		expect: { requireAssertions: true },
		include: ['src/**/*.test.ts'],
		coverage: {
			provider: 'v8',
			include: ['src/lib/**/*.ts'],
			exclude: ['src/lib/api-types.ts'],
			thresholds: { 100: true }
		}
	}
});
