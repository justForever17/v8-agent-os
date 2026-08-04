import { ik } from "@/i18n/admin-legacy";
import {
  applyAdminEngineOriginChange,
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

function configDomainUrl(domain: string, refreshEnvironment = false) {
  const baseUrl = `/api/config-registry/${encodeURIComponent(domain)}`;
  return refreshEnvironment ? `${baseUrl}?refresh=true` : baseUrl;
}

function configuredEngineBaseUrl(envelope: unknown) {
  if (!envelope || typeof envelope !== "object") return undefined;
  const data = (envelope as { data?: unknown }).data;
  if (!data || typeof data !== "object") return undefined;
  const bridge = (data as { bridge?: unknown }).bridge;
  if (!bridge || typeof bridge !== "object") return undefined;
  return (bridge as { engineBaseUrl?: unknown }).engineBaseUrl;
}

export function peekConfigDomain<T = Record<string, unknown>>(domain: string) {
  return peekAdminJsonCache<ConfigRegistryEnvelope<T>>(configDomainUrl(domain));
}

export async function fetchConfigDomain<T = Record<string, unknown>>(
  domain: string,
  options: { force?: boolean; refreshEnvironment?: boolean } = {},
) {
  const url = configDomainUrl(domain, options.refreshEnvironment);
  try {
    const envelope = await fetchAdminJson<ConfigRegistryEnvelope<T>>(url, { force: options.force });
    if (options.refreshEnvironment) {
      primeAdminJsonCache(configDomainUrl(domain), envelope);
    }
    return envelope;
  } catch (error) {
    throw new Error(error instanceof Error && error.message
      ? error.message
      : translateCurrentClient(ik("kfc8bd476af")));
  }
}
export async function saveConfigDomain<T = Record<string, unknown>>(domain: string, payload: Record<string, unknown>) {
  const url = configDomainUrl(domain);
  const previousEnvelope = domain === "system-base"
    ? peekAdminJsonCache<ConfigRegistryEnvelope<Record<string, unknown>>>(url)
    : undefined;
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
  const engineOriginChanged = previousEnvelope !== undefined
    && domain === "system-base"
    && applyAdminEngineOriginChange(
      configuredEngineBaseUrl(previousEnvelope),
      configuredEngineBaseUrl(data),
      { reload: true },
    );
  if (!engineOriginChanged) {
    invalidateAdminJsonCache(url);
    primeAdminJsonCache(url, data);
  }
  return data as ConfigRegistryEnvelope<T>;
}
