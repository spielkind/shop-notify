import { createRawSnippet } from 'svelte';
import { render } from 'svelte/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ErrorPage from '../routes/+error.svelte';
import Layout from '../routes/+layout.svelte';
import Overview from '../routes/+page.svelte';
import Events from '../routes/events/+page.svelte';
import Listing from '../routes/listings/[id]/+page.svelte';
import Listings from '../routes/listings/+page.svelte';
import Login from '../routes/login/+page.svelte';
import Polls from '../routes/polls/+page.svelte';
import { event, listingPage, poll, storedListing } from './helpers.ts';

const state = vi.hoisted(() => ({
	page: { url: new URL('https://shop.example.com/'), status: 200, data: {} as object }
}));
vi.mock('$app/state', () => state);

const base = { user: 'admin', language: 'en' as const, timeZone: 'Europe/Berlin' };
const XSS = '<script>alert(1)</script>';

/** The HTML of a page for ``data`` (and ``form``). */
function html(component: unknown, data: object, form: object | null = null): string {
	const props = { data: { ...base, ...data }, form, params: {} };
	return render(component as never, { props } as never).body;
}

beforeEach(() => {
	state.page = { url: new URL('https://shop.example.com/'), status: 200, data: {} };
});

describe('layout', () => {
	const children = createRawSnippet(() => ({ render: () => '<p>content</p>' }));

	it('shows the navigation to signed-in users', () => {
		state.page.url = new URL('https://shop.example.com/listings');
		const body = render(Layout as never, { props: { data: base, children } } as never).body;
		expect(body).toContain('<a href="/listings" aria-current="page">Listings</a>');
		expect(body).toContain('<a href="/events">Events</a>');
		expect(body).toContain('Signed in as admin');
		expect(body).toContain('<form method="POST" action="/logout">');
		expect(body).toContain('<p>content</p>');
	});

	it('shows only the content to visitors', () => {
		const data = { ...base, user: null };
		const body = render(Layout as never, { props: { data, children } } as never).body;
		expect(body).not.toContain('<nav');
		expect(body).toContain('<p>content</p>');
	});
});

describe('overview', () => {
	const ok = { status: 'ok', version: '1.6.0', schema_version: 4 };

	it('shows the last poll, the listings in the shop and the recent events', () => {
		const body = html(Overview, {
			health: { ...ok, last_poll: poll({ new: 2, sold_out: 1 }) },
			events: [event({ title: XSS })],
			listed: 125
		});
		expect(body).toContain('28 Oct 2026, 12:00');
		expect(body).toContain('OK – 125 listings in the shop');
		expect(body).toContain('(2 new, 1 sold out)');
		expect(body).toContain('<a href="/listings">125 listings in the shop →</a>');
		// listing titles are escaped
		expect(body).toContain('&lt;script>alert(1)&lt;/script>');
		expect(body).not.toContain(XSS);
	});

	it.each([
		[poll({ error: 'Etsy is down' }), 'Failed: Etsy is down'],
		[
			poll({ complete: false, received: 3, reported: 5 }),
			'Incomplete answer from Etsy – 3 of 5'
		],
		[poll({ complete: false, received: null, reported: null }), 'Etsy – 0 of 0 listings'],
		[poll({ received: null }), 'OK – 0 listings in the shop'],
		[null, 'hasn’t polled the shop yet']
	])('shows the state of the last poll %#', (lastPoll, text) => {
		const body = html(Overview, {
			health: { ...ok, last_poll: lastPoll },
			events: [],
			listed: 0
		});
		expect(body).toContain(text);
		expect(body).toContain('No events match.');
	});

	it('says when the history is unavailable', () => {
		const body = html(Overview, {
			health: { ...ok, status: 'unavailable', last_poll: null },
			events: [],
			listed: null
		});
		expect(body).toContain('The history is not available right now');
		expect(body).not.toContain('Last poll');
	});
});

describe('events', () => {
	const data = {
		events: [
			event({ event: 'price_changed', details: { old: '24.99 USD', new: '19.99 USD' } })
		],
		filter: { event: ['sold_out'], q: 'ring', listingId: 12 },
		older: '?before=40',
		newest: '?q=ring'
	};

	it('shows the events with the filter and links to more', () => {
		const body = html(Events, data);
		expect(body).toContain('<a href="/listings/1001">Silver ring</a>');
		expect(body).toContain('Price changed');
		expect(body).toContain('was: 24.99 USD, now: 19.99 USD');
		expect(body).toMatch(/value="sold_out" checked/);
		expect(body).toMatch(/value="new"(?! checked)/);
		expect(body).toContain('value="ring"');
		expect(body).toContain('value="12"');
		expect(body).toContain('<a href="?before=40">Older events →</a>');
		expect(body).toContain('<a href="?q=ring">← Newest events</a>');
	});

	it('says when nothing matches', () => {
		const body = html(Events, {
			...data,
			events: [],
			filter: { event: [], q: '' },
			older: null,
			newest: null
		});
		expect(body).toContain('No events match.');
		expect(body).not.toContain('Older events');
	});
});

describe('polls', () => {
	it('shows each poll with its result', () => {
		const body = html(Polls, {
			polls: [
				poll({ id: 3, error: 'Etsy is down', received: null, reported: null }),
				poll({ id: 2, complete: false, received: 3, reported: 5 }),
				poll({ id: 1, new: 1 })
			],
			older: '?before=1',
			newest: true
		});
		expect(body).toContain('data-result="failed">Failed</span>');
		expect(body).toContain('Etsy is down');
		expect(body).toContain('data-result="incomplete">Incomplete</span>');
		expect(body).toContain('3/5');
		expect(body).toContain('1 new');
		expect(body).toContain('No changes');
		expect(body).toContain('<a href="/polls">← Newest polls</a>');
		expect(body).toContain('<a href="?before=1">Older polls →</a>');
	});

	it('says when there are none', () => {
		const body = html(Polls, { polls: [], older: null, newest: false });
		expect(body).toContain('No polls recorded.');
	});
});

describe('listings', () => {
	const sorts = ['newest', 'oldest', 'price_asc', 'price_desc', 'title', 'changed', 'stock'];
	const statuses = ['listed', 'missing', 'sold_out', 'gone', 'all'].map((name) => ({
		name,
		href: `?status=${name}`
	}));

	it('shows the listings as cards with their images', () => {
		const body = html(Listings, {
			...listingPage([
				storedListing(),
				storedListing({ status: 'sold_out', thumbnail_url: null }, 1002),
				storedListing(
					{ image_url: 'https://evil.example/x.jpg', status: 'archived' },
					1003
				),
				storedListing(
					{
						listing: {
							...storedListing().listing,
							listing_id: 1004,
							title: XSS,
							price: null,
							quantity: null
						}
					},
					1004
				)
			]),
			filter: { status: 'all', q: '', sort: 'price_asc' },
			sorts,
			statuses,
			more: true
		});
		// thumbnails with the large image for wide screens, loaded lazily from Etsy only
		expect(body).toContain(
			'srcset="https://i.etsystatic.com/1001/il_170x135.jpg 170w, https://i.etsystatic.com/1001/il_570xN.jpg 570w"'
		);
		expect(body).toContain('loading="lazy"');
		expect(body).toContain('src="https://i.etsystatic.com/1002/il_570xN.jpg"');
		expect(body).not.toContain('evil.example');
		expect(body).toContain('No image');
		expect(body).toContain('€24.99');
		expect(body).toContain('3 in stock');
		expect(body).toContain('<a href="/listings/1002">');
		expect(body).toContain('data-status="sold_out">Sold out</span>');
		// a status the UI doesn't know yet is shown as it is
		expect(body).toContain('data-status="archived">archived</span>');
		expect(body).not.toContain(XSS);
		// the tabs with the counts; "all" is the current one
		expect(body).toContain('In the shop <span class="muted">125</span>');
		expect(body).toMatch(/aria-current="page">All <span class="muted">130<\/span>/);
		// the order can be chosen
		expect(body).toContain('<option value="price_asc" selected="">Lowest price</option>');
		expect(body).toContain('<option value="stock">Most in stock</option>');
		expect(body).toContain('Only the first 4 are shown – search to narrow them down.');
	});

	it('says when nothing matches', () => {
		const body = html(Listings, {
			...listingPage([]),
			filter: { status: 'gone', q: 'x', sort: 'newest' },
			sorts,
			statuses,
			more: false
		});
		expect(body).toContain('No listings match.');
		expect(body).not.toContain('Only the first');
		expect(body).toContain('name="status" value="gone"');
	});
});

describe('listing', () => {
	it('shows the listing, its state and its history', () => {
		const body = html(Listing, {
			listing: storedListing({
				status: 'sold_out',
				missing_since: '2026-10-27T08:00:00Z',
				sold_out_since: '2026-10-27T08:01:00Z',
				pending: 'restocked'
			}),
			events: [event({ event: 'sold_out' })]
		});
		expect(body).toContain('<h1>Item 1001</h1>');
		expect(body).toContain('src="https://i.etsystatic.com/1001/il_570xN.jpg"');
		expect(body).toContain(
			'href="https://www.etsy.com/listing/1001/item" rel="noopener noreferrer"'
		);
		expect(body).toContain('data-status="sold_out">Sold out</span>');
		expect(body).toContain('<dt>Sold out since</dt>');
		expect(body).toContain('<dt>Notified</dt>');
		expect(body).toContain('<dd>back in stock</dd>');
		expect(body).toContain('<dd>€24.99</dd>');
		expect(body).toContain('<dd>3</dd>');
	});

	it('leaves out unsafe links and images', () => {
		const unsafe = storedListing({
			image_url: 'http://i.etsystatic.com/1.jpg',
			filtered: true
		});
		unsafe.listing = {
			...unsafe.listing,
			url: 'javascript:alert(1)',
			price: null,
			quantity: null
		};
		const body = html(Listing, { listing: { ...unsafe, status: 'unknown' }, events: [] });
		expect(body).not.toContain('<img');
		expect(body).not.toContain('javascript:');
		expect(body).not.toContain('View on Etsy');
		expect(body).toContain('<dt>Filtered out</dt>');
		expect(body).toContain('data-status="unknown">unknown</span>');
		expect(body).not.toContain('<dt>Price</dt>');
	});
});

describe('login', () => {
	it('explains how to create the first account', () => {
		const body = html(Login, { user: null, noUsers: true });
		expect(body).toContain('No account has been set up yet');
		expect(body).toContain('shop-notify-ui set-password admin');
		expect(body).not.toContain('<form');
	});

	it('shows an empty form at first', () => {
		const body = html(Login, { user: null, noUsers: false });
		expect(body).toMatch(/name="username"[^>]* value=""/);
		expect(body).not.toContain('role="alert"');
	});

	it('shows the form, keeping the username', () => {
		const body = html(Login, { user: null, noUsers: false }, { username: XSS, invalid: true });
		expect(body).toContain('<form method="POST">');
		expect(body).toContain('Wrong username or password.');
		expect(body).not.toContain(XSS);
		expect(body).toContain('autocomplete="current-password"');
	});

	it('says how long to wait after too many attempts', () => {
		const body = html(
			Login,
			{ user: null, noUsers: false },
			{ username: 'admin', minutes: 15 }
		);
		expect(body).toContain('Try again in 15 minutes.');
	});
});

describe('error page', () => {
	it.each([
		[404, 'This page doesn’t exist.'],
		[503, 'The history is not available right now'],
		[500, 'Something went wrong.']
	])('explains error %i', (status, text) => {
		state.page = { ...state.page, status, data: { language: 'en' } };
		const body = render(ErrorPage as never, {} as never).body;
		expect(body).toContain(`<h1>Error ${status}</h1>`);
		expect(body).toContain(text);
	});

	it('falls back to English without layout data', () => {
		state.page = { ...state.page, status: 404, data: {} };
		expect(render(ErrorPage as never, {} as never).body).toContain('This page doesn’t exist.');
	});
});
