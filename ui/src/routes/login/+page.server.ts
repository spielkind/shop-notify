import { fail, redirect } from '@sveltejs/kit';
import { getContext } from '#lib/server/context.ts';
import { now } from '#lib/server/db.ts';
import { createSession, SESSION_COOKIE } from '#lib/server/sessions.ts';
import { hasUsers, verifyLogin } from '#lib/server/users.ts';
import type { Actions, PageServerLoad } from './$types';

/** Only local paths, so that the login can't redirect to another site. */
function target(url: URL): string {
	const path = url.searchParams.get('redirectTo') ?? '/';
	return /^\/(?![/\\])/.test(path) ? path : '/';
}

export const load: PageServerLoad = ({ locals, url }) => {
	if (locals.user) {
		redirect(303, target(url));
	}
	return { noUsers: !hasUsers(getContext().db) };
};

export const actions: Actions = {
	default: async ({ cookies, getClientAddress, request, url }) => {
		const { config, db, limiter } = getContext();
		const form = await request.formData();
		const username = String(form.get('username') ?? '').trim();
		const password = String(form.get('password') ?? '');
		let address: string;
		try {
			address = getClientAddress();
		} catch {
			// ADDRESS_HEADER is set, but the request didn't come through the proxy
			address = 'unknown';
		}

		const time = now();
		const wait = limiter.retryAfter(username, address, time);
		if (wait > 0) {
			return fail(429, { username, minutes: Math.ceil(wait / 60) });
		}
		const user = await verifyLogin(db, username, password);
		if (!user) {
			limiter.failed(username, address, time);
			console.warn(
				`Failed sign-in for ${JSON.stringify(username.slice(0, 64))} from ${address}`
			);
			return fail(400, { username, invalid: true });
		}
		limiter.succeeded(username);
		const days = config.sessionDays;
		cookies.set(SESSION_COOKIE, createSession(db, user.id, days, time), {
			path: '/',
			httpOnly: true,
			secure: true,
			sameSite: 'lax',
			maxAge: days * 86400
		});
		redirect(303, target(url));
	}
};
