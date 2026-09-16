import { engineIdentity } from "@/lib/server/engine-identity";

export type V8InstanceIdentity = { version: 1; instanceId: string; createdAt: string; product: "v8-agent-os" };

/** The historical name remains at callers; only Engine creates the identity. */
export async function readOrCreateInstanceIdentity(): Promise<V8InstanceIdentity> {
    const identity = await engineIdentity<V8InstanceIdentity>("/instance");
    return { version: 1, instanceId: identity.instanceId, createdAt: identity.createdAt, product: "v8-agent-os" };
}
