import { describe, expect, it } from 'vitest';
import { Formatter } from './format.ts';
import { en } from './i18n/en.ts';
import { safeUrl } from './links.ts';
import { localize } from './localize.ts';

const f = new Formatter(en, 'Europe/Berlin');

describe('Formatter', () => {
	it('formats times in the configured time zone', () => {
		expect(f.dateTime('2026-10-28T11:00:00Z')).toBe('28 Oct 2026, 12:00');
		expect(f.dateTime(1_792_000_000)).toBe(f.dateTime('2026-10-14T17:46:40Z'));
		expect(new Formatter(en, 'UTC').dateTime('2026-10-28T11:00:00Z')).toBe(
			'28 Oct 2026, 11:00'
		);
	});

	it('formats prices', () => {
		expect(f.money({ amount: 2499, divisor: 100, currency_code: 'EUR' })).toBe('€24.99');
		expect(f.money({ amount: 5, divisor: 0, currency_code: 'USD' })).toBe('US$5.00');
		// an unknown currency code still shows the amount
		expect(f.money({ amount: 1250, divisor: 100, currency_code: 'XX' })).toBe('12.50 XX');
	});

	it('formats durations in minutes, hours or days', () => {
		expect(f.duration(90)).toBe('1.5 minutes');
		expect(f.duration(5400)).toBe('1.5 hours');
		expect(f.duration(30 * 3600)).toBe('30 hours');
		expect(f.duration(12 * 86400)).toBe('12 days');
	});

	it('names events and notices, and keeps unknown names', () => {
		expect(f.eventName('sold_out')).toBe('Sold out');
		expect(f.eventName('draft')).toBe('draft');
		expect(f.notice('price_drop')).toBe('price drop');
		expect(f.notice('other')).toBe('other');
	});

	it('describes the details of an event', () => {
		expect(f.details({ details: {} })).toBe('');
		expect(f.details({ details: { old: '24.99 USD', new: '19.99 USD' } })).toBe(
			'was: 24.99 USD, now: 19.99 USD'
		);
		expect(f.details({ details: { since: 1_792_000_000, seconds: 7200 } })).toBe(
			'last seen: 14 Oct 2026, 19:46, missing for: 2 hours'
		);
		expect(f.details({ details: { kind: 'restocked', quantity: 1, price: null } })).toBe(
			'as: back in stock, quantity: 1'
		);
		expect(f.details({ details: { future: { a: 1 }, reason: '' } })).toBe('future: {"a":1}');
	});

	it('summarizes the changes of a poll', () => {
		const poll = {
			id: 1,
			time: '2026-10-28T11:00:00Z',
			reported: 3,
			received: 3,
			complete: true,
			new: 2,
			back: 0,
			missing: 0,
			gone: 0,
			filtered: 1,
			restocked: 0,
			sold_out: 1,
			error: null
		};
		expect(f.pollChanges(poll)).toBe('2 new, 1 sold out, 1 filtered');
		expect(f.pollChanges({ ...poll, new: 0, filtered: 0, sold_out: 0 })).toBe('');
	});
});

describe('localize', () => {
	it('uses the language and time zone of the layout data', () => {
		const formatter = localize({ language: 'en', timeZone: 'UTC' });
		expect(formatter.t).toBe(en);
		expect(formatter.dateTime('2026-10-28T11:00:00Z')).toBe('28 Oct 2026, 11:00');
	});
});

describe('safeUrl', () => {
	it.each([
		['https://www.etsy.com/listing/1/x', 'https://www.etsy.com/listing/1/x'],
		['https://i.etsystatic.com/1/a.jpg', 'https://i.etsystatic.com/1/a.jpg'],
		['https://etsy.com/', 'https://etsy.com/'],
		['http://www.etsy.com/listing/1', null],
		['javascript:alert(1)', null],
		['https://evil.example/etsy.com', null],
		['https://notetsy.com/', null],
		['https://etsy.com.evil.example/', null],
		['not a url', null],
		['', null],
		[null, null],
		[undefined, null]
	])('%s -> %s', (url, expected) => {
		expect(safeUrl(url)).toBe(expected);
	});

	it('takes other hosts', () => {
		expect(safeUrl('https://example.org/a', ['example.org'])).toBe('https://example.org/a');
	});
});
