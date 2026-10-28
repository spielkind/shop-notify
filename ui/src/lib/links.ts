/** Hosts that listing URLs and images may point to. */
const ETSY_HOSTS = ['etsy.com', 'etsystatic.com'];

/**
 * ``url`` if it is an HTTPS URL on one of ``hosts`` (or their subdomains), else null.
 *
 * Listing URLs and images come from the Etsy API and are not trusted: this keeps out
 * e.g. ``javascript:`` URLs and images from elsewhere (also blocked by the CSP).
 */
export function safeUrl(url: string | null | undefined, hosts = ETSY_HOSTS): string | null {
	if (!url) {
		return null;
	}
	let parsed: URL;
	try {
		parsed = new URL(url);
	} catch {
		return null;
	}
	const allowed = hosts.some(
		(host) => parsed.hostname === host || parsed.hostname.endsWith(`.${host}`)
	);
	return parsed.protocol === 'https:' && allowed ? parsed.href : null;
}
