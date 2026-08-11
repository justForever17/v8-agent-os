import fs from "node:fs";
import path from "node:path";

export const FEATURE_PACK_JOURNAL_VERSION = 1;

export type FeaturePackJournalPhase =
    | "prepared"
    | "installing"
    | "staged"
    | "published"
    | "commit_pending"
    | "commit_blocked"
    | "recovery_pending"
    | "committed"
    | "recovered"
    | "failed"
    | "superseded";

export type FeaturePackOperationPaths = {
    stagingRoot: string;
    versionRoot: string;
    targetDir: string;
    assetRoot: string;
    receiptRef: string;
    journalRef: string;
};

export type FeaturePackInstallJournal = {
    version: number;
    packId: string;
    operationId: string;
    phase: FeaturePackJournalPhase;
    createdAt: string;
    updatedAt: string;
    logRef: string;
    paths: FeaturePackOperationPaths;
    backup: {
        operationId: string;
        state: Record<string, unknown>;
        compatible: boolean | null;
    };
    finalPatch: Record<string, unknown> | null;
    commitAttempts: number;
    lastError: string | null;
};

export type FeaturePackRecoveryObservation = {
    activeOperationId?: string | null;
    currentOperationId?: string | null;
    currentStatus?: string | null;
    currentTargetDir?: string | null;
    currentReceiptRef?: string | null;
    stagingExists: boolean;
    stagingReceiptExists: boolean;
    versionExists: boolean;
    versionReceiptExists: boolean;
};

export type FeaturePackRecoveryAction =
    | "none"
    | "publish_staging"
    | "commit_version"
    | "restore_previous"
    | "mark_failed"
    | "finalize_committed"
    | "mark_superseded";

const OPERATION_ID_PATTERN = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/;
const PACK_ID_PATTERN = /^[a-z0-9_]+$/;
const JOURNAL_PHASES = new Set<FeaturePackJournalPhase>([
    "prepared",
    "installing",
    "staged",
    "published",
    "commit_pending",
    "commit_blocked",
    "recovery_pending",
    "committed",
    "recovered",
    "failed",
    "superseded",
]);

export function isFeaturePackOperationId(value: unknown): value is string {
    return OPERATION_ID_PATTERN.test(String(value || "").trim());
}

function assertPackId(packId: string) {
    if (!PACK_ID_PATTERN.test(packId)) throw new Error("feature_pack_journal_invalid_pack_id");
}

function assertOperationId(operationId: string) {
    if (!isFeaturePackOperationId(operationId)) throw new Error("feature_pack_journal_invalid_operation_id");
}

export function featurePackOperationPaths(
    installRoot: string,
    packId: string,
    operationId: string,
): FeaturePackOperationPaths {
    assertPackId(packId);
    assertOperationId(operationId);
    const canonicalRoot = path.resolve(installRoot);
    const stagingRoot = path.join(canonicalRoot, ".staging", `${packId}-${operationId}`);
    const versionRoot = path.join(canonicalRoot, packId, "versions", operationId);
    return {
        stagingRoot,
        versionRoot,
        targetDir: path.join(versionRoot, "python"),
        assetRoot: path.join(versionRoot, "models"),
        receiptRef: path.join(versionRoot, "receipt.json"),
        journalRef: path.join(canonicalRoot, ".journal", `${packId}-${operationId}.json`),
    };
}

function samePath(left: unknown, right: string) {
    return path.resolve(String(left || "")) === path.resolve(right);
}

function assertJournalShape(installRoot: string, journal: FeaturePackInstallJournal) {
    if (journal.version !== FEATURE_PACK_JOURNAL_VERSION) throw new Error("feature_pack_journal_version_unsupported");
    assertPackId(journal.packId);
    assertOperationId(journal.operationId);
    if (!JOURNAL_PHASES.has(journal.phase)) throw new Error("feature_pack_journal_invalid_phase");
    if (journal.backup?.operationId !== journal.operationId) throw new Error("feature_pack_journal_backup_operation_mismatch");
    const expected = featurePackOperationPaths(installRoot, journal.packId, journal.operationId);
    for (const field of Object.keys(expected) as Array<keyof FeaturePackOperationPaths>) {
        if (!samePath(journal.paths?.[field], expected[field])) {
            throw new Error(`feature_pack_journal_path_mismatch:${field}`);
        }
    }
    if (journal.finalPatch) {
        if (journal.finalPatch.targetDir && !samePath(journal.finalPatch.targetDir, expected.targetDir)) {
            throw new Error("feature_pack_journal_patch_target_mismatch");
        }
        if (journal.finalPatch.assetRoot && !samePath(journal.finalPatch.assetRoot, expected.assetRoot)) {
            throw new Error("feature_pack_journal_patch_asset_mismatch");
        }
        if (journal.finalPatch.receiptRef && !samePath(journal.finalPatch.receiptRef, expected.receiptRef)) {
            throw new Error("feature_pack_journal_patch_receipt_mismatch");
        }
    }
}

function fsyncParentDirectory(filePath: string) {
    let descriptor: number | null = null;
    try {
        descriptor = fs.openSync(path.dirname(filePath), "r");
        fs.fsyncSync(descriptor);
    } catch {
        // Some Windows filesystems do not allow opening directories for fsync.
    } finally {
        if (descriptor !== null) fs.closeSync(descriptor);
    }
}

export function persistFeaturePackInstallJournal(
    installRoot: string,
    journal: FeaturePackInstallJournal,
): FeaturePackInstallJournal {
    const next = { ...journal, updatedAt: new Date().toISOString() };
    assertJournalShape(installRoot, next);
    fs.mkdirSync(path.dirname(next.paths.journalRef), { recursive: true });
    const temporaryRef = `${next.paths.journalRef}.tmp-${next.operationId}`;
    const descriptor = fs.openSync(temporaryRef, "w");
    try {
        fs.writeFileSync(descriptor, JSON.stringify(next, null, 2), "utf-8");
        fs.fsyncSync(descriptor);
    } finally {
        fs.closeSync(descriptor);
    }
    fs.renameSync(temporaryRef, next.paths.journalRef);
    fsyncParentDirectory(next.paths.journalRef);
    return next;
}

export function createFeaturePackInstallJournal(input: {
    installRoot: string;
    packId: string;
    operationId: string;
    logRef: string;
    previousState: Record<string, unknown>;
}): FeaturePackInstallJournal {
    const timestamp = new Date().toISOString();
    const journal: FeaturePackInstallJournal = {
        version: FEATURE_PACK_JOURNAL_VERSION,
        packId: input.packId,
        operationId: input.operationId,
        phase: "prepared",
        createdAt: timestamp,
        updatedAt: timestamp,
        logRef: input.logRef,
        paths: featurePackOperationPaths(input.installRoot, input.packId, input.operationId),
        backup: {
            operationId: input.operationId,
            state: { ...input.previousState },
            compatible: null,
        },
        finalPatch: null,
        commitAttempts: 0,
        lastError: null,
    };
    return persistFeaturePackInstallJournal(input.installRoot, journal);
}

export function transitionFeaturePackInstallJournal(
    installRoot: string,
    journal: FeaturePackInstallJournal,
    phase: FeaturePackJournalPhase,
    patch: Partial<Pick<FeaturePackInstallJournal, "backup" | "finalPatch" | "commitAttempts" | "lastError">> = {},
) {
    return persistFeaturePackInstallJournal(installRoot, {
        ...journal,
        ...patch,
        phase,
    });
}

export function readFeaturePackInstallJournal(
    installRoot: string,
    journalRef: string,
): FeaturePackInstallJournal | null {
    try {
        const payload = JSON.parse(fs.readFileSync(journalRef, "utf-8")) as FeaturePackInstallJournal;
        assertJournalShape(installRoot, payload);
        return payload;
    } catch {
        return null;
    }
}

export function listFeaturePackInstallJournals(installRoot: string) {
    const journalRoot = path.join(path.resolve(installRoot), ".journal");
    if (!fs.existsSync(journalRoot)) return [];
    return fs.readdirSync(journalRoot)
        .filter((name) => name.endsWith(".json"))
        .map((name) => readFeaturePackInstallJournal(installRoot, path.join(journalRoot, name)))
        .filter((journal): journal is FeaturePackInstallJournal => journal !== null)
        .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
}

export function planFeaturePackInstallRecovery(
    journal: FeaturePackInstallJournal,
    observation: FeaturePackRecoveryObservation,
): FeaturePackRecoveryAction {
    if (observation.activeOperationId === journal.operationId) return "none";
    if (["committed", "recovered", "failed", "superseded"].includes(journal.phase)) return "none";
    if (observation.currentOperationId && observation.currentOperationId !== journal.operationId) {
        return "mark_superseded";
    }
    const committedStateMatches = !observation.currentOperationId
        && observation.currentStatus === "installed"
        && samePath(observation.currentTargetDir, journal.paths.targetDir)
        && samePath(observation.currentReceiptRef, journal.paths.receiptRef);
    if (committedStateMatches) return "finalize_committed";
    if (observation.currentOperationId !== journal.operationId) return "mark_superseded";
    if (journal.finalPatch && observation.versionExists && observation.versionReceiptExists) {
        return "commit_version";
    }
    if (journal.finalPatch && observation.stagingExists && observation.stagingReceiptExists) {
        return "publish_staging";
    }
    if (
        journal.backup.compatible !== false
        && String(journal.backup.state.status || "") === "installed"
        && Boolean(journal.backup.state.targetDir)
        && Boolean(journal.backup.state.receiptRef)
    ) return "restore_previous";
    return "mark_failed";
}
