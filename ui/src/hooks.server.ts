import type { Handle, HandleServerError } from '@sveltejs/kit/hooks';
import { getContext } from '#lib/server/context.ts';
import { now } from '#lib/server/db.ts';
import { securityHeaders } from '#lib/server/headers.ts';
import { SESSION_COOKIE, sessionUser } from '#lib/server/sessions.ts';

/** Pages that can be opened without signing in. */
const PUBLIC = new Set(['/login']);

// Fail at startup, not on the first request, if the configuration is invalid
if (!getContext().config.domain) {
	console.warn('UI_DOMAIN is not set: requests for any host name are answered');
}

export const handle: Handle = async ({ event, resolve }) => {
	const { config, db } = getContext();
	const { url, request } = event;

	if (url.pathname === '/healthz') {
		return new Response('ok\n', { headers: { 'Content-Type': 'text/plain' } });
	}
	// A request for another host means a misconfigured proxy (or a DNS rebinding attempt)
	if (config.domain && url.hostname !== config.domain) {
		return new Response(`This server only answers for ${config.domain}\n`, { status: 421 });
	}

	event.locals.user = sessionUser(db, event.cookies.get(SESSION_COOKIE), now());
	event.locals.language = config.language;
	event.locals.timeZone = config.timeZone;

	if (!event.locals.user && !PUBLIC.has(url.pathname)) {
		if (request.method !== 'GET' && request.method !== 'HEAD') {
			return new Response('Sign in first\n', { status: 401 });
		}
		const target =
			url.pathname === '/'
				? ''
				: `?redirectTo=${encodeURIComponent(url.pathname + url.search)}`;
		return new Response(null, { status: 303, headers: { Location: `/login${target}` } });
	}

	const response = await resolve(event, {
		transformPageChunk: ({ html }) => html.replace('%lang%', config.language)
	});
	for (const [name, value] of Object.entries(securityHeaders(url.protocol === 'https:'))) {
		response.headers.set(name, value);
	}
	return response;
};

export const handleError: HandleServerError = ({ kind, error }) => {
	// Errors of the app (error()) and of SvelteKit (404, 405) are expected; the default
	// answer for others hides their details, so they are only logged
	if (kind === 'unknown') {
		console.error(error);
	}
};
