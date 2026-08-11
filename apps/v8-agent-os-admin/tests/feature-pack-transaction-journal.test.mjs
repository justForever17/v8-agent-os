import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  createFeaturePackInstallJournal,
  featurePackOperationPaths,
  listFeaturePackInstallJournals,
  persistFeaturePackInstallJournal,
  planFeaturePackInstallRecovery,
  readFeaturePackInstallJournal,
  transitionFeaturePackInstallJournal,
} from "../src/lib/server/feature-pack-transaction-journal.ts";

const operationId = "11111111-2222-4333-8444-555555555555";

function withRoot(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8-feature-pack-journal-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function observation(journal, overrides = {}) {
  return {
    activeOperationId: null,
    currentOperationId: operationId,
    currentStatus: "installing",
    currentTargetDir: null,
    currentReceiptRef: null,
    stagingExists: false,
    stagingReceiptExists: false,
    versionExists: false,
    versionReceiptExists: false,
    ...overrides,
  };
}

test("operation paths bind staging, immutable version, logical backup, and journal to one UUID", (t) => {
  const root = withRoot(t);
  const journal = createFeaturePackInstallJournal({
    installRoot: root,
    packId: "rpa_automation",
    operationId,
    logRef: path.join(root, "install.log"),
    previousState: { status: "installed", targetDir: path.join(root, "legacy", "python") },
  });

  for (const value of Object.values(journal.paths)) assert.match(value, new RegExp(operationId));
  assert.equal(journal.backup.operationId, operationId);
  assert.equal(listFeaturePackInstallJournals(root).length, 1);

  const tampered = structuredClone(journal);
  tampered.paths.targetDir = path.join(root, "outside", "python");
  assert.throws(() => persistFeaturePackInstallJournal(root, tampered), /path_mismatch/);
});

test("recovery planner covers crashes before staging, after staging, after publish, and after commit", (t) => {
  const root = withRoot(t);
  let journal = createFeaturePackInstallJournal({
    installRoot: root,
    packId: "rpa_automation",
    operationId,
    logRef: path.join(root, "install.log"),
    previousState: {
      status: "installed",
      targetDir: path.join(root, "legacy", "python"),
      receiptRef: path.join(root, "legacy", "receipt.json"),
    },
  });

  assert.equal(planFeaturePackInstallRecovery(journal, observation(journal)), "restore_previous");
  journal = transitionFeaturePackInstallJournal(root, journal, "installing", {
    backup: { ...journal.backup, compatible: false },
  });
  assert.equal(planFeaturePackInstallRecovery(journal, observation(journal)), "mark_failed");
  journal = transitionFeaturePackInstallJournal(root, journal, "installing", {
    backup: { ...journal.backup, compatible: true },
  });
  assert.equal(readFeaturePackInstallJournal(root, journal.paths.journalRef)?.phase, "installing");
  assert.equal(planFeaturePackInstallRecovery(journal, observation(journal)), "restore_previous");
  assert.equal(planFeaturePackInstallRecovery(journal, observation(journal, {
    stagingExists: true,
    stagingReceiptExists: true,
  })), "restore_previous");
  journal = transitionFeaturePackInstallJournal(root, journal, "staged", {
    finalPatch: {
      status: "installed",
      targetDir: journal.paths.targetDir,
      receiptRef: journal.paths.receiptRef,
    },
  });
  assert.equal(readFeaturePackInstallJournal(root, journal.paths.journalRef)?.phase, "staged");
  assert.equal(planFeaturePackInstallRecovery(journal, observation(journal, {
    stagingExists: true,
    stagingReceiptExists: true,
  })), "publish_staging");
  journal = transitionFeaturePackInstallJournal(root, journal, "published");
  assert.equal(readFeaturePackInstallJournal(root, journal.paths.journalRef)?.phase, "published");
  assert.equal(planFeaturePackInstallRecovery(journal, observation(journal, {
    versionExists: true,
    versionReceiptExists: true,
  })), "commit_version");
  journal = transitionFeaturePackInstallJournal(root, journal, "commit_pending");
  assert.equal(readFeaturePackInstallJournal(root, journal.paths.journalRef)?.phase, "commit_pending");
  assert.equal(planFeaturePackInstallRecovery(journal, observation(journal, {
    versionExists: true,
    versionReceiptExists: true,
  })), "commit_version");
  assert.equal(planFeaturePackInstallRecovery(journal, observation(journal, {
    currentOperationId: null,
    currentStatus: "installed",
    currentTargetDir: journal.paths.targetDir,
    currentReceiptRef: journal.paths.receiptRef,
    versionExists: true,
    versionReceiptExists: true,
  })), "finalize_committed");
  journal = transitionFeaturePackInstallJournal(root, journal, "committed");
  assert.equal(readFeaturePackInstallJournal(root, journal.paths.journalRef)?.phase, "committed");
  assert.equal(planFeaturePackInstallRecovery(journal, observation(journal, {
    currentOperationId: null,
    currentStatus: "installed",
    currentTargetDir: journal.paths.targetDir,
    currentReceiptRef: journal.paths.receiptRef,
    versionExists: true,
    versionReceiptExists: true,
  })), "none");
});

test("recovery never adopts or deletes artifacts when a newer operation owns the pack", (t) => {
  const root = withRoot(t);
  const journal = createFeaturePackInstallJournal({
    installRoot: root,
    packId: "computer_use_desktop",
    operationId,
    logRef: path.join(root, "install.log"),
    previousState: {},
  });

  assert.equal(planFeaturePackInstallRecovery(journal, observation(journal, {
    currentOperationId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    stagingExists: true,
    stagingReceiptExists: true,
    versionExists: true,
    versionReceiptExists: true,
  })), "mark_superseded");
  assert.equal(planFeaturePackInstallRecovery(journal, observation(journal, {
    activeOperationId: operationId,
  })), "none");
  const committed = transitionFeaturePackInstallJournal(root, journal, "committed");
  assert.equal(planFeaturePackInstallRecovery(committed, observation(committed, {
    currentOperationId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
  })), "none");
  assert.deepEqual(featurePackOperationPaths(root, journal.packId, operationId), journal.paths);
});

test("a receipt written before the staged journal never publishes without a final patch", (t) => {
  const root = withRoot(t);
  const recoverable = createFeaturePackInstallJournal({
    installRoot: root,
    packId: "rpa_automation",
    operationId,
    logRef: path.join(root, "install.log"),
    previousState: {
      status: "installed",
      targetDir: path.join(root, "legacy", "python"),
      receiptRef: path.join(root, "legacy", "receipt.json"),
    },
  });
  const interrupted = transitionFeaturePackInstallJournal(root, recoverable, "installing", {
    backup: { ...recoverable.backup, compatible: true },
  });

  assert.equal(planFeaturePackInstallRecovery(interrupted, observation(interrupted, {
    stagingExists: true,
    stagingReceiptExists: true,
  })), "restore_previous");
  assert.equal(planFeaturePackInstallRecovery(interrupted, observation(interrupted, {
    versionExists: true,
    versionReceiptExists: true,
  })), "restore_previous");

  const firstInstall = createFeaturePackInstallJournal({
    installRoot: root,
    packId: "computer_use_desktop",
    operationId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    logRef: path.join(root, "first-install.log"),
    previousState: {},
  });
  assert.equal(planFeaturePackInstallRecovery(firstInstall, observation(firstInstall, {
    currentOperationId: firstInstall.operationId,
    stagingExists: true,
    stagingReceiptExists: true,
  })), "mark_failed");
});
