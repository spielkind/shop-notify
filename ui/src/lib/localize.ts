import { Formatter } from './format.ts';
import { messages, type Language } from './i18n/index.ts';

/** Texts and formatter for the language and time zone passed down by the root layout. */
export function localize(data: { language: Language; timeZone: string }): Formatter {
	return new Formatter(messages(data.language), data.timeZone);
}
