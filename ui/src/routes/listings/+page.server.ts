import type { ListingQuery } from '#lib/server/api.ts';
import { getContext } from '#lib/server/context.ts';
import { fromApi, positiveInt } from '#lib/server/load.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 60;
const STATUSES = ['listed', 'missing', 'sold_out', 'gone', 'all'] as const;
type Status = (typeof STATUSES)[number];

function isStatus(value: string | null): value is Status {
	return (STATUSES as readonly (string | null)[]).includes(value);
}

export const load: PageServerLoad = async ({ url }) => {
	const params = url.searchParams;
	const value = params.get('status');
	const status: Status = isStatus(value) ? value : 'listed';
	const q = (params.get('q') ?? '').trim().slice(0, 200);
	const before = positiveInt(params.get('before'));
	const query: ListingQuery = { status, q: q || undefined, before, limit: PAGE_SIZE };
	const page = await fromApi(getContext().api.listings(query));

	const link = (changes: Record<string, string | null>) => {
		const next = new URLSearchParams({ status, ...(q ? { q } : {}) });
		for (const [key, change] of Object.entries(changes)) {
			if (change === null) {
				next.delete(key);
			} else {
				next.set(key, change);
			}
		}
		return `?${next}`;
	};
	return {
		listings: page.listings,
		counts: page.counts,
		filter: { status, q },
		statuses: STATUSES.map((name) => ({ name, href: link({ status: name, before: null }) })),
		older: page.next === null ? null : link({ before: String(page.next) }),
		newest: before === undefined ? null : link({ before: null })
	};
};
