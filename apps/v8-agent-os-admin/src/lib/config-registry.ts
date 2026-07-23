import { ik } from "@/i18n/admin-legacy";
import {
  fetchAdminJson,
  invalidateAdminJsonCache,
  peekAdminJsonCache,
  primeAdminJsonCache,
} from "@/lib/admin-client-cache";
import { translateCurrentClient } from "@/lib/locale";
export type ConfigRegistryEnvelope<T = Record<string, unknown>> = {
  domain: string;
  title: string;
  summary: string;
  data: T;
  source: string;
  savePath: string | string[];
  reloadRequired: boolean;
  warnings: string[];
  advancedFields: string[];
};

function configDomainUrl(domain: string) {
  return `/api/config-registry/${encodeURIComponent(domain)}`;
}

export function peekConfigDomain<T = Record<string, unknown>>(domain: string) {
  return peekAdminJsonCache<ConfigRegistryEnvelope<T>>(configDomainUrl(domain));
}

export async function fetchConfigDomain<T = Record<string, unknown>>(domain: string, options: { force?: boolean } = {}) {
  const url = configDomainUrl(domain);
  try {
    return await fetchAdminJson<ConfigRegistryEnvelope<T>>(url, { force: options.force });
  } catch (error) {
    throw new Error(error instanceof Error && error.message
      ? error.message
      : translateCurrentClient(ik("kfc8bd476af")));
  }
}
export async function saveConfigDomain<T = Record<string, unknown>>(domain: string, payload: Record<string, unknown>) {
  const url = configDomainUrl(domain);
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
  const data = (await response.json().catch(() => ({}))) as ConfigRegistryEnvelope<T> | {
    error?: string;
    detail?: string;
  };
  if (!response.ok) {
    throw new Error((data as {
      detail?: string;
      error?: string;
    }).detail || (data as {
      error?: string;
    }).error || translateCurrentClient(ik("k83498cb523")));
  }
  invalidateAdminJsonCache(url);
  primeAdminJsonCache(url, data);
  return data as ConfigRegistryEnvelope<T>;
}
