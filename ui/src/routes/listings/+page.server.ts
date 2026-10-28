import { LISTING_SORTS, type ListingSort } from '#lib/server/api.ts';
import { getContext } from '#lib/server/context.ts';
import { fromApi } from '#lib/server/load.ts';
import type { PageServerLoad } from './$types';

/** All listings are shown on one page, so that every order covers them all. */
const MAX_LISTINGS = 500;
const STATUSES = ['listed', 'missing', 'sold_out', 'gone', 'all'] as const;
type Status = (typeof STATUSES)[number];

function oneOf<T extends string>(values: readonly T[], value: string | null, fallback: T): T {
	return (values as readonly (string | null)[]).includes(value) ? (value as T) : fallback;
}

export const load: PageServerLoad = async ({ url }) => {
	const params = url.searchParams;
	const status: Status = oneOf(STATUSES, params.get('status'), 'listed');
	const sort: ListingSort = oneOf(LISTING_SORTS, params.get('sort'), 'newest');
	const q = (params.get('q') ?? '').trim().slice(0, 200);
	const page = await fromApi(
		getContext().api.listings({ status, q: q || undefined, sort, limit: MAX_LISTINGS })
	);

	const link = (name: Status) => {
		const query = new URLSearchParams({ status: name });
		if (q) {
			query.set('q', q);
		}
		if (sort !== 'newest') {
			query.set('sort', sort);
		}
		return `?${query}`;
	};
	return {
		listings: page.listings,
		counts: page.counts,
		more: page.next !== null,
		filter: { status, q, sort },
		sorts: LISTING_SORTS,
		statuses: STATUSES.map((name) => ({ name, href: link(name) }))
	};
};
