import { describe, expect, it } from 'vitest';
import { en } from './en.ts';
import { isLanguage, LANGUAGES, messages } from './index.ts';
import { COUNT_KEYS, DETAIL_KEYS, EVENT_NAMES, NOTICE_KINDS } from './types.ts';

describe('languages', () => {
	it('knows English', () => {
		expect(isLanguage('en')).toBe(true);
		expect(isLanguage('xx')).toBe(false);
		expect(isLanguage('toString')).toBe(false);
		expect(messages('en')).toBe(en);
	});

	it.each(Object.entries(LANGUAGES))(
		'%s has a text for every event, detail and count',
		(_, t) => {
			expect(Object.keys(t.event).sort()).toEqual([...EVENT_NAMES].sort());
			expect(Object.keys(t.notice).sort()).toEqual([...NOTICE_KINDS].sort());
			expect(Object.keys(t.detail).sort()).toEqual([...DETAIL_KEYS].sort());
			expect(Object.keys(t.count).sort()).toEqual([...COUNT_KEYS].sort());
			expect(() => new Intl.DateTimeFormat(t.locale)).not.toThrow();
		}
	);

	it('fills in numbers', () => {
		expect(en.nav.signedInAs('admin')).toBe('Signed in as admin');
		expect(en.login.tooManyAttempts(1)).toBe(
			'Too many failed attempts. Try again in 1 minute.'
		);
		expect(en.login.tooManyAttempts(15)).toContain('15 minutes');
		expect(en.overview.pollOk(1)).toBe('OK – 1 listing in the shop');
		expect(en.overview.pollOk(124)).toBe('OK – 124 listings in the shop');
		expect(en.overview.pollIncomplete(3, 5)).toContain('3 of 5 listings');
		expect(en.listing.title(42)).toBe('Listing 42');
		expect(en.overview.listingsInShop(1)).toBe('1 listing in the shop');
		expect(en.overview.listingsInShop(125)).toBe('125 listings in the shop');
		expect(en.listings.inStock(3)).toBe('3 in stock');
		expect(en.errors.title(503)).toBe('Error 503');
		expect(COUNT_KEYS.map((key) => en.count[key](2))).toEqual([
			'2 new',
			'2 back',
			'2 missing',
			'2 gone',
			'2 sold out',
			'2 restocked',
			'2 filtered'
		]);
	});
});
