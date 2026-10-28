import { EVENT_NAMES, type EventName } from '#lib/i18n/types.ts';
import { getContext } from '#lib/server/context.ts';
import { fromApi, positiveInt } from '#lib/server/load.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 50;

export const load: PageServerLoad = async ({ url }) => {
	const params = url.searchParams;
	const filter = {
		event: params
			.getAll('event')
			.filter((name): name is EventName => (EVENT_NAMES as readonly string[]).includes(name)),
		q: (params.get('q') ?? '').trim().slice(0, 200),
		listingId: positiveInt(params.get('listing_id'))
	};
	const page = await fromApi(
		getContext().api.events({
			event: filter.event.join(',') || undefined,
			q: filter.q || undefined,
			listing_id: filter.listingId,
			before: positiveInt(params.get('before')),
			limit: PAGE_SIZE
		})
	);

	let older: string | null = null;
	if (page.next !== null) {
		const next = new URLSearchParams(params);
		next.set('before', String(page.next));
		older = `?${next}`;
	}
	const newest = new URLSearchParams(params);
	newest.delete('before');
	return {
		events: page.events,
		filter,
		older,
		newest: params.has('before') ? `?${newest}` : null
	};
};
