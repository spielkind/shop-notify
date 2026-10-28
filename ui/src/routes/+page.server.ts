import { getContext } from '#lib/server/context.ts';
import { fromApi } from '#lib/server/load.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	const { api } = getContext();
	const health = await fromApi(api.health());
	const events = health.status === 'ok' ? await fromApi(api.events({ limit: 10 })) : null;
	return { health, events: events?.events ?? [] };
};
