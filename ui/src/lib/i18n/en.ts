import type { Messages } from './types.ts';

const plural = (n: number, one: string, other: string) => `${n} ${n === 1 ? one : other}`;

export const en: Messages = {
	locale: 'en-GB',
	appName: 'Shop Notify',

	nav: {
		overview: 'Overview',
		events: 'Events',
		polls: 'Polls',
		logout: 'Sign out',
		signedInAs: (user) => `Signed in as ${user}`
	},

	login: {
		title: 'Sign in',
		username: 'Username',
		password: 'Password',
		submit: 'Sign in',
		invalid: 'Wrong username or password.',
		tooManyAttempts: (minutes) =>
			`Too many failed attempts. Try again in ${plural(minutes, 'minute', 'minutes')}.`,
		noUsers: 'No account has been set up yet. Create one on the server:',
		noUsersCommand: 'docker compose exec ui shop-notify-ui set-password admin'
	},

	overview: {
		title: 'Overview',
		lastPoll: 'Last poll',
		noPollYet: 'Shop Notify hasn’t polled the shop yet.',
		pollOk: (received) => `OK – ${plural(received, 'listing', 'listings')} in the shop`,
		pollIncomplete: (received, reported) =>
			`Incomplete answer from Etsy – ${received} of ${reported} listings`,
		pollFailed: 'Failed',
		recentEvents: 'Recent events',
		allEvents: 'All events'
	},

	events: {
		title: 'Events',
		filter: 'Filter',
		eventType: 'Event',
		allTypes: 'All events',
		search: 'Title contains',
		searchPlaceholder: 'e.g. ring',
		listingId: 'Listing ID',
		apply: 'Apply',
		reset: 'Reset',
		none: 'No events match.',
		older: 'Older events',
		newest: 'Newest events'
	},

	polls: {
		title: 'Polls',
		listings: 'Listings',
		changes: 'Changes',
		result: 'Result',
		complete: 'Complete',
		incomplete: 'Incomplete',
		failed: 'Failed',
		noChanges: 'No changes',
		none: 'No polls recorded.',
		older: 'Older polls',
		newest: 'Newest polls'
	},

	listing: {
		title: (id) => `Listing ${id}`,
		viewOnEtsy: 'View on Etsy',
		price: 'Price',
		quantity: 'Quantity',
		firstSeen: 'First seen',
		lastSeen: 'Last seen',
		missingSince: 'Missing since',
		goneSince: 'Gone since',
		soldOutSince: 'Sold out since',
		notified: 'Notified',
		filteredOut: 'Filtered out',
		pending: 'Notification waiting',
		history: 'History',
		status: {
			listed: 'In the shop',
			missing: 'Missing',
			sold_out: 'Sold out',
			gone: 'Gone'
		}
	},

	columns: {
		time: 'Time',
		listing: 'Listing',
		event: 'Event',
		details: 'Details'
	},

	event: {
		imported: 'Imported',
		new: 'New',
		notified: 'Notified',
		recorded: 'Recorded',
		filtered: 'Filtered out',
		missing: 'Missing',
		gone: 'Gone',
		back: 'Back in the shop',
		restocked: 'Back in stock',
		sold_out: 'Sold out',
		inactive: 'Deactivated',
		expired: 'Expired',
		removed: 'Removed',
		low_stock: 'Low stock',
		price_changed: 'Price changed',
		title_changed: 'Title changed'
	},

	notice: {
		new: 'new',
		restocked: 'back in stock',
		back: 'back in the shop',
		price_drop: 'price drop',
		low_stock: 'low stock',
		price_rise: 'price rise',
		sold_out: 'sold out'
	},

	detail: {
		price: 'price',
		quantity: 'quantity',
		created: 'created',
		renewed: 'renewed',
		active_since: 'active since',
		modified: 'modified',
		old: 'was',
		new: 'now',
		since: 'last seen',
		seconds: 'missing for',
		last_seen: 'last seen',
		sold_out_since: 'sold out since',
		reason: 'reason',
		kind: 'as'
	},

	count: {
		new: (n) => `${n} new`,
		back: (n) => `${n} back`,
		missing: (n) => `${n} missing`,
		gone: (n) => `${n} gone`,
		sold_out: (n) => `${n} sold out`,
		restocked: (n) => `${n} restocked`,
		filtered: (n) => `${n} filtered`
	},

	errors: {
		title: (status) => `Error ${status}`,
		notFound: 'This page doesn’t exist.',
		unavailable:
			'The history is not available right now – Shop Notify may be starting or upgrading its database.',
		unexpected: 'Something went wrong.'
	}
};
