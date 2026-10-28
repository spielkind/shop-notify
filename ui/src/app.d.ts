// See https://svelte.dev/docs/kit/types#app.d.ts
import type { Language } from '#lib/i18n/index.ts';
import type { User } from '#lib/server/users.ts';

declare global {
	namespace App {
		interface Locals {
			user: User | null;
			language: Language;
			timeZone: string;
		}
	}
}

export {};
