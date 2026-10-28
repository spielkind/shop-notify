/**
 * Headers for every page. The Content-Security-Policy comes from the SvelteKit
 * configuration (vite.config.ts); these don't depend on the reverse proxy in front.
 */
export function securityHeaders(https: boolean): Record<string, string> {
	return {
		'X-Content-Type-Options': 'nosniff',
		'X-Frame-Options': 'DENY',
		'Referrer-Policy': 'same-origin',
		'Cross-Origin-Opener-Policy': 'same-origin',
		'Cross-Origin-Resource-Policy': 'same-origin',
		'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
		// The pages show data of a signed-in user
		'Cache-Control': 'no-store',
		...(https ? { 'Strict-Transport-Security': 'max-age=31536000' } : {})
	};
}
