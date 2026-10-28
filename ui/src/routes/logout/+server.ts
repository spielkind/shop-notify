import { redirect } from '@sveltejs/kit';
import { getContext } from '#lib/server/context.ts';
import { deleteSession, SESSION_COOKIE } from '#lib/server/sessions.ts';
import type { RequestHandler } from './$types';

/** Ends the session; a form POST, so SvelteKit's CSRF check applies. */
export const POST: RequestHandler = ({ cookies }) => {
	const token = cookies.get(SESSION_COOKIE);
	if (token) {
		deleteSession(getContext().db, token);
	}
	cookies.delete(SESSION_COOKIE, { path: '/', secure: true });
	redirect(303, '/login');
};
