import { getContext } from '#lib/server/context.ts';
import { fromApi } from '#lib/server/load.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	const { api } = getContext();
	const health = await fromApi(api.health());
	if (health.status !== 'ok') {
		return { health, events: [], listed: null };
	}
	const [events, listings] = await Promise.all([
		fromApi(api.events({ limit: 10 })),
		fromApi(api.listings({ limit: 1 }))
	]);
	return { health, events: events.events, listed: listings.counts.listed };
};
