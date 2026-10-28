import { error } from '@sveltejs/kit';
import { getContext } from '#lib/server/context.ts';
import { fromApi, positiveInt } from '#lib/server/load.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	const id = positiveInt(params.id);
	if (id === undefined) {
		error(404, 'Not found');
	}
	return await fromApi(getContext().api.listing(id));
};
