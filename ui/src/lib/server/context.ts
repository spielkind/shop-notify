import type { DatabaseSync } from 'node:sqlite';
import { Api } from './api.ts';
import { loadConfig, type Config } from './config.ts';
import { openDatabase } from './db.ts';
import { LoginLimiter } from './ratelimit.ts';

export interface Context {
	config: Config;
	db: DatabaseSync;
	api: Api;
	limiter: LoginLimiter;
}

let context: Context | undefined;

/** Configuration, database, API client and rate limiter of this process, created once. */
export function getContext(env: Record<string, string | undefined> = process.env): Context {
	if (!context) {
		const config = loadConfig(env);
		context = {
			config,
			db: openDatabase(config.dataDir),
			api: new Api(config.apiUrl),
			limiter: new LoginLimiter()
		};
	}
	return context;
}

/** For tests: forget the context, so that the next call creates a new one. */
export function resetContext(): void {
	context?.db.close();
	context = undefined;
}
