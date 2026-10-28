import { getContext } from '#lib/server/context.ts';
import { fromApi, positiveInt } from '#lib/server/load.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 100;

export const load: PageServerLoad = async ({ url }) => {
	const before = positiveInt(url.searchParams.get('before'));
	const page = await fromApi(getContext().api.polls({ before, limit: PAGE_SIZE }));
	return {
		polls: page.polls,
		older: page.next === null ? null : `?before=${page.next}`,
		newest: before !== undefined
	};
};
