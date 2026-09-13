/** Opaque IDs retain case. Endpoints are transport, never cache authority. */
export type PhoneAuthority = { instanceId: string; principalId: string; profileId: string };

export function phoneAuthorityKey(identity: PhoneAuthority): string {
    if (!identity.instanceId || !identity.principalId || !identity.profileId) {
        throw new Error("A paired instance, principal and profile are required");
    }
    return JSON.stringify([identity.instanceId, identity.principalId, identity.profileId]);
}

export function phoneSessionKey(authorityKey: string, servingInstanceId: string, sessionId: string): string {
    if (!authorityKey || !servingInstanceId || !sessionId) throw new Error("Incomplete session identity");
    return JSON.stringify([authorityKey, servingInstanceId, sessionId]);
}

export function phoneResourceKey(sessionKey: string, kind: string, id: string, revision: string): string {
    return JSON.stringify([sessionKey, kind, id, revision]);
}
