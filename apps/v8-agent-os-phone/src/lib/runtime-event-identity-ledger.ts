const DEFAULT_IDENTITY_LIMIT = 2048;

function positiveSequence(value: unknown) {
    const sequence = Number(value || 0) || 0;
    return Number.isSafeInteger(sequence) && sequence > 0 ? sequence : 0;
}

export class BoundedRuntimeEventIdentityLedger {
    private readonly identities = new Set<string>();
    private readonly sequenceByIdentity = new Map<string, number>();
    private readonly limit: number;

    constructor(limit = DEFAULT_IDENTITY_LIMIT) {
        const requestedLimit = Math.floor(Number(limit) || 0);
        this.limit = Number.isSafeInteger(requestedLimit) && requestedLimit > 0
            ? requestedLimit
            : DEFAULT_IDENTITY_LIMIT;
    }

    get seenIdentities(): ReadonlySet<string> {
        return this.identities;
    }

    get size() {
        return this.identities.size;
    }

    has(identity: string) {
        return this.identities.has(identity);
    }

    remember(identity: string, sequence: unknown) {
        if (!identity || this.identities.has(identity)) {
            return;
        }
        this.identities.add(identity);
        this.sequenceByIdentity.set(identity, positiveSequence(sequence));
        while (this.identities.size > this.limit) {
            const oldest = this.identities.values().next();
            if (oldest.done) break;
            this.delete(oldest.value);
        }
    }

    pruneSnapshotCovered(snapshotCoveredSequence: unknown) {
        const coveredSequence = positiveSequence(snapshotCoveredSequence);
        if (!coveredSequence) {
            return;
        }
        for (const [identity, sequence] of this.sequenceByIdentity) {
            if (sequence > 0 && sequence <= coveredSequence) {
                this.delete(identity);
            }
        }
    }

    clear() {
        this.identities.clear();
        this.sequenceByIdentity.clear();
    }

    private delete(identity: string) {
        this.identities.delete(identity);
        this.sequenceByIdentity.delete(identity);
    }
}
