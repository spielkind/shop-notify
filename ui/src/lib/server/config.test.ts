import { describe, expect, it } from 'vitest';
import { ConfigError, loadConfig } from './config.ts';
import { securityHeaders } from './headers.ts';

describe('loadConfig', () => {
	it('has defaults', () => {
		expect(loadConfig({})).toEqual({
			domain: null,
			apiUrl: 'http://127.0.0.1:8080',
			dataDir: 'ui-data',
			sessionDays: 30,
			language: 'en',
			timeZone: 'UTC'
		});
	});

	it('reads the environment', () => {
		expect(
			loadConfig({
				UI_DOMAIN: ' Shop.Example.com ',
				API_URL: 'http://api:8080/',
				UI_DATA_DIR: '/data',
				UI_SESSION_DAYS: '7',
				UI_LANGUAGE: 'en',
				TZ: 'Europe/Berlin'
			})
		).toEqual({
			domain: 'shop.example.com',
			apiUrl: 'http://api:8080',
			dataDir: '/data',
			sessionDays: 7,
			language: 'en',
			timeZone: 'Europe/Berlin'
		});
	});

	it('treats empty values as unset', () => {
		expect(loadConfig({ UI_DOMAIN: '', TZ: ' ' }).domain).toBeNull();
	});

	it('lists every problem', () => {
		const load = () =>
			loadConfig({
				UI_DOMAIN: 'https://shop.example.com',
				API_URL: 'api:8080',
				UI_SESSION_DAYS: '0.5',
				UI_LANGUAGE: 'xx',
				TZ: 'Mars/Olympus'
			});
		expect(load).toThrow(ConfigError);
		expect(load).toThrow(
			[
				'Invalid configuration:',
				"- UI_DOMAIN must be a host name like shop.example.com, not 'https://shop.example.com'",
				"- API_URL must be like http://api:8080, not 'api:8080'",
				"- UI_SESSION_DAYS must be a whole number of days from 1 to 365, not '0.5'",
				"- UI_LANGUAGE 'xx' is not available",
				"- TZ 'Mars/Olympus' is not a known time zone"
			].join('\n')
		);
	});

	it.each(['0', '366', 'x'])('rejects UI_SESSION_DAYS=%s', (days) => {
		expect(() => loadConfig({ UI_SESSION_DAYS: days })).toThrow('UI_SESSION_DAYS');
	});

	it('uses the process environment by default', () => {
		expect(loadConfig().apiUrl).toMatch(/^http/);
	});
});

describe('securityHeaders', () => {
	it('sets HSTS only for HTTPS', () => {
		expect(securityHeaders(true)['Strict-Transport-Security']).toBe('max-age=31536000');
		expect(securityHeaders(false)).not.toHaveProperty('Strict-Transport-Security');
		expect(securityHeaders(false)).toMatchObject({
			'X-Frame-Options': 'DENY',
			'X-Content-Type-Options': 'nosniff',
			'Cache-Control': 'no-store'
		});
	});
});
