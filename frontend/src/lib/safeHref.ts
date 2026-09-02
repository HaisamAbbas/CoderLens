/** Only ever render a link for a real http(s) URL. These values (a saved
 *  Jira/Confluence base URL, a Confluence page URL echoed back by a publish
 *  job) come from the user's own integration settings with no scheme
 *  validation server-side — saving `javascript:...` as a base URL would
 *  otherwise produce a clickable `javascript:` link. Returns undefined
 *  (renders no href, i.e. a plain non-clickable label) for anything else. */
export function safeHref(url?: string | null): string | undefined {
  return url && /^https?:\/\//i.test(url) ? url : undefined;
}
