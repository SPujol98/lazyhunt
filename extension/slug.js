// Shared URL/id slug helper (portal-adapters): accents stripped, lowercase,
// runs of non-alphanumerics collapsed to one dash. Used by the search ids
// (searches.js) and the InfoJobs city path (portals/infojobs.js).

export function slugify(text) {
  return String(text || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}
