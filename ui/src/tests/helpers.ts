/** Test data and a context with an in-memory database and a fake API. */
import type { Event, ListingPage, Poll, StoredListing } from '#lib/api-types.ts';
import { Api } from '#lib/server/api.ts';
import { loadConfig } from '#lib/server/config.ts';
import { useContext, type Context } from '#lib/server/context.ts';
import { openDatabaseFile } from '#lib/server/db.ts';
import { LoginLimiter } from '#lib/server/ratelimit.ts';

export type Answer = [status: number, body: unknown];

/** A context whose API answers with ``answer(url)``; the requested URLs are recorded. */
export function testContext(answer: (url: URL) => Answer = () => [404, { error: 'x' }]) {
	const requests: URL[] = [];
	const fetch = async (input: string | URL | Request) => {
		const url = new URL(String(input));
		requests.push(url);
		const [status, body] = answer(url);
		return new Response(JSON.stringify(body), {
			status,
			headers: { 'Content-Type': 'application/json' }
		});
	};
	const context: Context = {
		config: loadConfig({ UI_DOMAIN: 'shop.example.com', TZ: 'Europe/Berlin' }),
		db: openDatabaseFile(':memory:'),
		api: new Api('http://api:8080', fetch as typeof globalThis.fetch),
		limiter: new LoginLimiter()
	};
	useContext(context);
	return { context, requests };
}

export function poll(changes: Partial<Poll> = {}): Poll {
	return {
		id: 1,
		time: '2026-10-28T11:00:00Z',
		reported: 125,
		received: 125,
		complete: true,
		new: 0,
		back: 0,
		missing: 0,
		gone: 0,
		filtered: 0,
		restocked: 0,
		sold_out: 0,
		error: null,
		...changes
	};
}

export function event(changes: Partial<Event> = {}): Event {
	return {
		id: 1,
		listing_id: 1001,
		time: '2026-10-28T11:00:00Z',
		event: 'new',
		details: {},
		title: 'Silver ring',
		...changes
	};
}

export function storedListing(changes: Partial<StoredListing> = {}, id = 1001): StoredListing {
	return {
		listing: {
			listing_id: id,
			title: `Item ${id}`,
			url: `https://www.etsy.com/listing/${id}/item`,
			last_modified_timestamp: 1_792_000_000,
			price: { amount: 2499, divisor: 100, currency_code: 'EUR' },
			quantity: 3,
			image_url: null
		},
		status: 'listed',
		first_seen: '2026-10-20T10:00:00Z',
		last_seen: '2026-10-28T11:00:00Z',
		missing_since: null,
		gone_since: null,
		sold_out_since: null,
		notified: '2026-10-20T10:01:00Z',
		filtered: false,
		pending: null,
		image_url: `https://i.etsystatic.com/${id}/il_570xN.jpg`,
		thumbnail_url: `https://i.etsystatic.com/${id}/il_170x135.jpg`,
		...changes
	};
}

export function listingPage(listings: StoredListing[], next: number | null = null): ListingPage {
	return { listings, counts: { listed: 125, missing: 0, sold_out: 3, gone: 2 }, next };
}
