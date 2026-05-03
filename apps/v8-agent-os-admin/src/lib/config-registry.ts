import { INTERNAL_READABLE } from "@/i18n/internal-readable";
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
export async function fetchConfigDomain<T = Record<string, unknown>>(domain: string) {
  const response = await fetch(`/api/config-registry/${encodeURIComponent(domain)}`, {
    cache: "no-store"
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
    }).error || INTERNAL_READABLE.kfc8bd476af);
  }
  return data as ConfigRegistryEnvelope<T>;
}
export async function saveConfigDomain<T = Record<string, unknown>>(domain: string, payload: Record<string, unknown>) {
  const response = await fetch(`/api/config-registry/${encodeURIComponent(domain)}`, {
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
    }).error || INTERNAL_READABLE.k83498cb523);
  }
  return data as ConfigRegistryEnvelope<T>;
}
