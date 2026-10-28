// Generated from docs/schema/api.schema.json by 'npm run api-types' - don't edit

/**
 * Responses of the read-only API, schema version 5
 */
export interface ShopNotifyAPI {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ShopNotifyAPI`'s JSON-Schema
 * via the `definition` "Error".
 */
export interface Error {
  error: string;
}
/**
 * This interface was referenced by `ShopNotifyAPI`'s JSON-Schema
 * via the `definition` "Event".
 */
export interface Event {
  id: number;
  listing_id: number;
  time: string;
  event: string;
  details: {
    [k: string]: unknown;
  };
  title: string;
}
/**
 * This interface was referenced by `ShopNotifyAPI`'s JSON-Schema
 * via the `definition` "EventPage".
 */
export interface EventPage {
  events: Event[];
  next: number | null;
}
/**
 * This interface was referenced by `ShopNotifyAPI`'s JSON-Schema
 * via the `definition` "Health".
 */
export interface Health {
  status: string;
  version: string;
  schema_version: number;
  last_poll: Poll | null;
  error?: string | null;
}
/**
 * This interface was referenced by `ShopNotifyAPI`'s JSON-Schema
 * via the `definition` "Poll".
 */
export interface Poll {
  id: number;
  time: string;
  reported: number | null;
  received: number | null;
  complete: boolean | null;
  new: number;
  back: number;
  missing: number;
  gone: number;
  filtered: number;
  restocked: number;
  sold_out: number;
  error: string | null;
}
/**
 * Subset of an Etsy v3 ShopListing; only these fields are stored locally.
 *
 * This interface was referenced by `ShopNotifyAPI`'s JSON-Schema
 * via the `definition` "Listing".
 */
export interface Listing {
  listing_id: number;
  title: string;
  url: string;
  last_modified_timestamp: number;
  state_timestamp?: number | null;
  created_timestamp?: number | null;
  original_creation_timestamp?: number | null;
  price?: Money | null;
  quantity?: number | null;
  image_url?: string | null;
}
/**
 * This interface was referenced by `ShopNotifyAPI`'s JSON-Schema
 * via the `definition` "Money".
 */
export interface Money {
  amount: number;
  divisor: number;
  currency_code: string;
}
/**
 * Number of listings per status.
 *
 * This interface was referenced by `ShopNotifyAPI`'s JSON-Schema
 * via the `definition` "ListingCounts".
 */
export interface ListingCounts {
  listed: number;
  missing: number;
  sold_out: number;
  gone: number;
}
/**
 * This interface was referenced by `ShopNotifyAPI`'s JSON-Schema
 * via the `definition` "ListingDetail".
 */
export interface ListingDetail {
  listing: StoredListing;
  events: Event[];
}
/**
 * The latest state of a listing as recorded in the database.
 *
 * This interface was referenced by `ShopNotifyAPI`'s JSON-Schema
 * via the `definition` "StoredListing".
 */
export interface StoredListing {
  listing: Listing;
  status: string;
  first_seen: string;
  last_seen: string;
  missing_since: string | null;
  gone_since: string | null;
  sold_out_since: string | null;
  notified: string | null;
  filtered: boolean;
  pending: string | null;
  image_url: string | null;
  thumbnail_url: string | null;
}
/**
 * This interface was referenced by `ShopNotifyAPI`'s JSON-Schema
 * via the `definition` "ListingPage".
 */
export interface ListingPage {
  listings: StoredListing[];
  counts: ListingCounts;
  next: number | null;
}
/**
 * This interface was referenced by `ShopNotifyAPI`'s JSON-Schema
 * via the `definition` "PollPage".
 */
export interface PollPage {
  polls: Poll[];
  next: number | null;
}
