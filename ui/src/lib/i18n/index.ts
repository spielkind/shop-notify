import { en } from './en.ts';
import type { Messages } from './types.ts';

export type { Messages } from './types.ts';

/** The available languages; add a new one here after translating ``en.ts``. */
export const LANGUAGES = { en } satisfies Record<string, Messages>;

export type Language = keyof typeof LANGUAGES;

export function isLanguage(value: string): value is Language {
	return Object.hasOwn(LANGUAGES, value);
}

export function messages(language: Language): Messages {
	return LANGUAGES[language];
}
