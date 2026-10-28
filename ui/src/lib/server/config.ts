import { isLanguage, type Language } from '../i18n/index.ts';

export interface Config {
	/** Public host name of the UI (UI_DOMAIN); requests for other hosts are rejected. */
	domain: string | null;
	/** Base URL of the read-only API (API_URL). */
	apiUrl: string;
	/** Directory of the UI's own database with the accounts and sessions (UI_DATA_DIR). */
	dataDir: string;
	/** Days a sign-in is valid (UI_SESSION_DAYS). */
	sessionDays: number;
	language: Language;
	/** Time zone of the times shown (TZ). */
	timeZone: string;
}

export class ConfigError extends Error {}

const HOST_NAME =
	/^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/i;

function isTimeZone(value: string): boolean {
	try {
		new Intl.DateTimeFormat('en', { timeZone: value });
		return true;
	} catch {
		return false;
	}
}

/** The settings from the environment; throws ``ConfigError`` listing every problem. */
export function loadConfig(env: Record<string, string | undefined> = process.env): Config {
	const problems: string[] = [];
	const value = (name: string) => env[name]?.trim() || undefined;

	const domain = value('UI_DOMAIN') ?? null;
	if (domain !== null && !HOST_NAME.test(domain)) {
		problems.push(`UI_DOMAIN must be a host name like shop.example.com, not '${domain}'`);
	}

	const apiUrl = value('API_URL') ?? 'http://127.0.0.1:8080';
	if (!/^https?:\/\/[^/]+\/?$/.test(apiUrl)) {
		problems.push(`API_URL must be like http://api:8080, not '${apiUrl}'`);
	}

	const days = value('UI_SESSION_DAYS') ?? '30';
	const sessionDays = Number(days);
	if (!Number.isInteger(sessionDays) || sessionDays < 1 || sessionDays > 365) {
		problems.push(
			`UI_SESSION_DAYS must be a whole number of days from 1 to 365, not '${days}'`
		);
	}

	const language = value('UI_LANGUAGE') ?? 'en';
	if (!isLanguage(language)) {
		problems.push(`UI_LANGUAGE '${language}' is not available`);
	}

	const timeZone = value('TZ') ?? 'UTC';
	if (!isTimeZone(timeZone)) {
		problems.push(`TZ '${timeZone}' is not a known time zone`);
	}

	if (problems.length) {
		throw new ConfigError(`Invalid configuration:\n- ${problems.join('\n- ')}`);
	}
	return {
		domain: domain?.toLowerCase() ?? null,
		apiUrl: apiUrl.replace(/\/$/, ''),
		dataDir: value('UI_DATA_DIR') ?? 'ui-data',
		sessionDays,
		language: language as Language,
		timeZone
	};
}
