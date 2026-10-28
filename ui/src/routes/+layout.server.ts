import type { LayoutServerLoad } from './$types';

// Only rendered on the server: the browser gets HTML without any scripts
export const csr = false;

export const load: LayoutServerLoad = ({ locals }) => ({
	user: locals.user?.name ?? null,
	language: locals.language,
	timeZone: locals.timeZone
});
