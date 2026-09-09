import assert from "node:assert/strict";
import test from "node:test";
import { profileAccessSummary } from "../../scripts/browser_profile_access.mjs";

test("only session domain metadata is exported; expiry and opaque pages are honest", async () => {
  const browser = { contexts: () => [{ cookies: async () => [
    { domain: ".metaso.cn", name: "secret-name", value: "PRIVATE", expires: -1 },
    { domain: "expired.test", value: "PRIVATE", expires: 1 },
  ], pages: () => [
    { url: () => "https://www.metaso.cn/?token=PRIVATE", evaluate: async () => false },
    { url: () => "https://storage.test/account", evaluate: async () => true },
    { url: () => "about:blank", evaluate: async () => { throw Error("must not inspect opaque origin"); } },
  ] }] };
  const result = await profileAccessSummary(browser);
  assert.equal(result.credentialsExported, false);
  assert.equal(result.sites.find(site => site.host === "storage.test").sessionPresent, true);
  assert.equal(result.sites.find(site => site.host === "www.metaso.cn").access, "unverified");
  assert.equal(result.sites.some(site => site.host === "expired.test"), false);
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE|secret-name|token=/);
});

test("an unrelated incognito context cannot advertise a login for persistent reads", async () => {
  const empty = { cookies: async () => [], pages: () => [] };
  const other = { cookies: async () => [{ domain: "example.test", expires: -1 }], pages: () => [] };
  const result = await profileAccessSummary({ contexts: () => [empty, other] });
  assert.deepEqual(result.sites, []);
});
