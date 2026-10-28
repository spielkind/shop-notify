/**
 * All texts of the UI. Every language implements this interface completely, so the
 * type check fails if a translation is missing (see docs/web-ui.md, "Languages").
 */
export interface Messages {
	/** BCP 47 locale for dates and numbers, e.g. "en-GB" or "de-DE". */
	locale: string;
	appName: string;

	nav: {
		overview: string;
		listings: string;
		events: string;
		polls: string;
		logout: string;
		signedInAs: (user: string) => string;
	};

	login: {
		title: string;
		username: string;
		password: string;
		submit: string;
		invalid: string;
		tooManyAttempts: (minutes: number) => string;
		noUsers: string;
		noUsersCommand: string;
	};

	overview: {
		title: string;
		lastPoll: string;
		noPollYet: string;
		pollOk: (received: number) => string;
		pollIncomplete: (received: number, reported: number) => string;
		pollFailed: string;
		listingsInShop: (n: number) => string;
		recentEvents: string;
		allEvents: string;
	};

	listings: {
		title: string;
		status: string;
		all: string;
		search: string;
		apply: string;
		none: string;
		older: string;
		newest: string;
		inStock: (n: number) => string;
		noImage: string;
	};

	events: {
		title: string;
		filter: string;
		eventType: string;
		allTypes: string;
		search: string;
		searchPlaceholder: string;
		listingId: string;
		apply: string;
		reset: string;
		none: string;
		older: string;
		newest: string;
	};

	polls: {
		title: string;
		listings: string;
		changes: string;
		result: string;
		complete: string;
		incomplete: string;
		failed: string;
		noChanges: string;
		none: string;
		older: string;
		newest: string;
	};

	listing: {
		title: (id: number) => string;
		viewOnEtsy: string;
		price: string;
		quantity: string;
		firstSeen: string;
		lastSeen: string;
		missingSince: string;
		goneSince: string;
		soldOutSince: string;
		notified: string;
		filteredOut: string;
		pending: string;
		history: string;
		status: Record<'listed' | 'missing' | 'sold_out' | 'gone', string>;
	};

	columns: {
		time: string;
		listing: string;
		event: string;
		details: string;
	};

	/** Names of the recorded events (``listing_events.event``). */
	event: Record<EventName, string>;
	/** Names of the notified events (``notified`` details, ``pending``). */
	notice: Record<NoticeKind, string>;
	/** Labels of the details of an event, by key. */
	detail: Record<DetailKey, string>;
	/** Counts of a poll, e.g. "3 new". */
	count: Record<CountKey, (n: number) => string>;

	errors: {
		title: (status: number) => string;
		notFound: string;
		unavailable: string;
		unexpected: string;
	};
}

export const EVENT_NAMES = [
	'imported',
	'new',
	'notified',
	'recorded',
	'filtered',
	'missing',
	'gone',
	'back',
	'restocked',
	'sold_out',
	'inactive',
	'expired',
	'removed',
	'low_stock',
	'price_changed',
	'title_changed'
] as const;
export type EventName = (typeof EVENT_NAMES)[number];

export const NOTICE_KINDS = [
	'new',
	'restocked',
	'back',
	'price_drop',
	'low_stock',
	'price_rise',
	'sold_out'
] as const;
export type NoticeKind = (typeof NOTICE_KINDS)[number];

export const DETAIL_KEYS = [
	'price',
	'quantity',
	'created',
	'renewed',
	'active_since',
	'modified',
	'old',
	'new',
	'since',
	'seconds',
	'last_seen',
	'sold_out_since',
	'reason',
	'kind'
] as const;
export type DetailKey = (typeof DETAIL_KEYS)[number];

export const COUNT_KEYS = [
	'new',
	'back',
	'missing',
	'gone',
	'sold_out',
	'restocked',
	'filtered'
] as const;
export type CountKey = (typeof COUNT_KEYS)[number];
