/**
 * Product Web owns both chat and configuration routes. Old Admin bridge URLs
 * are migration input only; they cannot choose a second host for local keys.
 */
export function resolveProductOrigin(environment = process.env) {
  const value = environment.V8_WEB_BASE_URL || environment.AUTH_URL || environment.NEXTAUTH_URL
    || `http://127.0.0.1:${environment.PORT || "9527"}`;
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol)
    || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
    || url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new Error("Product Web requires a local origin");
  }
  return url.origin;
}
