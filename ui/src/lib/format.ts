import type { Event, Money, Poll } from './api-types.ts';
import type { Messages } from './i18n/types.ts';
import { COUNT_KEYS, DETAIL_KEYS, EVENT_NAMES, NOTICE_KINDS } from './i18n/types.ts';

/** Details that hold a Unix timestamp. */
const TIMESTAMPS = new Set([
	'created',
	'renewed',
	'active_since',
	'modified',
	'since',
	'last_seen',
	'sold_out_since'
]);

function isOneOf<T extends string>(list: readonly T[], value: string): value is T {
	return (list as readonly string[]).includes(value);
}

/** Formats values for one language and time zone. */
export class Formatter {
	readonly t: Messages;
	readonly #dateTime: Intl.DateTimeFormat;

	constructor(t: Messages, timeZone: string) {
		this.t = t;
		this.#dateTime = new Intl.DateTimeFormat(t.locale, {
			dateStyle: 'medium',
			timeStyle: 'short',
			timeZone
		});
	}

	/** An ISO 8601 time from the API, or seconds since the epoch. */
	dateTime(value: string | number): string {
		return this.#dateTime.format(typeof value === 'number' ? value * 1000 : Date.parse(value));
	}

	money(money: Money): string {
		const value = money.amount / (money.divisor || 1);
		try {
			return new Intl.NumberFormat(this.t.locale, {
				style: 'currency',
				currency: money.currency_code
			}).format(value);
		} catch {
			return `${value.toFixed(2)} ${money.currency_code}`;
		}
	}

	duration(seconds: number): string {
		const [value, unit] =
			seconds < 3600
				? [seconds / 60, 'minute']
				: seconds < 2 * 86400
					? [seconds / 3600, 'hour']
					: [seconds / 86400, 'day'];
		return new Intl.NumberFormat(this.t.locale, {
			style: 'unit',
			unit,
			unitDisplay: 'long',
			maximumFractionDigits: value < 10 ? 1 : 0
		}).format(value);
	}

	eventName(name: string): string {
		return isOneOf(EVENT_NAMES, name) ? this.t.event[name] : name;
	}

	notice(kind: string): string {
		return isOneOf(NOTICE_KINDS, kind) ? this.t.notice[kind] : kind;
	}

	/** What changed in a poll, e.g. "2 new, 1 sold out"; empty if nothing. */
	pollChanges(poll: Poll): string {
		return COUNT_KEYS.filter((key) => poll[key] > 0)
			.map((key) => this.t.count[key](poll[key]))
			.join(', ');
	}

	/** The details of an event as one line, e.g. "was: 24.99 USD, now: 19.99 USD". */
	details(event: Pick<Event, 'details'>): string {
		return Object.entries(event.details)
			.filter(([, value]) => value !== null && value !== '')
			.map(([key, value]) => {
				const label = isOneOf(DETAIL_KEYS, key) ? this.t.detail[key] : key;
				return `${label}: ${this.#detailValue(key, value)}`;
			})
			.join(', ');
	}

	#detailValue(key: string, value: unknown): string {
		if (typeof value === 'number' && TIMESTAMPS.has(key)) {
			return this.dateTime(value);
		}
		if (typeof value === 'number' && key === 'seconds') {
			return this.duration(value);
		}
		if (typeof value === 'string' && key === 'kind') {
			return this.notice(value);
		}
		return typeof value === 'string' ? value : JSON.stringify(value);
	}
}
