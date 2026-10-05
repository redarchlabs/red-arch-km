/**
 * Load every page of a paginated admin list (`{ items, total }` per page).
 *
 * Admin pickers (API-key assignments, …) need the whole list, but the API caps a
 * page at 200 rows. Pages are fetched until `total` is reached, an empty page
 * comes back, or `maxPages` is hit — `truncated` then says the list is incomplete
 * so the UI can say so rather than silently showing part of it.
 */
export interface Page<T> {
  items: T[];
  total: number;
}

export interface AllPages<T> extends Page<T> {
  truncated: boolean;
}

export const MAX_PAGE_SIZE = 200;
export const DEFAULT_MAX_PAGES = 25; // 5,000 rows at the max page size

export async function fetchAllPages<T>(
  fetchPage: (page: number) => Promise<Page<T>>,
  { maxPages = DEFAULT_MAX_PAGES }: { maxPages?: number } = {},
): Promise<AllPages<T>> {
  const items: T[] = [];
  let total = 0;
  for (let page = 1; page <= maxPages; page += 1) {
    const result = await fetchPage(page);
    total = result.total;
    items.push(...result.items);
    if (items.length >= total || result.items.length === 0) break;
  }
  return { items, total, truncated: items.length < total };
}
