import { isHttpError, isRedirect } from '@sveltejs/kit';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { now } from '#lib/server/db.ts';
import { createSession, SESSION_COOKIE } from '#lib/server/sessions.ts';
import { setPassword, verifyLogin } from '#lib/server/users.ts';
import * as layout from '../routes/+layout.server.ts';
import * as overview from '../routes/+page.server.ts';
import * as events from '../routes/events/+page.server.ts';
import * as listingPage from '../routes/listings/[id]/+page.server.ts';
import * as listings from '../routes/listings/+page.server.ts';
import * as login from '../routes/login/+page.server.ts';
import * as logout from '../routes/logout/+server.ts';
import * as polls from '../routes/polls/+page.server.ts';
import {
	event,
	listingPage as page,
	poll,
	storedListing,
	testContext,
	type Answer
} from './helpers.ts';

const PASSWORD = 'correct horse battery';

/** Calls a SvelteKit function with an event that only has what it uses. */
function call<T>(fn: (event: never) => T, event: object): T {
	return fn(event as never);
}

/** The data a load function returns (typed without the ``void`` of a load that returns nothing). */
async function loaded<T>(fn: (event: never) => T, event: object) {
	return (await fn(event as never)) as Exclude<Awaited<T>, void>;
}

/** The error or redirect a function throws. */
async function thrown(fn: () => unknown): Promise<unknown> {
	try {
		await fn();
	} catch (e) {
		return e;
	}
	throw new Error('nothing thrown');
}

function cookieJar(initial: Record<string, string> = {}) {
	const values = { ...initial };
	const set: { name: string; value: string; options: object }[] = [];
	const deleted: string[] = [];
	return {
		set,
		deleted,
		get: (name: string) => values[name],
		cookies: {
			get: (name: string) => values[name],
			set: (name: string, value: string, options: object) => {
				set.push({ name, value, options });
				values[name] = value;
			},
			delete: (name: string) => {
				deleted.push(name);
				delete values[name];
			}
		}
	};
}

async function signedIn(db: ReturnType<typeof testContext>['context']['db']) {
	await setPassword(db, 'admin', PASSWORD, now());
	const user = (await verifyLogin(db, 'admin', PASSWORD))!;
	return { user, token: createSession(db, user.id, 30, now()) };
}

function api(routes: Record<string, Answer>) {
	return (url: URL): Answer => routes[url.pathname] ?? [404, { error: 'unknown' }];
}

afterEach(() => vi.restoreAllMocks());

describe('handle', () => {
	let hooks: typeof import('../hooks.server.ts');
	const test = testContext();

	beforeAll(async () => {
		hooks = await import('../hooks.server.ts');
	});

	function request(url: string, init: { method?: string; cookie?: string } = {}) {
		const resolved: object[] = [];
		const locals = {} as App.Locals;
		const event = {
			url: new URL(url),
			request: new Request(url, { method: init.method ?? 'GET' }),
			cookies: cookieJar(init.cookie ? { [SESSION_COOKIE]: init.cookie } : {}).cookies,
			locals
		};
		const resolve = async (
			_event: unknown,
			options?: { transformPageChunk?: (input: { html: string; done: boolean }) => string }
		) => {
			resolved.push(event);
			const html = options?.transformPageChunk?.({
				html: '<html lang="%lang%">',
				done: true
			});
			return new Response(html ?? 'page');
		};
		return {
			locals,
			resolved,
			response: hooks.handle({ event, resolve } as never) as Promise<Response>
		};
	}

	it('answers the health check for any host', async () => {
		const { response, resolved } = request('http://127.0.0.1:3000/healthz');
		const r = await response;
		expect([r.status, await r.text(), resolved.length]).toEqual([200, 'ok\n', 0]);
	});

	it('rejects other host names', async () => {
		const r = await request('https://evil.example/login').response;
		expect(r.status).toBe(421);
		expect(await r.text()).toContain('only answers for shop.example.com');
	});

	it('sends visitors who are not signed in to the login page', async () => {
		const deep = await request('https://shop.example.com/events?q=ring').response;
		expect([deep.status, deep.headers.get('location')]).toEqual([
			303,
			'/login?redirectTo=%2Fevents%3Fq%3Dring'
		]);
		const home = await request('https://shop.example.com/').response;
		expect(home.headers.get('location')).toBe('/login');
		const post = await request('https://shop.example.com/logout', { method: 'POST' }).response;
		expect(post.status).toBe(401);
		const head = await request('https://shop.example.com/', { method: 'HEAD' }).response;
		expect(head.status).toBe(303);
	});

	it('shows the login page with the security headers and the language', async () => {
		const { response, locals } = request('https://shop.example.com/login');
		const r = await response;
		expect(await r.text()).toBe('<html lang="en">');
		expect(r.headers.get('strict-transport-security')).toBe('max-age=31536000');
		expect(r.headers.get('x-frame-options')).toBe('DENY');
		expect(locals).toEqual({ user: null, language: 'en', timeZone: 'Europe/Berlin' });
	});

	it('lets signed-in users in', async () => {
		const { user, token } = await signedIn(test.context.db);
		const { response, locals } = request('http://shop.example.com/events', { cookie: token });
		const r = await response;
		expect(r.status).toBe(200);
		expect(locals.user).toEqual(user);
		// no HSTS over plain HTTP
		expect(r.headers.get('strict-transport-security')).toBeNull();
	});

	it('warns at startup if UI_DOMAIN is not set', async () => {
		const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
		vi.resetModules();
		// the fresh modules get a context without a domain, not one from the environment
		const fresh = await import('#lib/server/context.ts');
		fresh.useContext({ ...test.context, config: { ...test.context.config, domain: null } });
		await import('../hooks.server.ts');
		expect(warn).toHaveBeenCalledWith(
			'UI_DOMAIN is not set: requests for any host name are answered'
		);
	});

	it('logs unexpected errors only', () => {
		const log = vi.spyOn(console, 'error').mockImplementation(() => {});
		call(hooks.handleError, { kind: 'framework', error: { status: 404 } });
		expect(log).not.toHaveBeenCalled();
		call(hooks.handleError, { kind: 'unknown', error: new Error('bug') });
		expect(log).toHaveBeenCalledWith(new Error('bug'));
	});
});

describe('layout', () => {
	it('passes the user, language and time zone on', () => {
		expect(
			call(layout.load, {
				locals: { user: { id: 1, name: 'admin' }, language: 'en', timeZone: 'UTC' }
			})
		).toEqual({ user: 'admin', language: 'en', timeZone: 'UTC' });
		expect(
			call(layout.load, { locals: { user: null, language: 'en', timeZone: 'UTC' } })
		).toMatchObject({ user: null });
		expect(layout.csr).toBe(false);
	});
});

describe('overview', () => {
	it('shows the last poll, the recent events and the listings in the shop', async () => {
		const health = { status: 'ok', version: '1.6.0', schema_version: 4, last_poll: poll() };
		const { requests } = testContext(
			api({
				'/api/health': [200, health],
				'/api/events': [200, { events: [event()], next: null }],
				'/api/listings': [200, page([])]
			})
		);
		expect(await call(overview.load, {})).toEqual({
			health,
			events: [event()],
			listed: 125
		});
		expect(requests.map((url) => url.pathname + url.search)).toEqual([
			'/api/health',
			'/api/events?limit=10',
			'/api/listings?limit=1'
		]);
	});

	it('asks for nothing else while the history is unavailable', async () => {
		const { requests } = testContext(api({ '/api/health': [503, { error: 'No database' }] }));
		expect(await call(overview.load, {})).toMatchObject({ events: [], listed: null });
		expect(requests).toHaveLength(1);
	});
});

describe('events', () => {
	it('passes only valid filters on and links the pages', async () => {
		const { requests } = testContext(
			api({ '/api/events': [200, { events: [event()], next: 40 }] })
		);
		const url = new URL(
			'https://shop.example.com/events?event=sold_out&event=bogus&q=%20ring%20&listing_id=12&before=50'
		);
		const data = await loaded(events.load, { url });
		expect(requests[0]!.search).toBe('?event=sold_out&q=ring&listing_id=12&before=50&limit=50');
		expect(data.filter).toEqual({ event: ['sold_out'], q: 'ring', listingId: 12 });
		expect(data.older).toBe('?event=sold_out&event=bogus&q=+ring+&listing_id=12&before=40');
		expect(data.newest).toBe('?event=sold_out&event=bogus&q=+ring+&listing_id=12');
	});

	it('has no further pages without a next one', async () => {
		testContext(api({ '/api/events': [200, { events: [], next: null }] }));
		const data = await loaded(events.load, {
			url: new URL('https://shop.example.com/events?listing_id=abc')
		});
		expect([data.older, data.newest, data.filter.listingId]).toEqual([null, null, undefined]);
	});
});

describe('polls', () => {
	it('links the pages', async () => {
		const { requests } = testContext(
			api({ '/api/polls': [200, { polls: [poll()], next: 7 }] })
		);
		const first = await loaded(polls.load, { url: new URL('https://shop.example.com/polls') });
		expect(first).toMatchObject({ older: '?before=7', newest: false });
		const later = await loaded(polls.load, {
			url: new URL('https://shop.example.com/polls?before=9')
		});
		expect(later.newest).toBe(true);
		expect(requests.map((url) => url.search)).toEqual(['?limit=100', '?before=9&limit=100']);
	});

	it('has no further page after the last one', async () => {
		testContext(api({ '/api/polls': [200, { polls: [], next: null }] }));
		const data = await loaded(polls.load, { url: new URL('https://shop.example.com/polls') });
		expect(data.older).toBeNull();
	});

	it('shows an error page if the API fails', async () => {
		testContext(api({ '/api/polls': [500, { error: 'Internal error' }] }));
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const error = await thrown(() =>
			call(polls.load, { url: new URL('https://shop.example.com/polls') })
		);
		expect(isHttpError(error, 503)).toBe(true);
	});
});

describe('listings', () => {
	it('filters by status and title, sorts and keeps both in the tabs', async () => {
		const { requests } = testContext(
			api({ '/api/listings': [200, page([storedListing()], 900)] })
		);
		const data = await loaded(listings.load, {
			url: new URL('https://shop.example.com/listings?status=sold_out&q=ring&sort=price_asc')
		});
		expect(requests[0]!.search).toBe('?status=sold_out&q=ring&sort=price_asc&limit=500');
		expect(data.filter).toEqual({ status: 'sold_out', q: 'ring', sort: 'price_asc' });
		expect(data.counts).toEqual({ listed: 125, missing: 0, sold_out: 3, gone: 2 });
		expect(data.statuses).toContainEqual({
			name: 'gone',
			href: '?status=gone&q=ring&sort=price_asc'
		});
		expect(data.sorts).toContain('stock');
		// more than fit on the page
		expect(data.more).toBe(true);
	});

	it('shows the listings in the shop, the newest first, by default', async () => {
		const { requests } = testContext(api({ '/api/listings': [200, page([])] }));
		const data = await loaded(listings.load, {
			url: new URL('https://shop.example.com/listings?status=bogus&sort=bogus')
		});
		expect(requests[0]!.search).toBe('?status=listed&sort=newest&limit=500');
		expect([data.filter.status, data.filter.sort, data.more]).toEqual([
			'listed',
			'newest',
			false
		]);
		expect(data.statuses[0]).toEqual({ name: 'listed', href: '?status=listed' });
	});
});

describe('listing', () => {
	it('loads a listing with its events', async () => {
		const detail = { listing: storedListing(), events: [event()] };
		testContext(api({ '/api/listings/1001': [200, detail] }));
		expect(await call(listingPage.load, { params: { id: '1001' } })).toEqual(detail);
	});

	it.each(['abc', '0', '1234567890123456'])('answers 404 for the ID %s', async (id) => {
		testContext();
		const error = await thrown(() => call(listingPage.load, { params: { id } }));
		expect(isHttpError(error, 404)).toBe(true);
	});

	it('answers 404 for an unknown listing', async () => {
		testContext(api({ '/api/listings/7': [404, { error: 'unknown' }] }));
		const error = await thrown(() => call(listingPage.load, { params: { id: '7' } }));
		expect(isHttpError(error, 404)).toBe(true);
	});
});

describe('login', () => {
	function submit(fields: Record<string, string>, target = '', address?: string) {
		const jar = cookieJar();
		const result = call(login.actions.default!, {
			cookies: jar.cookies,
			request: new Request('https://shop.example.com/login', {
				method: 'POST',
				body: new URLSearchParams(fields)
			}),
			url: new URL(`https://shop.example.com/login${target}`),
			getClientAddress: () => {
				if (!address) {
					throw new Error('no X-Forwarded-For');
				}
				return address;
			}
		});
		return { jar, result: thrown(() => result).catch(() => result) };
	}

	it('tells whether an account exists', async () => {
		const { context } = testContext();
		const url = new URL('https://shop.example.com/login');
		expect(call(login.load, { locals: { user: null }, url })).toEqual({ noUsers: true });
		await signedIn(context.db);
		expect(call(login.load, { locals: { user: null }, url })).toEqual({ noUsers: false });
	});

	it('sends signed-in users on, but only to local pages', async () => {
		testContext();
		for (const [target, expected] of [
			['?redirectTo=%2Fpolls', '/polls'],
			['?redirectTo=%2F%2Fevil.example', '/'],
			['?redirectTo=https%3A%2F%2Fevil.example', '/'],
			['?redirectTo=%2F%5Cevil.example', '/'],
			['', '/']
		]) {
			const url = new URL(`https://shop.example.com/login${target}`);
			const redirect = await thrown(() =>
				call(login.load, { locals: { user: { id: 1, name: 'admin' } }, url })
			);
			expect(isRedirect(redirect) && redirect.location).toBe(expected);
		}
	});

	it('signs in with a session cookie', async () => {
		const { context } = testContext();
		await signedIn(context.db);
		const { jar, result } = submit(
			{ username: 'admin', password: PASSWORD },
			'?redirectTo=%2Fevents',
			'10.0.0.1'
		);
		const redirect = await result;
		expect(isRedirect(redirect) && [redirect.status, redirect.location]).toEqual([
			303,
			'/events'
		]);
		expect(jar.set).toEqual([
			{
				name: SESSION_COOKIE,
				value: expect.stringMatching(/^[A-Za-z0-9_-]{43}$/),
				options: {
					path: '/',
					httpOnly: true,
					secure: true,
					sameSite: 'lax',
					maxAge: 30 * 86400
				}
			}
		]);
	});

	it('rejects a wrong password, then too many attempts', async () => {
		const { context } = testContext();
		await signedIn(context.db);
		const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
		for (let i = 0; i < 5; i++) {
			const { result } = submit({ username: 'admin\nfake log line', password: 'wrong' });
			expect(await result).toMatchObject({
				status: 400,
				data: { username: 'admin\nfake log line', invalid: true }
			});
		}
		// the user name is logged escaped, so it can't forge log lines
		expect(warn).toHaveBeenCalledWith(
			'Failed sign-in for "admin\\nfake log line" from unknown'
		);
		const { result, jar } = submit({ username: 'admin\nfake log line', password: PASSWORD });
		expect(await result).toMatchObject({ status: 429, data: { minutes: 15 } });
		expect(jar.set).toEqual([]);
	});

	it('copes with a form without fields', async () => {
		testContext();
		vi.spyOn(console, 'warn').mockImplementation(() => {});
		const { result } = submit({}, '', '10.0.0.1');
		expect(await result).toMatchObject({ status: 400, data: { username: '', invalid: true } });
	});
});

describe('logout', () => {
	it('ends the session', async () => {
		const { context } = testContext();
		const { token } = await signedIn(context.db);
		const jar = cookieJar({ [SESSION_COOKIE]: token });
		const redirect = await thrown(() => call(logout.POST, { cookies: jar.cookies }));
		expect(isRedirect(redirect) && redirect.location).toBe('/login');
		expect(jar.deleted).toEqual([SESSION_COOKIE]);
		expect(context.db.prepare('SELECT COUNT(*) AS n FROM sessions').get()).toEqual({ n: 0 });
	});

	it('works without a session too', async () => {
		testContext();
		const jar = cookieJar();
		const redirect = await thrown(() => call(logout.POST, { cookies: jar.cookies }));
		expect(isRedirect(redirect)).toBe(true);
	});
});
