"use client";

import { type DragEvent, type KeyboardEvent, type MouseEvent, type PointerEvent, type WheelEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, ArrowDown, ArrowUp, CheckCircle2, Copy, Crosshair, FileCode2, GitBranch, MousePointerClick, Pause, Play, Plus, RefreshCw, Save, Search, ShieldAlert, Square, Trash2, Video, Wand2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
import { RUN_LABELS, formatWhen } from "@/components/runtime/use-runtime-ops";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { ag, tg, ti } from "@/i18n/admin-legacy";
import { translateCurrentClient } from "@/lib/locale";
type AvailabilityPayload = {
  robotFramework?: boolean;
  rpaFramework?: boolean;
  libraries?: Record<string, boolean>;
  robotFrameworkDetail?: AvailabilityProbe;
  rpaFrameworkDetail?: AvailabilityProbe;
  libraryDetails?: Record<string, AvailabilityProbe>;
};
type AvailabilityProbe = {
  detected?: boolean;
  importable?: boolean;
  origin?: string | null;
  error?: string | null;
};
type AssessmentSignals = {
  acceptedRatio?: number;
  excludedRatio?: number;
  nativeSemanticRatio?: number;
  recoveryHeavyRatio?: number;
  profileAugmentedSteps?: number;
  profileAugmentedRatio?: number;
  calibratedSteps?: number;
  historicalNativeSuccessRate?: number;
  historicalScriptRuns?: number;
  historicalScriptCompletedRate?: number;
  historicalScriptFallbackHeavyRate?: number;
  historicalScriptNativeSuccessRate?: number;
  historicalScriptProfileAugmentedRatio?: number;
  historicalScriptReviewRequiredRate?: number;
  historicalScriptCompileBlockedRate?: number;
  historicalScriptCalibrationSource?: string;
};
type AssessmentTrustModel = {
  effectiveStepReviewThreshold?: number;
  effectiveStepExcludeThreshold?: number;
  effectiveScriptTrustedThreshold?: number;
  effectiveScriptReviewThreshold?: number;
  effectiveScriptFallbackHeavyThreshold?: number;
  effectiveBlockedAcceptedRatioMin?: number;
  effectiveBlockedExcludedRatioMax?: number;
  trustModelVersion?: string;
};
type DraftPayload = {
  id: string;
  name?: string;
  appId?: string;
  goal?: string;
  archivedAt?: string;
  source?: {
    type?: string;
    traceRunId?: string;
    traceRunIds?: string[];
  };
  steps?: EditableDraftStep[];
  variables?: Array<{
    name?: string;
    required?: boolean;
    type?: string;
    defaultValue?: unknown;
    secretName?: string;
    sensitive?: boolean;
  }>;
  objectLibrary?: ObjectLibraryElement[];
  assessment?: {
    score?: number;
    status?: string;
    band?: string;
    reasons?: string[];
    acceptedSteps?: number;
    reviewRequiredSteps?: number;
    excludedSteps?: number;
    signals?: AssessmentSignals;
    trustModel?: AssessmentTrustModel;
  };
  metadata?: {
    compileIssues?: string[];
    templateGovernance?: {
      stage?: string;
      recommendedDecision?: string;
      confidence?: number;
      rolloutMode?: string;
      reasons?: string[];
    };
    templateGovernanceStage?: string;
    templateRecommendedDecision?: string;
    templateTrustConfidence?: number;
    templateRolloutMode?: string;
    templateStatus?: string;
    archivedAt?: string;
    archivedBy?: string;
    archiveReason?: string;
  };
  robot?: {
    tags?: string[];
    libraries?: Array<{
      name?: string;
      required?: boolean;
    }>;
  };
};
type ScriptPayload = {
  name: string;
  path: string;
  updatedAt?: string;
  size?: number;
};
type TemplateReviewSummary = {
  total?: number;
  approveCount?: number;
  freezeCount?: number;
  rollbackCount?: number;
  reviewRequiredCount?: number;
  rejectedCount?: number;
  lastDecision?: string | null;
  lastDecisionLabel?: string | null;
  lastReviewer?: string | null;
  lastReviewedAt?: string | null;
  approvalRequired?: boolean;
};
type TemplateViewPayload = {
  statusLabel?: string;
  stageLabel?: string;
  recommendedDecisionLabel?: string;
  rolloutModeLabel?: string;
  executionPath?: string;
  executionPathLabel?: string;
  confidenceLabel?: string;
  riskFlags?: string[];
  riskFlagLabels?: string[];
  reviewSummary?: TemplateReviewSummary;
  signalSummary?: {
    sourceTraceCount?: number;
    localRepairCount?: number;
    compileIssueCount?: number;
    historicalRuns?: number;
    historicalCompletedRate?: number | null;
    historicalFallbackHeavyRate?: number | null;
    historicalReviewRequiredRate?: number | null;
  };
};
type TemplatePayload = {
  id: string;
  name?: string;
  appId?: string;
  status?: string;
  archivedAt?: string;
  updatedAt?: string;
  source?: {
    draftId?: string;
    templateStage?: string;
    templateStatus?: string;
  };
  metadata?: {
    revision?: number;
    archivedAt?: string;
    archivedBy?: string;
    archiveReason?: string;
    reviewHistory?: Array<{
      decision?: string;
      reviewer?: string;
      at?: string;
      notes?: string | null;
    }>;
  };
  governance?: {
    stage?: string;
    confidence?: number;
    recommendedDecision?: string;
    rolloutMode?: string;
    reasons?: string[];
  };
  view?: TemplateViewPayload;
};
type TemplateHistoryRecord = {
  path?: string;
  revision?: number;
  actor?: string;
  reason?: string | null;
  at?: string;
  view?: TemplateViewPayload;
  historyView?: {
    revision?: number;
    statusLabel?: string;
    reason?: string | null;
    actor?: string | null;
    at?: string | null;
  };
  template?: TemplatePayload;
};
type TemplateSummaryPayload = {
  total?: number;
  byStatus?: Record<string, number>;
  byStage?: Record<string, number>;
  templatePreferredCount?: number;
  computerUseFirstCount?: number;
  atRiskCount?: number;
  reviewRequiredCount?: number;
  maxConfidence?: number;
};
type ApprovalRecord = {
  id: string;
  run_id?: string;
  session_id?: string;
  approval_kind?: string;
  created_at?: string;
  request?: {
    question?: string;
    prompt?: string;
    rpa?: {
      subject?: string;
      scriptId?: string;
      robotFile?: string;
      requiredApprovals?: Array<{
        stepId?: string;
        use?: string;
        mode?: string;
        reason?: string;
        confidence?: number;
      }>;
    };
  };
};
type RunRecord = {
  id: string;
  session_id?: string;
  status?: string;
  run_type?: string;
  trigger_source?: string;
  created_at?: string;
  metadata?: Record<string, unknown>;
};
type RecordingSessionPayload = {
  recordingSessionId: string;
  traceRunId: string;
  sessionId?: string;
  state?: string;
  targetMode?: string;
  name?: string;
  goal?: string;
  appId?: string;
  browserKind?: string;
  browserProfileId?: string;
  windowHandle?: string | number | null;
  targetLock?: Record<string, unknown>;
  captureAssistant?: Record<string, unknown>;
  capturePool?: CapturePoolItem[];
  objectLibrary?: ObjectLibraryElement[];
  createdDraftId?: string | null;
  compileError?: string | null;
  stepCount?: number;
  createdAt?: string;
  updatedAt?: string;
};
type CapturePoolItem = {
  tempElementId?: string;
  elementId?: string;
  label?: string;
  name?: string;
  source?: string;
  action?: string;
  selector?: Record<string, unknown>;
  selectorCandidates?: Array<Record<string, unknown>>;
  targetWindow?: Record<string, unknown>;
  coordinate?: Record<string, unknown>;
  confidence?: number;
  fragileCoordinateFallback?: boolean;
  captureMode?: string;
  capturedAt?: string;
};
type ObjectLibraryElement = CapturePoolItem & {
  elementId?: string;
  sourceTempElementId?: string;
  savedAt?: string;
};
type ObservationCandidate = {
  css?: string;
  selector?: string;
  elementId?: string;
  name?: string;
  role?: string;
  controlType?: string;
  automationId?: string;
  windowTitle?: string;
  confidence?: number;
  source?: string;
};
type EditableDraftStep = {
  stepId?: string;
  action?: string;
  use?: string;
  intent?: string;
  approval?: {
    mode?: string;
  };
  target?: Record<string, unknown>;
  params?: Record<string, unknown>;
  variables?: Array<Record<string, unknown>>;
  verification?: Record<string, unknown>;
  recovery?: Record<string, unknown>;
  risk?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  assessment?: {
    score?: number;
    status?: string;
  };
};
type DraftVariableRow = {
  id: string;
  name: string;
  type: string;
  required: boolean;
  defaultValue: string;
  secretName: string;
  sensitive: boolean;
};
type StepValidationResult = {
  ok?: boolean;
  mode?: string;
  summary?: string;
  checks?: Array<{
    name?: string;
    ok?: boolean;
    message?: string;
  }>;
  warnings?: string[];
  errors?: string[];
};
type ComputerUseAppPayload = {
  appId?: string;
  id?: string;
  profileId?: string;
  displayName?: string;
  name?: string;
  isRunning?: boolean;
  launchable?: boolean;
  learned?: boolean;
  sources?: string[];
  aliases?: string[];
  processNames?: string[];
  titlePatterns?: string[];
  runningWindows?: Array<{
    title?: string;
    processName?: string;
    className?: string;
  }>;
  learnedSelectorCount?: number;
  learnedInteractionCount?: number;
  topWindowTitle?: string;
};
function formatConfidence(score?: number | null) {
  if (typeof score !== "number" || Number.isNaN(score)) {
    return "n/a";
  }
  return `${Math.round(score * 100)}%`;
}
function formatRatio(value?: number | null) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "n/a";
  }
  return `${Math.round(value * 100)}%`;
}
function formatCalibrationSource(t: ReturnType<typeof useT>, source?: string | null) {
  if (!source) {
    return "n/a";
  }
  if (source === "fingerprint") {
    return ti(t, "k84e5e934df");
  }
  if (source === "script") {
    return ti(t, "k2240b84d00");
  }
  return source;
}
function readRunAssessment(metadata?: Record<string, unknown>) {
  const assessment = metadata?.assessment;
  if (assessment && typeof assessment === "object" && !Array.isArray(assessment)) {
    return assessment as {
      score?: number;
      status?: string;
      band?: string;
      acceptedSteps?: number;
      reviewRequiredSteps?: number;
      excludedSteps?: number;
      signals?: AssessmentSignals;
      trustModel?: AssessmentTrustModel;
    };
  }
  return null;
}
function readRunExecutionState(metadata?: Record<string, unknown>) {
  const state = metadata?.executionState;
  return typeof state === "string" && state.trim() ? state : null;
}
function readRunFallback(metadata?: Record<string, unknown>) {
  const fallback = metadata?.fallback;
  if (fallback && typeof fallback === "object" && !Array.isArray(fallback)) {
    return fallback as {
      type?: string;
      sourceScriptId?: string;
      sourceTraceRunId?: string;
    };
  }
  return null;
}
function readRunTemplatePolicy(metadata?: Record<string, unknown>) {
  const policy = metadata?.templateExecutionPolicy;
  if (policy && typeof policy === "object" && !Array.isArray(policy)) {
    return policy as {
      stage?: string;
      status?: string;
      rolloutMode?: string;
      executionPath?: string;
      recommendedDecision?: string;
      confidence?: number;
    };
  }
  return null;
}
function prettyJson(value: unknown) {
  return JSON.stringify(value, null, 2);
}
function safeJsonParse<T>(value: string, fallback: T): T {
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}
function stableDraftStepKey(draftId: string, step: EditableDraftStep, index: number) {
  return step.stepId || `${draftId}:step:${index}`;
}
function stepActionName(step?: EditableDraftStep | null) {
  return firstString(step?.action, step?.use) || "step";
}
function stepIntentLabel(step?: EditableDraftStep | null) {
  const params = isPlainRecord(step?.params) ? step.params : {};
  return firstString(params.intent, step?.intent, params.action_name, params.toolbar_action_name, params.text, stepActionName(step));
}
function stepSelectorValue(step?: EditableDraftStep | null) {
  const target = isPlainRecord(step?.target) ? step?.target : {};
  const selector = isPlainRecord(target.selector) ? target.selector : {};
  const params = isPlainRecord(step?.params) ? step?.params : {};
  return firstString(selector.css, selector.xpath, selector.role, selector.selector, params.selector, params.selector_key);
}
function stepTextValue(step?: EditableDraftStep | null) {
  const params = isPlainRecord(step?.params) ? step?.params : {};
  return firstString(params.text, params.value);
}
function captureItemLabel(item?: CapturePoolItem | ObjectLibraryElement | null) {
  if (!item) return "";
  return item.name || item.label || firstString(item.selector?.name, item.selector?.css, item.selector?.xpath, item.targetWindow?.title, item.tempElementId, item.elementId) || "element";
}
function captureItemSelectorValue(item?: CapturePoolItem | ObjectLibraryElement | null) {
  if (!item) return "";
  const selector = isPlainRecord(item.selector) ? item.selector : {};
  const candidates = Array.isArray(item.selectorCandidates) ? item.selectorCandidates : [];
  const best = candidates.find(candidate => isPlainRecord(candidate)) as Record<string, unknown> | undefined;
  return firstString(selector.css, selector.xpath, selector.role, selector.selector, selector.automationId, selector.name, best?.css, best?.xpath, best?.role, best?.automationId, best?.name);
}
function captureItemCoordinateValue(item?: CapturePoolItem | ObjectLibraryElement | null) {
  if (!item || !isPlainRecord(item.coordinate)) return "";
  const x = item.coordinate.x;
  const y = item.coordinate.y;
  if (x == null || y == null || x === "" || y === "") return "";
  return `${x}, ${y}`;
}
function actionKind(action?: string) {
  const value = String(action || "").toLowerCase();
  if (value.includes("browser")) return "browser";
  if (value.includes("assert")) return "assert";
  if (value.includes("wait")) return "wait";
  if (value.includes("loop")) return "loop";
  if (value === "open_app" || value === "wait_window") return "app";
  if (value === "type_text" || value.includes("type")) return "type";
  if (value === "click" || value.includes("click")) return "click";
  if (value === "hotkey") return "hotkey";
  if (value === "scroll") return "scroll";
  if (value.includes("file") || value.includes("http") || value.includes("ocr") || value.includes("llm") || value.includes("variable")) return "data";
  return "generic";
}
function makeDraftStep(action: string, appId?: string): EditableDraftStep {
  const stepId = `step_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  const base: EditableDraftStep = {
    stepId,
    action,
    intent: action,
    params: {},
    target: {
      window: {
        appId: appId || "desktop",
      },
    },
    metadata: {
      recordedBy: "human",
      editedFrom: "admin_step_builder",
    },
  };
  if (action === "open_app") {
    base.intent = "launch_app";
    base.params = { appId: appId || "desktop" };
  } else if (action === "wait") {
    base.intent = "wait_for_element";
    base.params = { timeoutMs: 3000 };
    base.target = { ...base.target, selector: { css: "" } };
  } else if (action === "click") {
    base.intent = "click";
    base.target = { ...base.target, selector: { css: "" } };
  } else if (action === "type_text") {
    base.intent = "find_and_type";
    base.params = { text: "" };
    base.target = { ...base.target, selector: { css: "" } };
  } else if (action === "hotkey") {
    base.params = { sequence: "Ctrl+L" };
  } else if (action === "scroll") {
    base.intent = "scroll_list";
    base.params = { direction: "down", amount: 3 };
  } else if (action === "screenshot") {
    base.intent = "capture_screenshot";
  }
  return base;
}
function probeState(detail?: AvailabilityProbe) {
  if (!detail?.detected) {
    return {
      label: "components.rpa.RPAWorkbench.kc623317d",
      variant: "secondary" as const
    };
  }
  if (detail.importable) {
    return {
      label: "components.rpa.RPAWorkbench.k6965af3b",
      variant: "default" as const
    };
  }
  return {
    label: "components.rpa.RPAWorkbench.k71ca3eb3",
    variant: "destructive" as const
  };
}
function parseJsonObject(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return {};
  const parsed = JSON.parse(trimmed);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(translateCurrentClient(ag("3d763c86")));
  }
  return parsed;
}
function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function stringValue(value: unknown) {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}
function firstString(...values: unknown[]) {
  for (const value of values) {
    const text = stringValue(value);
    if (text) return text;
  }
  return "";
}
function collectCandidateRecords(value: unknown, out: Record<string, unknown>[] = [], depth = 0) {
  if (depth > 4 || out.length >= 30) return out;
  if (Array.isArray(value)) {
    for (const item of value) {
      if (isPlainRecord(item)) {
        out.push(item);
      }
      if (out.length >= 30) break;
    }
    return out;
  }
  if (!isPlainRecord(value)) return out;
  for (const item of Object.values(value)) {
    if (Array.isArray(item)) {
      collectCandidateRecords(item, out, depth + 1);
    } else if (isPlainRecord(item)) {
      collectCandidateRecords(item, out, depth + 1);
    }
    if (out.length >= 30) break;
  }
  return out;
}
function normalizeObservationCandidate(value: Record<string, unknown>): ObservationCandidate | null {
  const nestedTarget = isPlainRecord(value.target) ? value.target : {};
  const nestedSelector = isPlainRecord(nestedTarget.selector) ? nestedTarget.selector : {};
  const nestedWindow = isPlainRecord(nestedTarget.window) ? nestedTarget.window : {};
  const candidate: ObservationCandidate = {
    css: firstString(value.css, value.selector, nestedSelector.css),
    selector: firstString(value.selector, nestedSelector.selector),
    elementId: firstString(value.elementId, value.id),
    name: firstString(value.name, value.text, value.label),
    role: firstString(value.role),
    controlType: firstString(value.controlType, value.type),
    automationId: firstString(value.automationId),
    windowTitle: firstString(value.windowTitle, nestedWindow.title),
    confidence: typeof value.confidence === "number" ? value.confidence : undefined,
    source: firstString(value.source) || "computer_use_observe",
  };
  if (!candidate.css && !candidate.selector && !candidate.elementId && !candidate.name && !candidate.controlType && !candidate.role) {
    return null;
  }
  return candidate;
}
function extractObservationCandidates(payload: unknown) {
  const raw = collectCandidateRecords(payload);
  const candidates: ObservationCandidate[] = [];
  for (const item of raw) {
    const normalized = normalizeObservationCandidate(item);
    if (normalized) {
      candidates.push(normalized);
    }
    if (candidates.length >= 8) break;
  }
  return candidates;
}
function candidateSelector(candidate?: ObservationCandidate | null) {
  if (!candidate) return "";
  return firstString(
    candidate.css,
    candidate.selector,
    candidate.elementId ? `elementId:${candidate.elementId}` : "",
    candidate.automationId ? `automationId:${candidate.automationId}` : "",
    candidate.name ? `name:${candidate.name}` : "",
  );
}
function summarizeObservation(payload: unknown, candidates: ObservationCandidate[]) {
  const record = isPlainRecord(payload) ? payload : {};
  const scene = isPlainRecord(record.scene) ? record.scene : {};
  const windowPayload = isPlainRecord(record.window) ? record.window : {};
  const activeWindow = isPlainRecord(record.activeWindow) ? record.activeWindow : {};
  const best = candidates[0];
  return {
    observedAt: new Date().toISOString(),
    windowTitle: firstString(windowPayload.title, activeWindow.title, scene.windowTitle, best?.windowTitle),
    candidateCount: candidates.length,
    bestSelector: candidateSelector(best),
    bestName: best?.name,
    bestRole: best?.role || best?.controlType,
  };
}
function parseRunIdsInput(value: string) {
  return Array.from(new Set(value.split(/[\s,，]+/).map(item => item.trim()).filter(Boolean)));
}
function readRunScriptName(metadata?: Record<string, unknown>) {
  const script = metadata?.script;
  if (script && typeof script === "object" && !Array.isArray(script)) {
    const name = (script as {
      name?: unknown;
    }).name;
    if (typeof name === "string" && name.trim()) {
      return name;
    }
  }
  return null;
}
function readRunRobotFile(metadata?: Record<string, unknown>) {
  const robotFile = metadata?.robotFile;
  return typeof robotFile === "string" && robotFile.trim() ? robotFile : null;
}
function readRunApprovalCount(metadata?: Record<string, unknown>) {
  const script = metadata?.script;
  if (script && typeof script === "object" && !Array.isArray(script)) {
    const steps = (script as {
      steps?: Array<{
        approval?: unknown;
      }>;
    }).steps;
    if (Array.isArray(steps)) {
      return steps.filter(item => !!item?.approval).length;
    }
  }
  return 0;
}
function readRunUnavailableLibraries(metadata?: Record<string, unknown>) {
  const availability = metadata?.availability;
  if (!availability || typeof availability !== "object" || Array.isArray(availability)) {
    return [];
  }
  const libraryDetails = (availability as {
    libraryDetails?: Record<string, AvailabilityProbe>;
  }).libraryDetails;
  if (!libraryDetails || typeof libraryDetails !== "object") {
    return [];
  }
  return Object.entries(libraryDetails).filter(([, detail]) => detail && detail.detected && !detail.importable).map(([name]) => name);
}
export function RPAWorkbench() {
  const {
    toast
  } = useToast();
  const t = useT();
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [availability, setAvailability] = useState<AvailabilityPayload>({});
  const [drafts, setDrafts] = useState<DraftPayload[]>([]);
  const [scripts, setScripts] = useState<ScriptPayload[]>([]);
  const [templates, setTemplates] = useState<TemplatePayload[]>([]);
  const [showArchivedDrafts, setShowArchivedDrafts] = useState(false);
  const [showArchivedTemplates, setShowArchivedTemplates] = useState(false);
  const [templateSummary, setTemplateSummary] = useState<TemplateSummaryPayload>({});
  const [templateHistory, setTemplateHistory] = useState<TemplateHistoryRecord[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [approvalDrafts, setApprovalDrafts] = useState<Record<string, string>>({});
  const [selectedDraftId, setSelectedDraftId] = useState<string>("");
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>("");
  const [studioDirty, setStudioDirty] = useState(false);
  const [templateNote, setTemplateNote] = useState("");
  const [compileRunId, setCompileRunId] = useState("");
  const [variablesText, setVariablesText] = useState("{}");
  const [existingRobotFile, setExistingRobotFile] = useState("");
  const [existingVariablesText, setExistingVariablesText] = useState("{}");
  const [cwd, setCwd] = useState("");
  const [outputDir, setOutputDir] = useState("");
  const [timeoutMs, setTimeoutMs] = useState("600000");
  const [latestResult, setLatestResult] = useState<unknown>(null);
  const [recordings, setRecordings] = useState<RecordingSessionPayload[]>([]);
  const [activeRecording, setActiveRecording] = useState<RecordingSessionPayload | null>(null);
  const [recordingName, setRecordingName] = useState("");
  const [recordingGoal, setRecordingGoal] = useState("");
  const [recordingTargetMode, setRecordingTargetMode] = useState("agent_browser");
  const [recordingBrowserKind, setRecordingBrowserKind] = useState("chrome");
  const [recordingAppId, setRecordingAppId] = useState("desktop");
  const [recordingAction, setRecordingAction] = useState("click");
  const [recordingIntent, setRecordingIntent] = useState("");
  const [recordingSelector, setRecordingSelector] = useState("");
  const [recordingX, setRecordingX] = useState("");
  const [recordingY, setRecordingY] = useState("");
  const [recordingVariableName, setRecordingVariableName] = useState("");
  const [recordingSensitive, setRecordingSensitive] = useState(false);
  const [recordingParamsText, setRecordingParamsText] = useState("{}");
  const [desktopLiveSessionId, setDesktopLiveSessionId] = useState("");
  const [desktopLiveError, setDesktopLiveError] = useState("");
  const [desktopLiveLoading, setDesktopLiveLoading] = useState(false);
  const [latestComputerObservation, setLatestComputerObservation] = useState<Record<string, unknown> | null>(null);
  const [computerSampling, setComputerSampling] = useState(false);
  const [recordAndForward, setRecordAndForward] = useState(false);
  const [browserCaptureActive, setBrowserCaptureActive] = useState(false);
  const [browserCapturePolling, setBrowserCapturePolling] = useState(false);
  const [captureAssistantActive, setCaptureAssistantActive] = useState(false);
  const [captureAssistantStage, setCaptureAssistantStage] = useState<"idle" | "creating" | "preparing" | "starting" | "active" | "captured" | "failed">("idle");
  const [computerApps, setComputerApps] = useState<ComputerUseAppPayload[]>([]);
  const [computerAppsLoading, setComputerAppsLoading] = useState(false);
  const [computerAppsError, setComputerAppsError] = useState("");
  const [appPickerOpen, setAppPickerOpen] = useState(false);
  const [appSearch, setAppSearch] = useState("");
  const [studioRightPanel, setStudioRightPanel] = useState<"properties" | "variables" | "elements" | "runs" | "diagnostics">("properties");
  const [showLegacyPanel, setShowLegacyPanel] = useState(false);
  const liveImageRef = useRef<HTMLImageElement | null>(null);
  const liveDragStartRef = useRef<{ x: number; y: number; mapping: Record<string, unknown> } | null>(null);
  const [draftStepEdits, setDraftStepEdits] = useState<Record<string, string>>({});
  const [draftStepOrder, setDraftStepOrder] = useState<string[]>([]);
  const [draftEditorView, setDraftEditorView] = useState<"steps" | "canvas">("canvas");
  const [selectedDraftStepKey, setSelectedDraftStepKey] = useState("");
  const [draftVariableRows, setDraftVariableRows] = useState<DraftVariableRow[]>([]);
  const [stepValidation, setStepValidation] = useState<StepValidationResult | null>(null);
  const selectedDraft = useMemo(() => drafts.find(item => item.id === selectedDraftId) || null, [drafts, selectedDraftId]);
  const selectedTemplate = useMemo(() => templates.find(item => item.id === selectedTemplateId) || null, [templates, selectedTemplateId]);
  const orderedDraftSteps = useMemo(() => draftStepOrder.map(key => ({
    key,
    step: safeJsonParse<EditableDraftStep | null>(draftStepEdits[key] || "", null)
  })).filter((item): item is { key: string; step: EditableDraftStep } => !!item.step), [draftStepEdits, draftStepOrder]);
  const selectedBuilderStep = useMemo(() => orderedDraftSteps.find(item => item.key === selectedDraftStepKey)?.step || null, [orderedDraftSteps, selectedDraftStepKey]);
  const draftActionOptions = useMemo(() => [
    ["open_app", t("components.rpa.RPAWorkbench.actionOpenApp")],
    ["wait", t("components.rpa.RPAWorkbench.actionWait")],
    ["click", t("components.rpa.RPAWorkbench.actionClick")],
    ["type_text", t("components.rpa.RPAWorkbench.actionTypeText")],
    ["hotkey", t("components.rpa.RPAWorkbench.actionHotkey")],
    ["scroll", t("components.rpa.RPAWorkbench.actionScroll")],
    ["screenshot", t("components.rpa.RPAWorkbench.actionScreenshot")]
  ] as Array<[string, string]>, [t]);
  const studioActionGroups = useMemo(() => [
    {
      key: "app",
      title: t("components.rpa.RPAWorkbench.studioGroupApp"),
      actions: [["open_app", t("components.rpa.RPAWorkbench.actionOpenApp")], ["wait_window", t("components.rpa.RPAWorkbench.studioActionWaitWindow")]]
    },
    {
      key: "browser",
      title: t("components.rpa.RPAWorkbench.studioGroupBrowser"),
      actions: [["browser_click", t("components.rpa.RPAWorkbench.studioActionBrowserClick")], ["browser_type", t("components.rpa.RPAWorkbench.studioActionBrowserType")], ["browser_assert", t("components.rpa.RPAWorkbench.studioActionBrowserAssert")], ["browser_extract", t("components.rpa.RPAWorkbench.studioActionBrowserExtract")]]
    },
    {
      key: "desktop",
      title: t("components.rpa.RPAWorkbench.studioGroupDesktop"),
      actions: [["click", t("components.rpa.RPAWorkbench.actionClick")], ["type_text", t("components.rpa.RPAWorkbench.actionTypeText")], ["hotkey", t("components.rpa.RPAWorkbench.actionHotkey")], ["scroll", t("components.rpa.RPAWorkbench.actionScroll")], ["screenshot", t("components.rpa.RPAWorkbench.actionScreenshot")]]
    },
    {
      key: "flow",
      title: t("components.rpa.RPAWorkbench.studioGroupFlow"),
      actions: [["wait", t("components.rpa.RPAWorkbench.actionWait")], ["if", t("components.rpa.RPAWorkbench.studioActionIf")], ["loop", t("components.rpa.RPAWorkbench.studioActionLoop")], ["try_catch", t("components.rpa.RPAWorkbench.studioActionTryCatch")], ["subflow", t("components.rpa.RPAWorkbench.studioActionSubflow")], ["comment", t("components.rpa.RPAWorkbench.studioActionComment")]]
    },
    {
      key: "data",
      title: t("components.rpa.RPAWorkbench.studioGroupData"),
      actions: [["set_variable", t("components.rpa.RPAWorkbench.studioActionSetVariable")], ["file_copy", t("components.rpa.RPAWorkbench.studioActionFileCopy")], ["http_request", t("components.rpa.RPAWorkbench.studioActionHttpRequest")], ["ocr", t("components.rpa.RPAWorkbench.studioActionOcr")], ["llm_call", t("components.rpa.RPAWorkbench.studioActionLlmCall")], ["assert_text", t("components.rpa.RPAWorkbench.studioActionAssertText")]]
    }
  ] as Array<{ key: string; title: string; actions: Array<[string, string]> }>, [t]);
  const selectedComputerApp = useMemo(() => computerApps.find(app => {
    const appId = String(app.appId || app.id || app.profileId || "");
    return appId === recordingAppId;
  }) || null, [computerApps, recordingAppId]);
  const selectedComputerAppLabel = selectedComputerApp?.displayName || selectedComputerApp?.name || (recordingAppId === "desktop" ? t("components.rpa.RPAWorkbench.studioManualDesktop") : recordingAppId) || "desktop";
  const targetLockLooksLikeAdmin = useMemo(() => /v8 agent os|v8 os|localhost:9528|127\.0\.0\.1:9528|admin/i.test([
    recordingAppId,
    selectedComputerApp?.displayName,
    selectedComputerApp?.name,
    selectedComputerApp?.topWindowTitle
  ].filter(Boolean).join(" ")), [recordingAppId, selectedComputerApp]);
  const appPickerOptions = useMemo(() => {
    const query = appSearch.trim().toLowerCase();
    const normalize = (value: unknown) => String(value || "").toLowerCase();
    const options = [
      {
        id: "desktop",
        label: t("components.rpa.RPAWorkbench.studioManualDesktop"),
        subtitle: t("components.rpa.RPAWorkbench.studioAppGroupManual"),
        group: "manual",
        running: false,
        launchable: true,
        searchText: [
          "desktop",
          t("components.rpa.RPAWorkbench.studioManualDesktop"),
          t("components.rpa.RPAWorkbench.studioAppGroupManual"),
        ].join(" "),
      },
      ...computerApps.map(app => {
        const id = String(app.appId || app.id || app.profileId || "");
        const sources = Array.isArray(app.sources) ? app.sources : [];
        const runningWindows = Array.isArray(app.runningWindows) ? app.runningWindows : [];
        const windowTitle = firstString(app.topWindowTitle, ...runningWindows.map(window => window?.title));
        const group = app.isRunning ? "running" : app.learned || sources.includes("computer_use_memory") ? "learned" : "launchable";
        const label = app.displayName || app.name || id;
        const subtitle = windowTitle || (group === "learned" ? t("components.rpa.RPAWorkbench.studioAppGroupLearned") : app.isRunning ? t("components.rpa.RPAWorkbench.studioAppGroupRunning") : t("components.rpa.RPAWorkbench.studioAppGroupLaunchable"));
        return {
          id,
          label,
          subtitle,
          group,
          running: Boolean(app.isRunning),
          launchable: Boolean(app.launchable),
          learned: Boolean(app.learned || sources.includes("computer_use_memory")),
          searchText: [
            id,
            label,
            subtitle,
            app.profileId,
            ...(Array.isArray(app.aliases) ? app.aliases : []),
            ...(Array.isArray(app.processNames) ? app.processNames : []),
            ...(Array.isArray(app.titlePatterns) ? app.titlePatterns : []),
            ...runningWindows.flatMap(window => [window?.title, window?.processName, window?.className]),
          ].filter(Boolean).join(" "),
        };
      }).filter(item => item.id)
    ];
    return options.filter(item => !query || normalize(item.searchText).includes(query));
  }, [appSearch, computerApps, t]);
  const groupedAppPickerOptions = useMemo(() => {
    const order = ["running", "launchable", "learned", "manual"];
    return order.map(group => ({
      group,
      items: appPickerOptions.filter(item => item.group === group),
    })).filter(entry => entry.items.length);
  }, [appPickerOptions]);
  const studioPanelOptions = useMemo(() => [
    ["properties", t("components.rpa.RPAWorkbench.studioPanelProperties")],
    ["variables", t("components.rpa.RPAWorkbench.studioPanelVariables")],
    ["elements", t("components.rpa.RPAWorkbench.studioPanelElements")],
    ["runs", t("components.rpa.RPAWorkbench.studioPanelRuns")],
    ["diagnostics", t("components.rpa.RPAWorkbench.studioPanelDiagnostics")]
  ] as Array<[typeof studioRightPanel, string]>, [t]);
  const selectedBuilderParams = useMemo(() => isPlainRecord(selectedBuilderStep?.params) ? selectedBuilderStep.params : {}, [selectedBuilderStep]);
  const selectedBuilderTarget = useMemo(() => isPlainRecord(selectedBuilderStep?.target) ? selectedBuilderStep.target : {}, [selectedBuilderStep]);
  const selectedBuilderSelector = useMemo(() => isPlainRecord(selectedBuilderTarget.selector) ? selectedBuilderTarget.selector : {}, [selectedBuilderTarget]);
  const selectedBuilderCoordinate = useMemo(() => isPlainRecord((selectedBuilderStep as Record<string, unknown> | null)?.coordinate) ? (selectedBuilderStep as Record<string, unknown>).coordinate as Record<string, unknown> : {}, [selectedBuilderStep]);
  const selectedBuilderVerification = useMemo(() => isPlainRecord(selectedBuilderStep?.verification) ? selectedBuilderStep.verification : {}, [selectedBuilderStep]);
  const selectedBuilderActionKind = useMemo(() => actionKind(stepActionName(selectedBuilderStep)), [selectedBuilderStep]);
  const capturePoolItems = useMemo(() => Array.isArray(activeRecording?.capturePool) ? activeRecording.capturePool : [], [activeRecording]);
  const captureAssistantBusy = captureAssistantStage === "creating" || captureAssistantStage === "preparing" || captureAssistantStage === "starting";
  const captureAssistantStageLabel = useMemo(() => {
    const keyByStage: Record<typeof captureAssistantStage, string> = {
      idle: "components.rpa.RPAWorkbench.studioStartCaptureAssistant",
      creating: "components.rpa.RPAWorkbench.studioCaptureStageCreating",
      preparing: "components.rpa.RPAWorkbench.studioCaptureStagePreparing",
      starting: "components.rpa.RPAWorkbench.studioCaptureStageStarting",
      active: "components.rpa.RPAWorkbench.studioCaptureStageActive",
      captured: "components.rpa.RPAWorkbench.studioCaptureStageCaptured",
      failed: "components.rpa.RPAWorkbench.studioCaptureStageFailed"
    };
    return t(keyByStage[captureAssistantStage]);
  }, [captureAssistantStage, t]);
  const nativeHotkeyBackend = useMemo(() => {
    const assistant = isPlainRecord(activeRecording?.captureAssistant) ? activeRecording.captureAssistant as Record<string, unknown> : {};
    const backend = assistant.nativeHotkeyBackend;
    return isPlainRecord(backend) ? backend : null;
  }, [activeRecording]);
  const objectLibraryItems = useMemo(() => Array.isArray(activeRecording?.objectLibrary) ? activeRecording.objectLibrary : Array.isArray(selectedDraft?.objectLibrary) ? selectedDraft.objectLibrary : [], [activeRecording, selectedDraft]);
  const selectableElements = useMemo(() => [
    ...objectLibraryItems.map(item => ({ ...item, sourceBucket: "library" as const, optionId: item.elementId || item.sourceTempElementId || item.tempElementId || captureItemLabel(item) })),
    ...capturePoolItems.map(item => ({ ...item, sourceBucket: "pool" as const, optionId: item.tempElementId || captureItemLabel(item) }))
  ], [capturePoolItems, objectLibraryItems]);
  const selectedLoopStartKey = firstString(selectedBuilderParams.loopStartStepKey, selectedBuilderParams.startStepKey);
  const selectedLoopEndKey = firstString(selectedBuilderParams.loopEndStepKey, selectedBuilderParams.endStepKey);
  const selectedLoopStartIndex = selectedLoopStartKey ? orderedDraftSteps.findIndex(item => item.key === selectedLoopStartKey) : -1;
  const selectedLoopEndIndex = selectedLoopEndKey ? orderedDraftSteps.findIndex(item => item.key === selectedLoopEndKey) : -1;
  const selectedLoopInvalid = selectedBuilderActionKind === "loop" && selectedLoopStartKey && selectedLoopEndKey && (selectedLoopStartIndex < 0 || selectedLoopEndIndex < 0 || selectedLoopStartIndex >= selectedLoopEndIndex);
  const isArchivedDraft = useCallback((draft?: DraftPayload | null) => Boolean(draft?.archivedAt || draft?.metadata?.archivedAt), []);
  const isArchivedTemplate = useCallback((template?: TemplatePayload | null) => Boolean(template?.archivedAt || template?.metadata?.archivedAt), []);
  useEffect(() => {
    if (!selectedDraft) {
      return;
    }
    const next: Record<string, string> = {};
    const order: string[] = [];
    (selectedDraft.steps || []).forEach((step, index) => {
      const key = stableDraftStepKey(selectedDraft.id, step, index);
      next[key] = JSON.stringify(step, null, 2);
      order.push(key);
    });
    setDraftStepEdits(next);
    setDraftStepOrder(order);
    setSelectedDraftStepKey(order[0] || "");
    setStepValidation(null);
    setRecordingName(selectedDraft.name || "");
    setRecordingGoal(selectedDraft.goal || "");
    setRecordingAppId(selectedDraft.appId || "desktop");
    setStudioDirty(false);
    setDraftVariableRows((selectedDraft.variables || []).map((variable, index) => ({
      id: `${selectedDraft.id}:var:${index}:${variable.name || "var"}`,
      name: variable.name || "",
      type: variable.type || "text",
      required: Boolean(variable.required),
      defaultValue: variable.defaultValue == null ? "" : String(variable.defaultValue),
      secretName: variable.secretName || "",
      sensitive: Boolean(variable.sensitive || variable.secretName),
    })));
  }, [selectedDraft]);
  const isRpaRun = (run: RunRecord) => run.run_type === "rpa" || run.metadata?.runtime === "rpa" || run.metadata?.mode === "draft" || run.metadata?.mode === "existing_robot" || String(run.session_id || "").startsWith("rpa:");
  const isRpaApproval = (approval: ApprovalRecord) => String(approval.approval_kind || "").startsWith("rpa") || !!approval.request?.rpa || String(approval.session_id || "").startsWith("rpa:");
  const rpaRuns = useMemo(() => runs.filter(isRpaRun).slice(0, 8), [runs]);
  const rpaApprovals = useMemo(() => approvals.filter(isRpaApproval), [approvals]);
  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [availabilityRes, draftsRes, scriptsRes, templatesRes, recordingsRes, approvalsRes, runsRes] = await Promise.all([fetch("/api/rpa/availability", {
        cache: "no-store"
      }), fetch(`/api/rpa/drafts?includeArchived=${showArchivedDrafts ? "true" : "false"}`, {
        cache: "no-store"
      }), fetch("/api/rpa/scripts", {
        cache: "no-store"
      }), fetch(`/api/rpa/templates?includeArchived=${showArchivedTemplates ? "true" : "false"}`, {
        cache: "no-store"
      }), fetch("/api/rpa/recordings?limit=12", {
        cache: "no-store"
      }), fetch("/api/approvals?status=pending", {
        cache: "no-store"
      }), fetch("/api/runs?limit=20", {
        cache: "no-store"
      })]);
      const nextAvailability = availabilityRes.ok ? await availabilityRes.json() : {};
      const nextDraftsPayload = draftsRes.ok ? await draftsRes.json() : {};
      const nextScriptsPayload = scriptsRes.ok ? await scriptsRes.json() : {};
      const nextTemplatesPayload = templatesRes.ok ? await templatesRes.json() : {};
      const nextRecordingsPayload = recordingsRes.ok ? await recordingsRes.json() : {};
      const approvalsData = approvalsRes.ok ? await approvalsRes.json().catch(() => ({})) : {};
      const runsData = runsRes.ok ? await runsRes.json().catch(() => ({})) : {};
      setAvailability(nextAvailability || {});
      const nextDrafts = Array.isArray(nextDraftsPayload?.drafts) ? nextDraftsPayload.drafts : [];
      const nextScripts = Array.isArray(nextScriptsPayload?.scripts) ? nextScriptsPayload.scripts : [];
      const nextTemplates = Array.isArray(nextTemplatesPayload?.templates) ? nextTemplatesPayload.templates : [];
      const nextRecordings = Array.isArray(nextRecordingsPayload?.recordings) ? nextRecordingsPayload.recordings : [];
      setDrafts(nextDrafts);
      setScripts(nextScripts);
      setTemplates(nextTemplates);
      setRecordings(nextRecordings);
      setTemplateSummary(nextTemplatesPayload?.summary || {});
      setApprovals(Array.isArray(approvalsData?.approvals) ? approvalsData.approvals : []);
      setRuns(Array.isArray(runsData?.runs) ? runsData.runs : []);
      if (selectedDraftId && !nextDrafts.some((draft: DraftPayload) => draft.id === selectedDraftId)) {
        setSelectedDraftId("");
      }
      if (!selectedTemplateId && nextTemplates[0]?.id) {
        setSelectedTemplateId(nextTemplates[0].id);
      }
    } catch (error) {
      console.error("[RPAWorkbench] load failed:", error);
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.k83db87f2"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError")
      });
    } finally {
      setLoading(false);
    }
  }, [selectedDraftId, selectedTemplateId, showArchivedDrafts, showArchivedTemplates, toast, t]);
  useEffect(() => {
    void loadAll();
  }, [loadAll]);
  const loadComputerApps = useCallback(async (force = false, queryOverride = "") => {
    setComputerAppsLoading(true);
    setComputerAppsError("");
    try {
      const query = queryOverride.trim();
      const response = await fetch("/api/computer-use/apps", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          limit: 100,
          query: query || undefined,
          includeRunning: true,
          includeLearned: true,
          forceRefresh: force
        })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message = firstString(payload?.detail, payload?.error, payload?.message) || `HTTP ${response.status}`;
        setComputerAppsError(message);
        toast({
          variant: "destructive",
          title: t("components.rpa.RPAWorkbench.studioAppListFailed"),
          description: message
        });
        return;
      }
      const apps = Array.isArray(payload?.apps)
        ? payload.apps
        : Array.isArray(payload?.items)
          ? payload.items
          : Array.isArray(payload?.data?.apps)
            ? payload.data.apps
            : [];
      setComputerApps(apps);
      if (!apps.length) {
        setComputerAppsError(t("components.rpa.RPAWorkbench.studioAppListEmpty"));
      }
    } catch (error) {
      console.warn("[RPAWorkbench] app list failed:", error);
      const message = error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError");
      setComputerAppsError(message);
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.studioAppListFailed"),
        description: message
      });
    } finally {
      setComputerAppsLoading(false);
    }
  }, [toast, t]);
  useEffect(() => {
    void loadComputerApps(false);
  }, [loadComputerApps]);
  useEffect(() => {
    if (!appPickerOpen) return;
    const timer = window.setTimeout(() => {
      void loadComputerApps(false, appSearch);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [appPickerOpen, appSearch, loadComputerApps]);
  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!studioDirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [studioDirty]);
  useEffect(() => {
    return () => {
      const sessionId = desktopLiveSessionId;
      if (!sessionId) return;
      void fetch("/api/desktop-live/release", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          sessionId
        })
      }).catch(() => undefined);
    };
  }, [desktopLiveSessionId]);
  useEffect(() => {
    if (!selectedTemplateId) {
      setTemplateHistory([]);
      return;
    }
    let cancelled = false;
    const loadHistory = async () => {
      try {
        const res = await fetch(`/api/rpa/templates/${encodeURIComponent(selectedTemplateId)}/history`, {
          cache: "no-store"
        });
        const data = res.ok ? await res.json() : {};
        if (!cancelled) {
          setTemplateHistory(Array.isArray(data?.history) ? data.history : []);
        }
      } catch {
        if (!cancelled) {
          setTemplateHistory([]);
        }
      }
    };
    void loadHistory();
    return () => {
      cancelled = true;
    };
  }, [selectedTemplateId]);
  const runAction = async (actionKey: string, runner: () => Promise<Response>, successTitle: string) => {
    setBusyAction(actionKey);
    try {
      const res = await runner();
      const data = await res.json().catch(() => ({}));
      setLatestResult(data);
      if (!res.ok) {
        throw new Error(data?.detail || data?.error || tg(t, "8fdc4112"));
      }
      toast({
        title: successTitle,
        description: typeof data?.status === "string" ? tg(t, "2bf0d2b2", {
          value1: data.status
        }) : tg(t, "3d8c4a5f")
      });
      await loadAll();
      return data;
    } catch (error) {
      console.error(`[RPAWorkbench] ${actionKey} failed:`, error);
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.k2e9cdd7b"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError")
      });
      throw error;
    } finally {
      setBusyAction(null);
    }
  };
  const commonPayload = () => ({
    cwd: cwd.trim() || undefined,
    outputDir: outputDir.trim() || undefined
  });
  const handlePrepareDesktopLive = async () => {
    setDesktopLiveLoading(true);
    setDesktopLiveError("");
    try {
      const res = await fetch("/api/desktop-live/session", {
        method: "POST",
        cache: "no-store"
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.sessionId) {
        throw new Error(data?.error || data?.detail || t("components.rpa.RPAWorkbench.desktopLiveFailed"));
      }
      setDesktopLiveSessionId(String(data.sessionId));
      setLatestResult(data);
      toast({
        title: t("components.rpa.RPAWorkbench.desktopLiveReady"),
        description: t("components.rpa.RPAWorkbench.desktopLiveClickHint")
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : t("components.rpa.RPAWorkbench.desktopLiveFailed");
      setDesktopLiveError(message);
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.desktopLiveFailed"),
        description: message
      });
    } finally {
      setDesktopLiveLoading(false);
    }
  };
  const handleReleaseDesktopLive = async () => {
    const sessionId = desktopLiveSessionId;
    if (!sessionId) return;
    setDesktopLiveLoading(true);
    try {
      await fetch("/api/desktop-live/release", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          sessionId
        })
      });
      setDesktopLiveSessionId("");
    } finally {
      setDesktopLiveLoading(false);
    }
  };
  const handleBrowserCaptureStart = async () => {
    if (!activeRecording?.recordingSessionId) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.noActiveRecording"),
        description: t("components.rpa.RPAWorkbench.startRecordingFirst")
      });
      return;
    }
    const data = await runAction(`recording:browser-start:${activeRecording.recordingSessionId}`, () => fetch(`/api/rpa/recordings/${encodeURIComponent(activeRecording.recordingSessionId)}/browser-capture/start`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        appId: recordingAppId.trim() || undefined,
        windowTitle: firstString(latestComputerObservation?.summary && isPlainRecord(latestComputerObservation.summary) ? latestComputerObservation.summary.windowTitle : undefined)
      })
    }), t("components.rpa.RPAWorkbench.browserCaptureStarted"));
    if (data?.ok) {
      setBrowserCaptureActive(true);
    }
  };
  const handleBrowserCapturePoll = useCallback(async (options?: { quiet?: boolean }) => {
    if (!activeRecording?.recordingSessionId || browserCapturePolling) return;
    setBrowserCapturePolling(true);
    try {
      const res = await fetch(`/api/rpa/recordings/${encodeURIComponent(activeRecording.recordingSessionId)}/browser-capture/poll`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          appId: recordingAppId.trim() || undefined,
          maxEvents: 80
        })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (!options?.quiet) {
          throw new Error(data?.detail || data?.error || t("components.rpa.RPAWorkbench.browserCapturePollFailed"));
        }
        return;
      }
      if (data?.recording) {
        setActiveRecording(data.recording as RecordingSessionPayload);
      }
      if (!options?.quiet && Number(data?.appendedCount || 0) > 0) {
        toast({
          title: t("components.rpa.RPAWorkbench.browserCapturePolled"),
          description: t("components.rpa.RPAWorkbench.browserCaptureEvents", {
            count: Number(data.appendedCount || 0)
          })
        });
      }
      setLatestResult(data);
    } catch (error) {
      if (!options?.quiet) {
        toast({
          variant: "destructive",
          title: t("components.rpa.RPAWorkbench.browserCapturePollFailed"),
          description: error instanceof Error ? error.message : String(error)
        });
      }
    } finally {
      setBrowserCapturePolling(false);
    }
  }, [activeRecording?.recordingSessionId, browserCapturePolling, recordingAppId, t, toast]);
  const handleBrowserCaptureStop = async () => {
    if (!activeRecording?.recordingSessionId) return;
    const data = await runAction(`recording:browser-stop:${activeRecording.recordingSessionId}`, () => fetch(`/api/rpa/recordings/${encodeURIComponent(activeRecording.recordingSessionId)}/browser-capture/stop`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        appId: recordingAppId.trim() || undefined
      })
    }), t("components.rpa.RPAWorkbench.browserCaptureStopped"));
    if (data?.recording) {
      setActiveRecording(data.recording as RecordingSessionPayload);
    }
    setBrowserCaptureActive(false);
  };
  useEffect(() => {
    if (!browserCaptureActive || !activeRecording?.recordingSessionId || activeRecording.state !== "recording") {
      return;
    }
    const timer = window.setInterval(() => {
      void handleBrowserCapturePoll({ quiet: true });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [activeRecording?.recordingSessionId, activeRecording?.state, browserCaptureActive, handleBrowserCapturePoll]);
  const refreshRecording = useCallback(async (recordingId: string) => {
    const res = await fetch(`/api/rpa/recordings/${encodeURIComponent(recordingId)}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok && data?.recordingSessionId) {
      setActiveRecording(data as RecordingSessionPayload);
    }
    return data;
  }, []);

  const buildRecordingStartPayload = () => {
    const targetAppId = recordingAppId.trim() || String(selectedComputerApp?.appId || selectedComputerApp?.id || selectedComputerApp?.profileId || "desktop");
    return {
      name: recordingName.trim() || undefined,
      goal: recordingGoal.trim() || recordingName.trim() || undefined,
      targetMode: recordingTargetMode,
      browserKind: recordingTargetMode === "agent_browser" ? recordingBrowserKind : undefined,
      appId: targetAppId,
      activeApp: selectedComputerApp ? {
        appId: targetAppId,
        displayName: selectedComputerApp.displayName || selectedComputerApp.name,
        topWindowTitle: selectedComputerApp.topWindowTitle,
        isRunning: selectedComputerApp.isRunning,
        launchable: selectedComputerApp.launchable
      } : undefined,
      targetLock: {
        enabled: true,
        mode: recordingTargetMode,
        appId: targetAppId,
        label: selectedComputerAppLabel,
        browserKind: recordingTargetMode === "agent_browser" ? recordingBrowserKind : undefined,
        ignoreAdminSurface: true,
        consoleTargetBlocked: targetLockLooksLikeAdmin
      },
      captureOptions: {
        screenshotAnchors: true,
        domSelectors: true,
        windowInfo: true,
        keyboardInput: true
      }
    };
  };

  const confirmLoseStudioChanges = () => !studioDirty || window.confirm(t("components.rpa.RPAWorkbench.studioUnsavedConfirm"));

  const resetStudioWorkspace = () => {
    if (!confirmLoseStudioChanges()) return;
    setSelectedDraftId("");
    setDraftStepEdits({});
    setDraftStepOrder([]);
    setSelectedDraftStepKey("");
    setDraftVariableRows([]);
    setStepValidation(null);
    setRecordingName("");
    setRecordingGoal("");
    setRecordingAppId("desktop");
    setLatestResult(null);
    setStudioDirty(false);
  };

  const handleSelectDraft = (draftId: string) => {
    if (draftId === selectedDraftId) return;
    if (!confirmLoseStudioChanges()) return;
    setSelectedDraftId(draftId);
    setStudioDirty(false);
  };

  const buildDraftStudioPayload = (options?: { saveAs?: boolean }) => {
    const steps = parseDraftStepsFromBuilder();
    const variables = draftVariableRows.map(row => ({
      name: row.name.trim(),
      type: row.type,
      required: row.required,
      ...(row.defaultValue ? {
        defaultValue: row.defaultValue
      } : {}),
      ...(row.sensitive ? {
        sensitive: true,
        secretName: row.secretName.trim() || row.name.trim()
      } : {})
    })).filter(row => row.name);
    const baseName = recordingName.trim() || selectedDraft?.name || t("components.rpa.RPAWorkbench.studioCurrentUnsaved");
    return {
      name: options?.saveAs ? `${baseName} Copy` : baseName,
      goal: recordingGoal.trim() || selectedDraft?.goal || recordingName.trim() || undefined,
      appId: recordingAppId.trim() || selectedDraft?.appId || "desktop",
      steps,
      variables,
      objectLibrary: objectLibraryItems,
      metadata: {
        source: "manual_canvas",
        editedBy: "admin_ui",
        editedFrom: "rpa_canvas_studio",
        targetLock: buildRecordingStartPayload().targetLock,
      },
    };
  };

  const handleCreateDraftFromStudio = async (options?: { saveAs?: boolean }) => {
    let payload: ReturnType<typeof buildDraftStudioPayload>;
    try {
      payload = buildDraftStudioPayload(options);
    } catch (error) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.invalidDraftStep"),
        description: error instanceof Error ? error.message : t("components.rpa.RPAWorkbench.invalidJsonObject")
      });
      return;
    }
    const data = await runAction(options?.saveAs ? "draft:create:save-as" : "draft:create", () => fetch("/api/rpa/drafts", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    }), options?.saveAs ? t("components.rpa.RPAWorkbench.studioDraftSavedAs") : t("components.rpa.RPAWorkbench.studioDraftCreated"));
    if (data?.id) {
      const draft = data as DraftPayload;
      setDrafts(current => [draft, ...current.filter(item => item.id !== draft.id)]);
      setSelectedDraftId(draft.id);
      setStudioDirty(false);
    }
  };

  const startRecordingSession = async (options?: { quiet?: boolean }) => {
    if (targetLockLooksLikeAdmin) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.studioTargetBlockedTitle"),
        description: t("components.rpa.RPAWorkbench.studioTargetBlockedDescription")
      });
      return null;
    }
    const data = await runAction("recording:start", () => fetch("/api/rpa/recordings/start", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(buildRecordingStartPayload())
    }), options?.quiet ? t("components.rpa.RPAWorkbench.recordingStartedQuiet") : t("components.rpa.RPAWorkbench.recordingStarted"));
    if (data?.recordingSessionId) {
      const recording = data as RecordingSessionPayload;
      setActiveRecording(recording);
      setCaptureAssistantActive(false);
      return recording;
    }
    return null;
  };

  const ensureActiveRecording = async () => {
    if (activeRecording?.recordingSessionId && activeRecording.state === "recording") {
      return activeRecording;
    }
    return startRecordingSession({ quiet: true });
  };

  const handleCaptureAssistantStart = async () => {
    if (targetLockLooksLikeAdmin) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.studioTargetBlockedTitle"),
        description: t("components.rpa.RPAWorkbench.studioTargetBlockedDescription")
      });
      return;
    }
    try {
      setCaptureAssistantStage("creating");
      const recording = await ensureActiveRecording();
      if (!recording?.recordingSessionId) {
        setCaptureAssistantStage("failed");
        return;
      }
      setCaptureAssistantStage("preparing");
      const prepared = await runAction(`recording:capture-assistant:prepare:${recording.recordingSessionId}`, () => fetch(`/api/rpa/recordings/${encodeURIComponent(recording.recordingSessionId)}/capture-assistant/prepare-target`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          mode: recordingTargetMode,
          appId: recordingAppId.trim() || "desktop",
          label: selectedComputerAppLabel,
          ignoreAdminSurface: true,
          consoleTargetBlocked: targetLockLooksLikeAdmin,
        })
      }), t("components.rpa.RPAWorkbench.studioCaptureTargetPrepared"));
      if (prepared?.recording) {
        setActiveRecording(prepared.recording as RecordingSessionPayload);
      }
      if (prepared?.ok === false) {
        throw new Error(prepared?.reason || prepared?.detail || prepared?.error || t("components.rpa.RPAWorkbench.studioCaptureTargetPrepareFailed"));
      }
      setCaptureAssistantStage("starting");
      const data = await runAction(`recording:capture-assistant:start:${recording.recordingSessionId}`, () => fetch(`/api/rpa/recordings/${encodeURIComponent(recording.recordingSessionId)}/capture-assistant/start`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          action: "click",
          backend: "auto",
          mode: "capture_only",
          hotkey: "ctrl+alt+c",
          persistent: true,
          recordAndForward,
          targetLock: {
            enabled: true,
            mode: recordingTargetMode,
            appId: recordingAppId.trim() || "desktop",
            label: selectedComputerAppLabel,
            ignoreAdminSurface: true,
            consoleTargetBlocked: targetLockLooksLikeAdmin,
          },
        })
      }), t("components.rpa.RPAWorkbench.studioCaptureAssistantStarted"));
      if (data?.ok !== false) {
        setCaptureAssistantActive(true);
        setCaptureAssistantStage("active");
        window.setTimeout(() => {
          void refreshRecording(recording.recordingSessionId);
        }, 1800);
      }
    } catch (error) {
      setCaptureAssistantStage("failed");
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.studioCaptureAssistantFailed"),
        description: error instanceof Error ? error.message : String(error)
      });
    }
  };
  const handleCaptureAssistantStop = async () => {
    if (!activeRecording?.recordingSessionId) return;
    const data = await runAction(`recording:capture-assistant:stop:${activeRecording.recordingSessionId}`, () => fetch(`/api/rpa/recordings/${encodeURIComponent(activeRecording.recordingSessionId)}/capture-assistant/stop`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        reason: "admin_stop"
      })
    }), t("components.rpa.RPAWorkbench.studioCaptureAssistantStopped"));
    if (data?.recording) {
      setActiveRecording(data.recording as RecordingSessionPayload);
    }
    setCaptureAssistantActive(false);
    setCaptureAssistantStage("idle");
  };
  const handleCaptureAssistantPoll = useCallback(async (options?: { quiet?: boolean }) => {
    if (!activeRecording?.recordingSessionId) return;
    try {
      const res = await fetch(`/api/rpa/recordings/${encodeURIComponent(activeRecording.recordingSessionId)}/capture-assistant/poll`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        }
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (!options?.quiet) {
          throw new Error(data?.detail || data?.error || t("components.rpa.RPAWorkbench.studioCapturePollFailed"));
        }
        return;
      }
      if (data?.recording) {
        setActiveRecording(data.recording as RecordingSessionPayload);
      }
      if (typeof data?.active === "boolean") {
        setCaptureAssistantActive(Boolean(data.active));
        setCaptureAssistantStage(data.active ? "active" : "captured");
      }
      setLatestResult(data);
    } catch (error) {
      if (!options?.quiet) {
        toast({
          variant: "destructive",
          title: t("components.rpa.RPAWorkbench.studioCapturePollFailed"),
          description: error instanceof Error ? error.message : String(error)
        });
      }
    }
  }, [activeRecording?.recordingSessionId, t, toast]);
  useEffect(() => {
    if (!captureAssistantActive || !activeRecording?.recordingSessionId || activeRecording.state !== "recording") {
      return;
    }
    const timer = window.setInterval(() => {
      void handleCaptureAssistantPoll({ quiet: true });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [activeRecording?.recordingSessionId, activeRecording?.state, captureAssistantActive, handleCaptureAssistantPoll]);
  const applyElementToSelectedStep = (item: CapturePoolItem | ObjectLibraryElement) => {
    if (!selectedBuilderStep) return;
    const selectorValue = captureItemSelectorValue(item);
    const selector = isPlainRecord(item.selector) ? item.selector : {};
    const coordinate = isPlainRecord(item.coordinate) ? item.coordinate : {};
    updateSelectedDraftStep(step => {
      const target = isPlainRecord(step.target) ? step.target : {};
      const metadata = isPlainRecord(step.metadata) ? step.metadata : {};
      const nextTarget: Record<string, unknown> = {
        ...target,
        selector: selectorValue ? {
          ...(isPlainRecord(target.selector) ? target.selector : {}),
          css: firstString(selector.css, selector.selector, selectorValue),
          xpath: firstString(selector.xpath),
          role: firstString(selector.role),
          automationId: firstString(selector.automationId),
          name: firstString(selector.name),
        } : (target.selector || {}),
        window: item.targetWindow || target.window,
      };
      const nextStep: EditableDraftStep = {
        ...step,
        target: nextTarget,
        metadata: {
          ...metadata,
          elementRef: item.elementId || item.tempElementId,
          elementSource: item.source,
          captureMode: item.captureMode,
          fragileCoordinateFallback: Boolean(item.fragileCoordinateFallback),
        },
      };
      if (coordinate.x != null || coordinate.y != null) {
        (nextStep as Record<string, unknown>).coordinate = coordinate;
      }
      return nextStep;
    });
    setStudioRightPanel("properties");
  };
  const handleSaveCapturePoolItem = async (item: CapturePoolItem) => {
    if (!activeRecording?.recordingSessionId || !item.tempElementId) return;
    const res = await fetch(`/api/rpa/recordings/${encodeURIComponent(activeRecording.recordingSessionId)}/capture-pool/${encodeURIComponent(item.tempElementId)}/save`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        name: captureItemLabel(item)
      })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.studioElementSaveFailed"),
        description: data?.detail || data?.error || t("app.admin.dashboard.subagents.page.externalWorkers.unknownError")
      });
      return;
    }
    if (data?.recording) {
      setActiveRecording(data.recording as RecordingSessionPayload);
    }
    setLatestResult(data);
    toast({
      title: t("components.rpa.RPAWorkbench.studioElementSaved"),
      description: captureItemLabel(item)
    });
  };
  const submitRecordingEvent = async (payload: Record<string, unknown>) => {
    if (!activeRecording?.recordingSessionId) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.noActiveRecording"),
        description: t("components.rpa.RPAWorkbench.startRecordingFirst")
      });
      return null;
    }
    const data = await runAction(`recording:event:${activeRecording.recordingSessionId}`, () => fetch(`/api/rpa/recordings/${encodeURIComponent(activeRecording.recordingSessionId)}/events`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    }), t("components.rpa.RPAWorkbench.recordingEventAdded"));
    if (data?.recording) {
      setActiveRecording(data.recording as RecordingSessionPayload);
    }
    return data;
  };
  const captureComputerObservation = async (options?: {
    toastResult?: boolean;
    updateForm?: boolean;
  }) => {
    const res = await fetch("/api/computer-use/observe", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        goal: "rpa_recording_observe",
        depthLimit: 4,
        elementLimit: 30,
        includeScreenshot: false
      })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data?.detail || data?.error || t("components.rpa.RPAWorkbench.computerSampleFailed"));
    }
    const candidates = extractObservationCandidates(data);
    const summary = summarizeObservation(data, candidates);
    setLatestComputerObservation({
      summary,
      candidates: candidates.slice(0, 5)
    });
    if (options?.updateForm !== false) {
      const selector = candidateSelector(candidates[0]);
      if (selector) {
        setRecordingSelector(selector);
      }
      if (summary.windowTitle) {
        let currentParams: Record<string, unknown> = {};
        try {
          currentParams = parseJsonObject(recordingParamsText);
        } catch {
          currentParams = {};
        }
        setRecordingParamsText(JSON.stringify({
          ...currentParams,
          observedWindowTitle: summary.windowTitle
        }, null, 2));
      }
    }
    setLatestResult(data);
    if (options?.toastResult) {
      toast({
        title: t("components.rpa.RPAWorkbench.computerSampled"),
        description: candidates.length ? t("components.rpa.RPAWorkbench.computerSampleCandidates", {
          count: candidates.length
        }) : t("components.rpa.RPAWorkbench.computerSampleNoCandidates")
      });
    }
    return {
      summary,
      candidates,
      raw: data
    };
  };
  const handleSampleComputerUse = async () => {
    setComputerSampling(true);
    try {
      const recording = await ensureActiveRecording();
      if (!recording?.recordingSessionId) {
        return;
      }
      const res = await fetch(`/api/rpa/recordings/${encodeURIComponent(recording.recordingSessionId)}/desktop-sample`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          target: {
            mode: recordingTargetMode,
            appId: recordingAppId.trim() || "desktop",
            label: selectedComputerAppLabel,
            ignoreAdminSurface: true,
            consoleTargetBlocked: targetLockLooksLikeAdmin,
          },
          event: {
            action: "sample_elements",
            source: "desktop_accessibility",
            intent: selectedComputerAppLabel,
            params: {
              label: selectedComputerAppLabel
            }
          },
          writeToCapturePool: true,
          maxPoolItems: 8,
          forwardAction: false
        })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.detail || data?.error || t("components.rpa.RPAWorkbench.computerSampleFailed"));
      }
      if (data?.recording) {
        setActiveRecording(data.recording as RecordingSessionPayload);
      } else {
        void refreshRecording(recording.recordingSessionId);
      }
      const candidates = extractObservationCandidates(data);
      const capturePoolItemsFromResult = Array.isArray(data?.capturePoolItems) ? data.capturePoolItems : [];
      const summary = summarizeObservation(data, candidates);
      setLatestComputerObservation({
        summary,
        candidates: candidates.slice(0, 8)
      });
      const selector = candidateSelector(candidates[0]);
      if (selector) {
        setRecordingSelector(selector);
      }
      if (summary.windowTitle) {
        let currentParams: Record<string, unknown> = {};
        try {
          currentParams = parseJsonObject(recordingParamsText);
        } catch {
          currentParams = {};
        }
        setRecordingParamsText(JSON.stringify({
          ...currentParams,
          observedWindowTitle: summary.windowTitle
        }, null, 2));
      }
      setLatestResult(data);
      const insertedCount = capturePoolItemsFromResult.length || Number(data?.capturePoolAdded || 0);
      toast({
        title: insertedCount > 0 ? t("components.rpa.RPAWorkbench.studioCapturePoolAddedTitle") : t("components.rpa.RPAWorkbench.computerSampled"),
        description: insertedCount > 0 ? t("components.rpa.RPAWorkbench.studioCapturePoolAdded", {
          count: insertedCount
        }) : t("components.rpa.RPAWorkbench.studioCapturePoolNoWrite", {
          reason: data?.reason || t("components.rpa.RPAWorkbench.computerSampleNoCandidates")
        })
      });
    } catch (error) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.computerSampleFailed"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError")
      });
    } finally {
      setComputerSampling(false);
    }
  };

  const computeLiveImagePoint = (clientX: number, clientY: number) => {
    const image = liveImageRef.current;
    if (!image) return null;
    const rect = image.getBoundingClientRect();
    const naturalWidth = image.naturalWidth || Math.round(rect.width);
    const naturalHeight = image.naturalHeight || Math.round(rect.height);
    const naturalRatio = naturalWidth / Math.max(1, naturalHeight);
    const renderedRatio = rect.width / Math.max(1, rect.height);
    let displayedWidth = rect.width;
    let displayedHeight = rect.height;
    let offsetX = 0;
    let offsetY = 0;
    if (naturalRatio > renderedRatio) {
      displayedHeight = rect.width / Math.max(0.0001, naturalRatio);
      offsetY = (rect.height - displayedHeight) / 2;
    } else {
      displayedWidth = rect.height * naturalRatio;
      offsetX = (rect.width - displayedWidth) / 2;
    }
    const localX = clientX - rect.left - offsetX;
    const localY = clientY - rect.top - offsetY;
    if (localX < 0 || localY < 0 || localX > displayedWidth || localY > displayedHeight) {
      return null;
    }
    const x = Math.round(localX * naturalWidth / Math.max(1, displayedWidth));
    const y = Math.round(localY * naturalHeight / Math.max(1, displayedHeight));
    return {
      x,
      y,
      mapping: {
        source: "desktop_live_bridge",
        desktopLiveSessionId,
        naturalWidth,
        naturalHeight,
        renderedWidth: Math.round(rect.width),
        renderedHeight: Math.round(rect.height),
        displayedWidth: Math.round(displayedWidth),
        displayedHeight: Math.round(displayedHeight),
        letterboxOffsetX: Math.round(offsetX),
        letterboxOffsetY: Math.round(offsetY),
        clientX: Math.round(clientX - rect.left),
        clientY: Math.round(clientY - rect.top),
        devicePixelRatio: typeof window !== "undefined" ? window.devicePixelRatio : undefined,
        monitorId: "primary"
      }
    };
  };

  const recordLiveOverlayAction = async (action: string, point: { x: number; y: number; mapping: Record<string, unknown> } | null, extra?: Record<string, unknown>) => {
    if (!activeRecording?.recordingSessionId || activeRecording.state !== "recording") {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.noActiveRecording"),
        description: t("components.rpa.RPAWorkbench.startRecordingFirst")
      });
      return;
    }
    if (targetLockLooksLikeAdmin) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.studioTargetBlockedTitle"),
        description: t("components.rpa.RPAWorkbench.studioTargetBlockedDescription")
      });
      return;
    }
    if (point) {
      setRecordingX(String(point.x));
      setRecordingY(String(point.y));
    }
    let params: Record<string, unknown> = {};
    try {
      params = parseJsonObject(recordingParamsText);
    } catch {
      params = {};
    }
    params = { ...params, ...(isPlainRecord(extra?.params) ? extra.params : {}) };
    let observationSummary = isPlainRecord(latestComputerObservation?.summary) ? latestComputerObservation.summary : {};
    let observationCandidates = Array.isArray(latestComputerObservation?.candidates) ? latestComputerObservation.candidates as ObservationCandidate[] : [];
    if (!observationCandidates.length) {
      try {
        const autoObservation = await captureComputerObservation({
          updateForm: false
        });
        observationSummary = autoObservation.summary;
        observationCandidates = autoObservation.candidates;
      } catch {
        observationSummary = {};
        observationCandidates = [];
      }
    }
    const selector = recordingSelector.trim();
    const payload: Record<string, unknown> = {
      source: "desktop_live_overlay",
      action,
      intent: recordingIntent.trim() || t("components.rpa.RPAWorkbench.desktopLiveRecordedAction", {
        action
      }),
      params,
      target: {
        window: {
          appId: recordingAppId.trim() || "desktop",
          title: firstString(observationSummary.windowTitle)
        },
        ...(selector ? {
          selector: {
            css: selector,
            source: "admin_recorder"
          }
        } : {})
      },
      ...(point ? {
        coordinate: {
          x: point.x,
          y: point.y
        },
        viewport: point.mapping,
        viewportMapping: point.mapping
      } : {}),
      ...(selector || observationCandidates.length ? {
        selectorCandidates: selector ? [{
          css: selector,
          source: "admin_recorder"
        }] : observationCandidates.slice(0, 5)
      } : {}),
      ...(extra || {}),
      metadata: {
        source: "admin_desktop_live_overlay",
        fragileCoordinateFallback: !selector,
        desktopLiveSessionId,
        computerObservation: observationSummary,
        recordAndForward,
        targetLock: {
          enabled: true,
          mode: recordingTargetMode,
          appId: recordingAppId.trim() || "desktop",
          label: selectedComputerAppLabel,
          ignoreAdminSurface: true
        }
      }
    };
    let sample: Record<string, unknown> | null = null;
    try {
      const sampleRes = await fetch(`/api/rpa/recordings/${encodeURIComponent(activeRecording.recordingSessionId)}/desktop-sample`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          event: payload,
          coordinate: point ? { x: point.x, y: point.y } : {},
          viewportMapping: point?.mapping || {},
          forwardAction: recordAndForward
        })
      });
      sample = await sampleRes.json().catch(() => ({}));
      if (sampleRes.ok && sample) {
        const sampleRecord = isPlainRecord(sample) ? sample : {};
        const sampleCandidates = Array.isArray(sampleRecord.selectorCandidates) ? sampleRecord.selectorCandidates : [];
        if (!selector && sampleCandidates.length) {
          payload.selectorCandidates = sampleCandidates;
        }
        payload.accessibilitySample = sampleRecord.sample;
        payload.forwardedActionResult = sampleRecord.forwardedActionResult;
        payload.metadata = {
          ...(isPlainRecord(payload.metadata) ? payload.metadata : {}),
          accessibilitySample: sampleRecord.sample,
          forwardedActionResult: sampleRecord.forwardedActionResult
        };
      }
    } catch (error) {
      payload.metadata = {
        ...(isPlainRecord(payload.metadata) ? payload.metadata : {}),
        desktopSampleError: error instanceof Error ? error.message : String(error)
      };
    }
    await submitRecordingEvent(payload);
  };

  const handleLivePreviewClick = async (event: MouseEvent<HTMLDivElement>) => {
    const point = computeLiveImagePoint(event.clientX, event.clientY);
    if (!point) return;
    await recordLiveOverlayAction("click", point);
  };

  const handleLivePreviewDoubleClick = async (event: MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const point = computeLiveImagePoint(event.clientX, event.clientY);
    if (!point) return;
    await recordLiveOverlayAction("double_click", point);
  };

  const handleLivePreviewContextMenu = async (event: MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const point = computeLiveImagePoint(event.clientX, event.clientY);
    if (!point) return;
    await recordLiveOverlayAction("right_click", point);
  };

  const handleLivePreviewWheel = async (event: WheelEvent<HTMLDivElement>) => {
    if (!activeRecording || activeRecording.state !== "recording") return;
    event.preventDefault();
    const point = computeLiveImagePoint(event.clientX, event.clientY);
    await recordLiveOverlayAction("scroll", point, {
      params: {
        amount: Math.round(event.deltaY)
      },
      mergeGroupId: "desktop-live-scroll"
    });
  };

  const handleLivePreviewPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    const point = computeLiveImagePoint(event.clientX, event.clientY);
    if (!point) return;
    liveDragStartRef.current = point;
  };

  const handleLivePreviewPointerUp = async (event: PointerEvent<HTMLDivElement>) => {
    const start = liveDragStartRef.current;
    liveDragStartRef.current = null;
    const end = computeLiveImagePoint(event.clientX, event.clientY);
    if (!start || !end) return;
    const distance = Math.hypot(end.x - start.x, end.y - start.y);
    if (distance < 8) return;
    await recordLiveOverlayAction("drag", end, {
      params: {
        startPoint: [start.x, start.y],
        endPoint: [end.x, end.y],
        steps: 12
      },
      coordinate: {
        x: end.x,
        y: end.y
      }
    });
  };

  const handleLivePreviewKeyDown = async (event: KeyboardEvent<HTMLDivElement>) => {
    if (!(event.ctrlKey || event.metaKey || event.altKey)) return;
    event.preventDefault();
    const parts: string[] = [];
    if (event.ctrlKey) parts.push("Ctrl");
    if (event.metaKey) parts.push("Meta");
    if (event.altKey) parts.push("Alt");
    if (event.shiftKey) parts.push("Shift");
    parts.push(event.key);
    await recordLiveOverlayAction("hotkey", null, {
      params: {
        sequence: parts.join("+")
      }
    });
  };
  const handleCompile = async () => {
    const runIds = parseRunIdsInput(compileRunId);
    if (runIds.length === 0) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.kbf8ee2bb"),
        description: t("components.rpa.RPAWorkbench.k67c52b94")
      });
      return;
    }
    const compileRequest = runIds.length === 1 ? () => fetch(`/api/rpa/compile/${encodeURIComponent(runIds[0])}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        save: true
      })
    }) : () => fetch("/api/rpa/compile", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        runIds,
        save: true
      })
    });
    const data = await runAction("compile", compileRequest, tg(t, "d71174f4"));
    if (data?.id) {
      setSelectedDraftId(data.id);
    }
  };
  const handleStartRecording = async () => {
    await startRecordingSession();
  };
  const handleRecordingControl = async (action: "pause" | "resume" | "cancel" | "stop") => {
    if (!activeRecording?.recordingSessionId) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.noActiveRecording"),
        description: t("components.rpa.RPAWorkbench.startRecordingFirst")
      });
      return;
    }
    const body = action === "stop" ? {
      compileDraft: true,
      save: true
    } : undefined;
    const data = await runAction(`recording:${action}:${activeRecording.recordingSessionId}`, () => fetch(`/api/rpa/recordings/${encodeURIComponent(activeRecording.recordingSessionId)}/${action}`, {
      method: "POST",
      headers: body ? {
        "Content-Type": "application/json"
      } : undefined,
      body: body ? JSON.stringify(body) : undefined
    }), t(action === "stop" ? "components.rpa.RPAWorkbench.recordingStopped" : "components.rpa.RPAWorkbench.recordingUpdated"));
    const nextRecording = (data?.recording || data) as RecordingSessionPayload;
    if (nextRecording?.recordingSessionId) {
      setActiveRecording(nextRecording);
    }
    if (action === "stop" || action === "cancel") {
      setCaptureAssistantActive(false);
    }
    if (data?.draft?.id) {
      setSelectedDraftId(data.draft.id);
    }
  };
  const handleOpenAgentBrowser = async () => {
    await runAction("recording:open-agent-browser", () => fetch("/api/computer-use/agent-browser/open", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        browserKind: recordingBrowserKind,
        url: "about:blank"
      })
    }), t("components.rpa.RPAWorkbench.agentBrowserOpened"));
  };
  const handleAppendRecordingEvent = async (overrideAction?: string) => {
    if (!activeRecording?.recordingSessionId) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.noActiveRecording"),
        description: t("components.rpa.RPAWorkbench.startRecordingFirst")
      });
      return;
    }
    let params: Record<string, unknown>;
    try {
      params = parseJsonObject(recordingParamsText);
    } catch (error) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.invalidEventParams"),
        description: error instanceof Error ? error.message : t("components.rpa.RPAWorkbench.invalidJsonObject")
      });
      return;
    }
    const selector = recordingSelector.trim();
    const x = recordingX.trim() ? Number(recordingX) : undefined;
    const y = recordingY.trim() ? Number(recordingY) : undefined;
    const action = overrideAction || recordingAction;
    const payload = {
      action,
      intent: recordingIntent.trim() || action,
      params,
      target: {
        window: {
          appId: recordingAppId.trim() || "desktop"
        },
        ...(selector ? {
          selector: {
            css: selector,
            source: "admin_recorder"
          }
        } : {})
      },
      ...(Number.isFinite(x) && Number.isFinite(y) ? {
        coordinate: {
          x,
          y
        }
      } : {}),
      ...(selector ? {
        selectorCandidates: [{
          css: selector,
          source: "admin_recorder"
        }]
      } : {}),
      sensitiveInput: recordingSensitive,
      variableName: recordingVariableName.trim() || undefined,
      metadata: {
        source: "admin_rpa_workbench",
        fragileCoordinateFallback: !selector && Number.isFinite(x) && Number.isFinite(y)
      }
    };
    await submitRecordingEvent(payload);
  };
  const parseDraftStepsFromBuilder = () => {
    const steps: EditableDraftStep[] = [];
    for (const key of draftStepOrder) {
      const value = draftStepEdits[key] || "";
      if (!value.trim()) {
        continue;
      }
      steps.push(JSON.parse(value) as EditableDraftStep);
    }
    return steps;
  };
  const updateSelectedDraftStep = (updater: (step: EditableDraftStep) => EditableDraftStep) => {
    if (!selectedDraftStepKey) return;
    setStudioDirty(true);
    setDraftStepEdits(current => {
      const currentStep = safeJsonParse<EditableDraftStep>(current[selectedDraftStepKey] || "{}", {});
      const nextStep = updater(currentStep);
      return {
        ...current,
        [selectedDraftStepKey]: JSON.stringify(nextStep, null, 2)
      };
    });
    setStepValidation(null);
  };
  const updateSelectedStepParam = (key: string, value: unknown) => {
    updateSelectedDraftStep(step => ({
      ...step,
      params: {
        ...(isPlainRecord(step.params) ? step.params : {}),
        [key]: value
      }
    }));
  };
  const updateSelectedStepSelector = (selectorKind: string, value: string) => {
    updateSelectedDraftStep(step => {
      const target = isPlainRecord(step.target) ? step.target : {};
      const selector = isPlainRecord(target.selector) ? target.selector : {};
      const nextSelector = {
        ...selector,
        [selectorKind]: value
      };
      return {
        ...step,
        target: {
          ...target,
          selector: nextSelector
        }
      };
    });
  };
  const updateSelectedStepCoordinate = (axis: "x" | "y", value: string) => {
    updateSelectedDraftStep(step => {
      const coordinate = isPlainRecord((step as Record<string, unknown>).coordinate) ? (step as Record<string, unknown>).coordinate as Record<string, unknown> : {};
      return {
        ...step,
        coordinate: {
          ...coordinate,
          [axis]: value === "" ? "" : Number(value)
        }
      } as EditableDraftStep;
    });
  };
  const updateSelectedStepVerification = (key: string, value: unknown) => {
    updateSelectedDraftStep(step => ({
      ...step,
      verification: {
        ...(isPlainRecord(step.verification) ? step.verification : {}),
        [key]: value
      }
    }));
  };
  const handleAddDraftStep = (action: string, insertBeforeKey?: string) => {
    const workspaceId = selectedDraft?.id || "unsaved";
    const step = makeDraftStep(action, selectedDraft?.appId || recordingAppId || "desktop");
    const key = step.stepId || `${workspaceId}:step:new:${Date.now()}`;
    setStudioDirty(true);
    setDraftStepEdits(current => ({
      ...current,
      [key]: JSON.stringify(step, null, 2)
    }));
    setDraftStepOrder(current => {
      const next = [...current];
      const insertIndex = insertBeforeKey ? next.indexOf(insertBeforeKey) : -1;
      if (insertIndex >= 0) {
        next.splice(insertIndex, 0, key);
        return next;
      }
      return [...next, key];
    });
    setSelectedDraftStepKey(key);
    setStepValidation(null);
  };
  const handleReorderDraftStep = (sourceKey: string, targetKey: string) => {
    if (!sourceKey || !targetKey || sourceKey === targetKey) return;
    setStudioDirty(true);
    setDraftStepOrder(current => {
      if (!current.includes(sourceKey) || !current.includes(targetKey)) return current;
      const next = current.filter(key => key !== sourceKey);
      const targetIndex = next.indexOf(targetKey);
      next.splice(targetIndex >= 0 ? targetIndex : next.length, 0, sourceKey);
      return next;
    });
    setSelectedDraftStepKey(sourceKey);
    setStepValidation(null);
  };
  const handleDraftCanvasDrop = (event: DragEvent<HTMLElement>, targetKey?: string) => {
    event.preventDefault();
    const action = event.dataTransfer.getData("application/x-v8-rpa-action");
    if (action) {
      handleAddDraftStep(action, targetKey);
      return;
    }
    const sourceKey = event.dataTransfer.getData("application/x-v8-rpa-step");
    if (sourceKey && targetKey) {
      handleReorderDraftStep(sourceKey, targetKey);
    }
  };
  const draftStepBadges = (step: EditableDraftStep) => {
    const serialized = JSON.stringify(step);
    const action = stepActionName(step);
    const metadata = isPlainRecord(step.metadata) ? step.metadata : {};
    const coordinate = isPlainRecord((step as Record<string, unknown>).coordinate) ? (step as Record<string, unknown>).coordinate as Record<string, unknown> : {};
    const verification = isPlainRecord(step.verification) ? step.verification : {};
    const badges: Array<{ key: string; label: string; tone: string }> = [];
    if (action === "wait") badges.push({ key: "wait", label: t("components.rpa.RPAWorkbench.waitNode"), tone: "bg-sky-500/10 text-sky-700 dark:text-sky-300" });
    if (Object.keys(verification).length || action.includes("assert")) badges.push({ key: "assert", label: t("components.rpa.RPAWorkbench.assertNode"), tone: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" });
    if (metadata.fragileCoordinateFallback || metadata.coordinateFallback || metadata.fragile_coordinate_fallback || Object.keys(coordinate).length) badges.push({ key: "fragile", label: t("components.rpa.RPAWorkbench.fragileCoordinate"), tone: "bg-amber-500/10 text-amber-700 dark:text-amber-300" });
    if (serialized.includes("{{")) badges.push({ key: "variable", label: t("components.rpa.RPAWorkbench.hasVariable"), tone: "bg-violet-500/10 text-violet-700 dark:text-violet-300" });
    if (serialized.includes("${secret:") || metadata.sensitiveInput || metadata.sensitive_input) badges.push({ key: "secret", label: t("components.rpa.RPAWorkbench.hasSecret"), tone: "bg-rose-500/10 text-rose-700 dark:text-rose-300" });
    return badges;
  };
  const handleMoveDraftStep = (stepKey: string, direction: -1 | 1) => {
    setStudioDirty(true);
    setDraftStepOrder(current => {
      const index = current.indexOf(stepKey);
      const nextIndex = index + direction;
      if (index < 0 || nextIndex < 0 || nextIndex >= current.length) return current;
      const next = [...current];
      [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
      return next;
    });
  };
  const handleDuplicateDraftStep = (stepKey: string) => {
    const step = safeJsonParse<EditableDraftStep>(draftStepEdits[stepKey] || "{}", {});
    const duplicated = {
      ...step,
      stepId: `step_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
      metadata: {
        ...(isPlainRecord(step.metadata) ? step.metadata : {}),
        duplicatedFrom: step.stepId || stepKey,
      },
    };
    const nextKey = duplicated.stepId || `${selectedDraft?.id || "unsaved"}:step:copy:${Date.now()}`;
    setStudioDirty(true);
    setDraftStepEdits(current => ({
      ...current,
      [nextKey]: JSON.stringify(duplicated, null, 2)
    }));
    setDraftStepOrder(current => {
      const index = current.indexOf(stepKey);
      const next = [...current];
      next.splice(index >= 0 ? index + 1 : next.length, 0, nextKey);
      return next;
    });
    setSelectedDraftStepKey(nextKey);
    setStepValidation(null);
  };
  const handleValidateSelectedStep = async (mode: "dry_run" | "selector" | "assertion") => {
    if (!selectedDraft || !selectedBuilderStep) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.studioSaveDraftFirst"),
        description: t("components.rpa.RPAWorkbench.studioSaveDraftFirst")
      });
      return;
    }
    let variables: Record<string, unknown> = {};
    try {
      variables = parseJsonObject(variablesText);
    } catch {
      variables = {};
    }
    const actionKey = `draft:validate:${selectedDraft.id}:${mode}`;
    setBusyAction(actionKey);
    try {
      const res = await fetch(`/api/rpa/drafts/${encodeURIComponent(selectedDraft.id)}/validate-step`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          step: selectedBuilderStep,
          index: draftStepOrder.indexOf(selectedDraftStepKey),
          mode,
          variables
        })
      });
      const data = await res.json().catch(() => ({}));
      setLatestResult(data);
      if (!res.ok) {
        throw new Error(data?.detail || data?.error || tg(t, "8fdc4112"));
      }
      setStepValidation(data as StepValidationResult);
      toast({
        title: t("components.rpa.RPAWorkbench.stepValidationFinished"),
        description: typeof data?.summary === "string" ? data.summary : tg(t, "3d8c4a5f")
      });
    } catch (error) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.k2e9cdd7b"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError")
      });
    } finally {
      setBusyAction(null);
    }
  };
  const handleAddVariableRow = () => {
    setStudioDirty(true);
    setDraftVariableRows(current => [...current, {
      id: `var_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 5)}`,
      name: "",
      type: "text",
      required: false,
      defaultValue: "",
      secretName: "",
      sensitive: false,
    }]);
  };
  const updateVariableRow = (id: string, patch: Partial<DraftVariableRow>) => {
    setStudioDirty(true);
    setDraftVariableRows(current => current.map(row => row.id === id ? {
      ...row,
      ...patch
    } : row));
  };
  const handleDeleteVariableRow = (id: string) => {
    setStudioDirty(true);
    setDraftVariableRows(current => current.filter(row => row.id !== id));
  };
  const handleInsertVariableIntoSelectedStep = (name: string) => {
    if (!name) return;
    updateSelectedStepParam("text", `{{${name}}}`);
  };
  const handlePatchDraftSteps = async () => {
    if (!selectedDraft) {
      await handleCreateDraftFromStudio();
      return;
    }
    let payload: ReturnType<typeof buildDraftStudioPayload>;
    try {
      payload = buildDraftStudioPayload();
    } catch (error) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.invalidDraftStep"),
        description: error instanceof Error ? error.message : t("components.rpa.RPAWorkbench.invalidJsonObject")
      });
      return;
    }
    const data = await runAction(`draft:patch:${selectedDraft.id}`, () => fetch(`/api/rpa/drafts/${encodeURIComponent(selectedDraft.id)}/patch`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        steps: payload.steps,
        variables: payload.variables,
        objectLibrary: payload.objectLibrary,
        metadataPatch: {
          ...payload.metadata,
          editedBy: "admin_ui",
          editedFrom: "rpa_canvas_studio"
        }
      })
    }), t("components.rpa.RPAWorkbench.draftSaved"));
    if (data?.id) {
      const draft = data as DraftPayload;
      setDrafts(current => current.map(item => item.id === draft.id ? draft : item));
      setSelectedDraftId(data.id);
      setStudioDirty(false);
    }
  };
  const handleDeleteDraftStep = (stepKey: string) => {
    setStudioDirty(true);
    setDraftStepEdits(current => {
      const next = {
        ...current
      };
      delete next[stepKey];
      return next;
    });
    setDraftStepOrder(current => current.filter(item => item !== stepKey));
    if (selectedDraftStepKey === stepKey) {
      const nextKey = draftStepOrder.find(item => item !== stepKey) || "";
      setSelectedDraftStepKey(nextKey);
    }
    setStepValidation(null);
  };
  const handleViewSourceTrace = async () => {
    if (!selectedDraftId) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.ke05762b8"),
        description: t("components.rpa.RPAWorkbench.k95f5425c")
      });
      return;
    }
    await runAction(`draft:source:${selectedDraftId}`, () => fetch(`/api/rpa/drafts/${encodeURIComponent(selectedDraftId)}/source-traces?include_steps=true&max_steps=8`, {
      cache: "no-store"
    }), tg(t, "846dc508"));
  };
  const handleDraftAction = async (mode: "export" | "prepare" | "run") => {
    if (!selectedDraftId) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.ke05762b8"),
        description: t("components.rpa.RPAWorkbench.k95f5425c")
      });
      return;
    }
    let variables: Record<string, unknown>;
    try {
      variables = parseJsonObject(variablesText);
    } catch (error) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.k35f41066"),
        description: error instanceof Error ? error.message : tg(t, "3d763c86")
      });
      return;
    }
    const payload = {
      variables,
      timeoutMs: Number(timeoutMs || 600000),
      ...commonPayload()
    };
    await runAction(`draft:${mode}`, () => fetch(`/api/rpa/drafts/${encodeURIComponent(selectedDraftId)}/${mode}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(mode === "export" ? commonPayload() : payload)
    }), mode === "run" ? tg(t, "be524bf1") : tg(t, "e9e44bf1"));
  };
  const handleDraftGovernanceAction = async (action: "archive" | "restore" | "delete") => {
    if (!selectedDraftId) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.ke05762b8"),
        description: t("components.rpa.RPAWorkbench.k95f5425c")
      });
      return;
    }
    if (action === "delete" && !window.confirm(t("components.rpa.RPAWorkbench.deleteDraftConfirm"))) {
      return;
    }
    const endpoint = action === "delete" ? `/api/rpa/drafts/${encodeURIComponent(selectedDraftId)}?confirm=true` : `/api/rpa/drafts/${encodeURIComponent(selectedDraftId)}/${action}`;
    await runAction(`draft:${action}:${selectedDraftId}`, () => fetch(endpoint, {
      method: action === "delete" ? "DELETE" : "POST",
      headers: action === "delete" ? undefined : {
        "Content-Type": "application/json"
      },
      body: action === "delete" ? undefined : JSON.stringify({
        actor: "admin_ui",
        reason: action === "archive" ? "admin_archive" : undefined
      })
    }), action === "archive" ? t("components.rpa.RPAWorkbench.archiveDone") : action === "restore" ? t("components.rpa.RPAWorkbench.restoreDone") : t("components.rpa.RPAWorkbench.deleteDone"));
    if (action === "delete" || action === "archive" && !showArchivedDrafts) {
      setSelectedDraftId("");
    }
  };
  const handleApproveDraftAsTemplate = async () => {
    if (!selectedDraft) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.ke05762b8"),
        description: t("components.rpa.RPAWorkbench.k95f5425c")
      });
      return;
    }
    const data = await runAction(`draft:approve-template:${selectedDraft.id}`, () => fetch(`/api/rpa/drafts/${encodeURIComponent(selectedDraft.id)}/approve-template`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        reviewer: "admin_ui",
        notes: templateNote.trim() || undefined,
        metadataPatch: {
          approvedFrom: "admin_step_builder"
        }
      })
    }), t("components.rpa.RPAWorkbench.draftApprovedAsTemplate"));
    const templateId = typeof data?.templateId === "string" ? data.templateId : typeof data?.template?.id === "string" ? data.template.id : "";
    if (templateId) {
      setSelectedTemplateId(templateId);
    }
  };
  const handleExistingAction = async (mode: "prepare-existing" | "run-existing") => {
    const robotFile = existingRobotFile.trim();
    if (!robotFile) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.kfdb80aec"),
        description: t("components.rpa.RPAWorkbench.kbf440ba2")
      });
      return;
    }
    let variables: Record<string, unknown>;
    try {
      variables = parseJsonObject(existingVariablesText);
    } catch (error) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.k35f41066"),
        description: error instanceof Error ? error.message : tg(t, "3d763c86")
      });
      return;
    }
    const payload = {
      robotFile,
      variables,
      timeoutMs: Number(timeoutMs || 600000),
      ...commonPayload()
    };
    await runAction(mode, () => fetch(`/api/rpa/${mode}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    }), tg(t, "a04fb186"));
  };
  const handleApproval = async (approvalId: string, approve: boolean) => {
    const answer = approvalDrafts[approvalId]?.trim() || "";
    const endpoint = approve ? `/api/approvals/${approvalId}/approve` : `/api/approvals/${approvalId}/reject`;
    await runAction(`approval:${approve ? "approve" : "reject"}:${approvalId}`, () => fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        response: {
          answer,
          approved: approve
        }
      })
    }), tg(t, "3860d079"));
    setApprovalDrafts(current => {
      const next = {
        ...current
      };
      delete next[approvalId];
      return next;
    });
  };
  const handleRunCommand = async (runId: string, command: "interrupt" | "retry") => {
    await runAction(`run:${command}:${runId}`, () => fetch(`/api/runs/${runId}/commands/${command}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        reason: command === "interrupt" ? "admin_rpa_interrupt" : "admin_rpa_retry"
      })
    }), tg(t, "c6683c32"));
  };
  const handleTemplateAction = async (action: "approve" | "freeze" | "review_required") => {
    if (!selectedTemplateId) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.ka3b5b6f9"),
        description: t("components.rpa.RPAWorkbench.k899e7048")
      });
      return;
    }
    const endpoint = action === "approve" ? `/api/rpa/templates/${encodeURIComponent(selectedTemplateId)}/approve` : action === "freeze" ? `/api/rpa/templates/${encodeURIComponent(selectedTemplateId)}/freeze` : `/api/rpa/templates/${encodeURIComponent(selectedTemplateId)}/review`;
    const body = action === "review_required" ? {
      decision: "review_required",
      reviewer: "admin_ui",
      notes: templateNote.trim() || undefined
    } : {
      reviewer: "admin_ui",
      notes: templateNote.trim() || undefined
    };
    await runAction(`template:${action}:${selectedTemplateId}`, () => fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(body)
    }), action === "approve" ? tg(t, "b2d6ae9b") : tg(t, "5f612337"));
  };
  const handleTemplateGovernanceAction = async (action: "archive" | "restore" | "delete") => {
    if (!selectedTemplateId) {
      toast({
        variant: "destructive",
        title: t("components.rpa.RPAWorkbench.ka3b5b6f9"),
        description: t("components.rpa.RPAWorkbench.k899e7048")
      });
      return;
    }
    if (action === "delete" && !window.confirm(t("components.rpa.RPAWorkbench.deleteTemplateConfirm"))) {
      return;
    }
    const endpoint = action === "delete" ? `/api/rpa/templates/${encodeURIComponent(selectedTemplateId)}?confirm=true` : `/api/rpa/templates/${encodeURIComponent(selectedTemplateId)}/${action}`;
    await runAction(`template:${action}:${selectedTemplateId}`, () => fetch(endpoint, {
      method: action === "delete" ? "DELETE" : "POST",
      headers: action === "delete" ? undefined : {
        "Content-Type": "application/json"
      },
      body: action === "delete" ? undefined : JSON.stringify({
        actor: "admin_ui",
        reason: action === "archive" ? templateNote.trim() || "admin_archive" : undefined
      })
    }), action === "archive" ? t("components.rpa.RPAWorkbench.archiveDone") : action === "restore" ? t("components.rpa.RPAWorkbench.restoreDone") : t("components.rpa.RPAWorkbench.deleteDone"));
    if (action === "delete" || action === "archive" && !showArchivedTemplates) {
      setSelectedTemplateId("");
    }
  };
  const handleTemplateRollback = async (revision?: number, historyPath?: string) => {
    if (!selectedTemplateId) {
      return;
    }
    await runAction(`template:rollback:${selectedTemplateId}:${revision || historyPath || "latest"}`, () => fetch(`/api/rpa/templates/${encodeURIComponent(selectedTemplateId)}/rollback`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        revision,
        historyPath: historyPath || undefined,
        reviewer: "admin_ui",
        notes: templateNote.trim() || undefined
      })
    }), tg(t, "b77221d2"));
  };
  return <div className="space-y-6">
            <div className="flex items-center justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">{tg(t, "545938bb")}</h1>
                    <p className="mt-1 text-muted-foreground">{tg(t, "a6e48ba6")}</p>
                </div>
                <Button variant="outline" onClick={() => void loadAll()} disabled={loading || !!busyAction}>
                    <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                    {t("app.admin.dashboard.creativeMedia.refresh")}
                </Button>
            </div>

            <Card className="overflow-hidden border-border/60 bg-background">
                <CardHeader className="border-b border-border/50 bg-muted/20">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                            <CardTitle className="text-lg">RPA Studio</CardTitle>
                            <CardDescription>{t("components.rpa.RPAWorkbench.studioDescription")}</CardDescription>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                            <Button variant="outline" size="sm" onClick={resetStudioWorkspace}>
                                <Plus className="mr-1.5 h-3.5 w-3.5" />
                                {t("components.rpa.RPAWorkbench.studioNewFlow")}
                            </Button>
                            <select className="h-9 min-w-[12rem] rounded-md border border-input bg-background px-2.5 text-xs" value={selectedDraftId} onChange={event => handleSelectDraft(event.target.value)}>
                                <option value="">{t("components.rpa.RPAWorkbench.studioCurrentUnsaved")}</option>
                                {drafts.filter(draft => showArchivedDrafts || !isArchivedDraft(draft)).map(draft => <option key={draft.id} value={draft.id}>{draft.name || draft.id}</option>)}
                            </select>
                            <select className="h-9 min-w-[12rem] rounded-md border border-input bg-background px-2.5 text-xs" value={selectedTemplateId} onChange={event => setSelectedTemplateId(event.target.value)}>
                                <option value="">{t("components.rpa.RPAWorkbench.studioSelectTemplate")}</option>
                                {templates.filter(template => showArchivedTemplates || !isArchivedTemplate(template)).map(template => <option key={template.id} value={template.id}>{template.name || template.id}</option>)}
                            </select>
                            <Button variant="outline" onClick={() => setShowLegacyPanel(current => !current)}>
                                {showLegacyPanel ? t("components.rpa.RPAWorkbench.studioHideLegacy") : t("components.rpa.RPAWorkbench.studioLegacyDiagnostics")}
                            </Button>
                            <Button onClick={() => void handlePatchDraftSteps()} disabled={busyAction === `draft:patch:${selectedDraft?.id}` || busyAction === "draft:create"}>
                                <Save className="mr-2 h-4 w-4" />
                                {selectedDraft ? t("components.rpa.RPAWorkbench.studioSaveChanges") : t("components.rpa.RPAWorkbench.studioSaveNewDraft")}
                            </Button>
                            {selectedDraft ? <Button variant="outline" onClick={() => void handleCreateDraftFromStudio({ saveAs: true })} disabled={!!busyAction}>
                                    {t("components.rpa.RPAWorkbench.studioSaveAsDraft")}
                                </Button> : null}
                            {studioDirty ? <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-900/70 dark:bg-amber-950/40 dark:text-amber-200">{t("components.rpa.RPAWorkbench.studioDirtyBadge")}</Badge> : null}
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="p-0">
                    <div className="grid min-h-[660px] xl:grid-cols-[minmax(200px,0.17fr)_minmax(0,1fr)_minmax(300px,0.24fr)] 2xl:grid-cols-[220px_minmax(0,1fr)_320px]">
                        <aside className="border-r border-border/50 bg-muted/10 p-3">
                            <div className="mb-3">
                                <div className="text-xs font-semibold">{t("components.rpa.RPAWorkbench.studioActionLibraryTitle")}</div>
                                <div className="text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.studioActionLibraryDescription")}</div>
                            </div>
                            <ScrollArea className="h-[560px] pr-2">
                                <div className="space-y-3">
                                    {studioActionGroups.map(group => <div key={group.key} className="space-y-1.5">
                                            <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">{group.title}</div>
                                            <div className="grid gap-1.5">
                                                {group.actions.map(([action, label]) => <button key={`${group.key}:${action}`} type="button" draggable onDragStart={event => {
                                                        event.dataTransfer.setData("application/x-v8-rpa-action", action);
                                                        event.dataTransfer.effectAllowed = "copy";
                                                    }} onClick={() => handleAddDraftStep(action)} className="flex min-h-9 cursor-grab items-center justify-between rounded-lg border border-border/60 bg-background px-2.5 py-1.5 text-left text-xs shadow-none transition hover:border-primary/50 hover:bg-primary/5">
                                                        <span className="truncate">{label}</span>
                                                        <Plus className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                                    </button>)}
                                            </div>
                                        </div>)}
                                </div>
                            </ScrollArea>
                        </aside>

                        <main className="flex min-w-0 flex-col bg-muted/5">
                            <div className="border-b border-border/50 bg-background/80 p-3">
                                <div className="grid gap-3 2xl:grid-cols-[minmax(0,1.35fr)_minmax(12rem,0.45fr)_auto]">
                                    <div className="grid min-w-0 gap-2">
                                        <Label className="text-xs">{t("components.rpa.RPAWorkbench.studioRecordingTarget")}</Label>
                                        <div className="grid gap-2 md:grid-cols-[10rem_minmax(18rem,1fr)]">
                                            <select className="h-9 rounded-md border border-input bg-background px-2.5 text-xs" value={recordingTargetMode} onChange={event => {
                                                setRecordingTargetMode(event.target.value);
                                                setStudioDirty(true);
                                            }}>
                                                <option value="agent_browser">{t("components.rpa.RPAWorkbench.targetAgentBrowser")}</option>
                                                <option value="desktop_window">{t("components.rpa.RPAWorkbench.targetDesktopWindow")}</option>
                                                <option value="launch_app">{t("components.rpa.RPAWorkbench.targetLaunchApp")}</option>
                                            </select>
                                            <div className="relative min-w-0">
                                                <button type="button" onClick={() => {
                                                    setAppPickerOpen(current => !current);
                                                    void loadComputerApps(false, appSearch);
                                                }} className="flex h-9 w-full items-center justify-between rounded-md border border-input bg-background px-2.5 text-left text-xs transition hover:border-primary/40">
                                                    <AdminHoverInfo content={selectedComputerAppLabel} panelClassName="w-auto max-w-[28rem] whitespace-normal">
                                                        <span className="min-w-0 max-w-full truncate">{selectedComputerAppLabel}</span>
                                                    </AdminHoverInfo>
                                                    <Search className="ml-2 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                                </button>
                                                {appPickerOpen ? <div className="absolute left-0 top-10 z-30 w-[min(32rem,calc(100vw-3rem))] min-w-full max-w-[calc(100vw-3rem)] rounded-xl border border-border/70 bg-popover p-2 shadow-xl">
                                                        <Input className="h-8 text-xs" value={appSearch} onChange={event => setAppSearch(event.target.value)} placeholder={t("components.rpa.RPAWorkbench.studioSearchApps")} autoFocus />
                                                        <ScrollArea className="mt-2 max-h-72 pr-2">
                                                            <div className="space-y-2">
                                                                {computerAppsError ? <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">{computerAppsError}</div> : null}
                                                                {groupedAppPickerOptions.map(group => <div key={group.group}>
                                                                        <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                                                                            {t(`components.rpa.RPAWorkbench.studioAppGroup.${group.group}`)}
                                                                        </div>
                                                                        <div className="grid gap-1">
                                                                            {group.items.map(item => <button key={item.id} type="button" onClick={() => {
                                                                                    setRecordingAppId(item.id);
                                                                                    setAppSearch("");
                                                                                    setAppPickerOpen(false);
                                                                                    setStudioDirty(true);
                                                                                }} className={`rounded-lg px-2 py-1.5 text-left text-xs transition hover:bg-muted ${recordingAppId === item.id ? "bg-primary/10 text-primary" : ""}`}>
                                                                                    <AdminHoverInfo content={item.label} panelClassName="w-auto max-w-[28rem] whitespace-normal">
                                                                                        <div className="truncate font-medium">{item.label}</div>
                                                                                    </AdminHoverInfo>
                                                                                    <div className="truncate text-[11px] text-muted-foreground">{item.subtitle || item.id}</div>
                                                                                </button>)}
                                                                        </div>
                                                                    </div>)}
                                                                {!appPickerOptions.length && !computerAppsLoading ? <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">{computerApps.length ? t("components.rpa.RPAWorkbench.studioNoAppsFound") : t("components.rpa.RPAWorkbench.studioAppListEmptyHint")}</div> : null}
                                                            </div>
                                                        </ScrollArea>
                                                    </div> : null}
                                            </div>
                                        </div>
                                    </div>
                                    <div className="grid gap-2">
                                        <Label className="text-xs">{t("components.rpa.RPAWorkbench.studioFlowDescription")}</Label>
                                        <Input className="h-9 text-xs" value={recordingName} onChange={event => {
                                            setRecordingName(event.target.value);
                                            setStudioDirty(true);
                                        }} placeholder={t("components.rpa.RPAWorkbench.flowNamePlaceholder")} />
                                    </div>
                                    <div className="flex items-end gap-2">
                                        <Button size="sm" variant="outline" onClick={() => {
                                            setAppPickerOpen(true);
                                            void loadComputerApps(true, appSearch);
                                        }} disabled={computerAppsLoading}>
                                            <RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${computerAppsLoading ? "animate-spin" : ""}`} />
                                            {t("components.rpa.RPAWorkbench.studioAppList")}
                                        </Button>
                                        <Button size="sm" onClick={() => void handleStartRecording()} disabled={!!busyAction || activeRecording?.state === "recording" || targetLockLooksLikeAdmin}>
                                            <Video className="mr-1.5 h-3.5 w-3.5" />
                                            {t("components.rpa.RPAWorkbench.studioStartRecording")}
                                        </Button>
                                    </div>
                                </div>
                                <div className={`mt-3 rounded-xl border px-3 py-2 text-xs ${targetLockLooksLikeAdmin ? "border-destructive/30 bg-destructive/10 text-destructive" : "border-border/60 bg-muted/20 text-muted-foreground"}`}>
                                    {t("components.rpa.RPAWorkbench.studioTargetLockPrefix")}{selectedComputerAppLabel} · {recordingTargetMode}
                                    {targetLockLooksLikeAdmin ? t("components.rpa.RPAWorkbench.studioAdminExcluded") : t("components.rpa.RPAWorkbench.studioTargetLockHint")}
                                    {captureAssistantActive ? <Badge variant="outline" className="ml-2 align-middle text-[10px]">{t("components.rpa.RPAWorkbench.studioCaptureAssistantActive")}</Badge> : null}
                                    {captureAssistantStage !== "idle" && !captureAssistantActive ? <Badge variant="outline" className="ml-2 align-middle text-[10px]">{captureAssistantStageLabel}</Badge> : null}
                                </div>
                            </div>

                            <div className="flex-1 overflow-hidden p-4">
                                <div className="relative h-[500px] overflow-auto rounded-2xl border border-dashed border-border/70 bg-[radial-gradient(circle_at_1px_1px,hsl(var(--muted-foreground)/0.12)_1px,transparent_0)] bg-[length:18px_18px] p-5" onDragOver={event => event.preventDefault()} onDrop={event => handleDraftCanvasDrop(event)}>
                                    {orderedDraftSteps.length > 1 ? <svg className="pointer-events-none absolute left-1/2 top-24 h-[calc(100%-12rem)] w-1 -translate-x-1/2 text-primary/30" aria-hidden="true">
                                            <line x1="2" y1="0" x2="2" y2="100%" stroke="currentColor" strokeWidth="2" strokeDasharray="8 10" />
                                        </svg> : null}
                                    <div className="relative z-10 mx-auto flex max-w-xl flex-col gap-3">
                                        <div className="mx-auto rounded-full border border-border/60 bg-background px-3 py-1.5 text-[11px] font-semibold text-muted-foreground shadow-sm">Start</div>
                                        {orderedDraftSteps.map((item, index) => {
                                            const badges = draftStepBadges(item.step);
                                            return <div key={item.key} draggable onDragStart={event => {
                                                event.dataTransfer.setData("application/x-v8-rpa-step", item.key);
                                                event.dataTransfer.effectAllowed = "move";
                                            }} onDragOver={event => event.preventDefault()} onDrop={event => handleDraftCanvasDrop(event, item.key)}>
                                                <button type="button" onClick={() => {
                                                    setSelectedDraftStepKey(item.key);
                                                    setStepValidation(null);
                                                    setStudioRightPanel("properties");
                                                }} className={`w-full rounded-xl border bg-background/95 p-3 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md ${selectedDraftStepKey === item.key ? "border-primary ring-2 ring-primary/15" : "border-border/70"}`}>
                                                    <div className="flex items-start justify-between gap-3">
                                                        <div className="flex min-w-0 gap-3">
                                                            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{index + 1}</div>
                                                            <div className="min-w-0">
                                                                <div className="truncate text-xs font-semibold">{stepIntentLabel(item.step)}</div>
                                                                <div className="mt-1 truncate text-xs text-muted-foreground">{stepActionName(item.step)}{stepSelectorValue(item.step) ? ` · ${stepSelectorValue(item.step)}` : ""}</div>
                                                            </div>
                                                        </div>
                                                        <GitBranch className="h-4 w-4 shrink-0 text-muted-foreground" />
                                                    </div>
                                                    {badges.length ? <div className="mt-2 flex flex-wrap gap-1">
                                                            {badges.map(badge => <span key={`${item.key}:${badge.key}`} className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${badge.tone}`}>{badge.label}</span>)}
                                                        </div> : null}
                                                </button>
                                            </div>;
                                        })}
                                        {orderedDraftSteps.length === 0 ? <div className="flex h-[360px] items-center justify-center rounded-2xl border border-dashed bg-background/80 text-center text-sm text-muted-foreground">
                                                <div>
                                                    <GitBranch className="mx-auto mb-3 h-8 w-8 text-primary/50" />
                                                    {t("components.rpa.RPAWorkbench.studioCanvasEmpty")}
                                                </div>
                                            </div> : null}
                                        <div className="mx-auto rounded-full border border-border/60 bg-background px-3 py-1.5 text-[11px] font-semibold text-muted-foreground shadow-sm">End</div>
                                    </div>
                                </div>
                            </div>

                            <div className="border-t border-border/50 bg-background/90 p-3">
                                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                    <Badge variant="outline">{orderedDraftSteps.length} steps</Badge>
                                    <Badge variant={activeRecording?.state === "recording" ? "default" : "secondary"}>{activeRecording?.state || "idle"}</Badge>
                                    {desktopLiveSessionId ? <Badge variant="outline">desktop live</Badge> : null}
                                    {browserCaptureActive ? <Badge variant="outline">browser capture</Badge> : null}
                                    {latestResult ? <span className="truncate">{t("components.rpa.RPAWorkbench.studioRecentResult")}{firstString((latestResult as Record<string, unknown>)?.status) || firstString((latestResult as Record<string, unknown>)?.message) || t("components.rpa.RPAWorkbench.studioResultUpdated")}</span> : <span>{t("components.rpa.RPAWorkbench.studioRunLogPlaceholder")}</span>}
                                </div>
                            </div>
                        </main>

                        <aside className="border-l border-border/50 bg-background p-3">
                            <div className="mb-3 flex flex-wrap gap-1 rounded-xl border border-border/60 bg-muted/20 p-1">
                                {studioPanelOptions.map(([key, label]) => <button key={key} type="button" onClick={() => setStudioRightPanel(key)} className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${studioRightPanel === key ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
                                        {label}
                                    </button>)}
                            </div>
                            {studioRightPanel === "properties" ? <div className="space-y-4">
                                    <div>
                                        <div className="text-xs font-semibold">{t("components.rpa.RPAWorkbench.studioStepProperties")}</div>
                                        <div className="text-xs text-muted-foreground">{selectedDraftStepKey || t("components.rpa.RPAWorkbench.studioNoStepSelected")}</div>
                                    </div>
                                    {selectedBuilderStep ? <>
                                            <div className="rounded-xl border border-border/60 bg-muted/20 p-3 text-xs text-muted-foreground">
                                                {t(`components.rpa.RPAWorkbench.studioNextStep.${selectedBuilderActionKind}`)}
                                            </div>
                                            <div className="grid gap-2">
                                                <Label>{t("components.rpa.RPAWorkbench.studioActionType")}</Label>
                                                <select className="h-9 rounded-md border border-input bg-background px-2.5 text-xs" value={stepActionName(selectedBuilderStep)} onChange={event => updateSelectedDraftStep(step => {
                                                    const nextStep = { ...step, action: event.target.value };
                                                    delete nextStep.use;
                                                    return nextStep;
                                                })}>
                                                    {studioActionGroups.flatMap(group => group.actions).map(([action, label]) => <option key={action} value={action}>{label}</option>)}
                                                </select>
                                            </div>
                                            <div className="grid gap-2">
                                                <Label>{t("components.rpa.RPAWorkbench.studioStepIntent")}</Label>
                                                <Input className="h-9 text-xs" value={selectedBuilderStep.intent || ""} onChange={event => updateSelectedDraftStep(step => ({ ...step, intent: event.target.value }))} />
                                            </div>

                                            {selectedBuilderActionKind === "app" ? <div className="space-y-3">
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.studioAppToLaunch")}</Label>
                                                        <Input className="h-9 text-xs" value={String(selectedBuilderParams.appId || selectedComputerAppLabel || "")} onChange={event => updateSelectedStepParam("appId", event.target.value)} placeholder={t("components.rpa.RPAWorkbench.studioAppToLaunchPlaceholder")} />
                                                    </div>
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.studioLaunchArgs")}</Label>
                                                        <Input className="h-9 text-xs" value={String(selectedBuilderParams.args || "")} onChange={event => updateSelectedStepParam("args", event.target.value)} placeholder="--profile default" />
                                                    </div>
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.studioTimeoutMs")}</Label>
                                                        <Input className="h-9 text-xs" value={String(selectedBuilderParams.timeoutMs || "")} onChange={event => updateSelectedStepParam("timeoutMs", Number(event.target.value || 0))} placeholder="5000" />
                                                    </div>
                                                </div> : null}

                                            {["click", "type", "wait", "assert", "browser"].includes(selectedBuilderActionKind) ? <div className="space-y-3">
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.studioElementTarget")}</Label>
                                                        <select className="h-9 rounded-md border border-input bg-background px-2.5 text-xs" value="" onChange={event => {
                                                            const item = selectableElements.find(candidate => candidate.optionId === event.target.value);
                                                            if (item) applyElementToSelectedStep(item);
                                                        }}>
                                                            <option value="">{selectableElements.length ? t("components.rpa.RPAWorkbench.studioChooseElement") : t("components.rpa.RPAWorkbench.studioNoSelectableElements")}</option>
                                                            {selectableElements.map(item => <option key={`${item.sourceBucket}:${item.optionId}`} value={item.optionId}>{item.sourceBucket === "library" ? "★ " : ""}{captureItemLabel(item)}</option>)}
                                                        </select>
                                                        <div className="truncate rounded-md border border-border/50 bg-muted/20 px-2 py-1.5 text-[11px] text-muted-foreground">
                                                            {stepSelectorValue(selectedBuilderStep) || captureItemCoordinateValue({ coordinate: selectedBuilderCoordinate }) || t("components.rpa.RPAWorkbench.studioNoElementBound")}
                                                        </div>
                                                    </div>
                                                    {selectedBuilderActionKind === "type" ? <div className="grid gap-2">
                                                            <Label>{t("components.rpa.RPAWorkbench.inputTextOrVariable")}</Label>
                                                            <Textarea className="min-h-[76px] text-xs" value={String(selectedBuilderParams.text || selectedBuilderParams.value || "")} onChange={event => updateSelectedStepParam("text", event.target.value)} placeholder="${customerName} / text" />
                                                        </div> : null}
                                                    {selectedBuilderActionKind === "wait" ? <div className="grid gap-2">
                                                            <Label>{t("components.rpa.RPAWorkbench.studioTimeoutMs")}</Label>
                                                            <Input className="h-9 text-xs" value={String(selectedBuilderParams.timeoutMs || "")} onChange={event => updateSelectedStepParam("timeoutMs", Number(event.target.value || 0))} placeholder="3000" />
                                                        </div> : null}
                                                    {selectedBuilderActionKind === "assert" ? <div className="grid gap-2">
                                                            <Label>{t("components.rpa.RPAWorkbench.assertionExpected")}</Label>
                                                            <Textarea className="min-h-[72px] text-xs" value={String(selectedBuilderVerification.expectedText || selectedBuilderVerification.expected || "")} onChange={event => updateSelectedStepVerification("expectedText", event.target.value)} placeholder={t("components.rpa.RPAWorkbench.studioExpectedTextPlaceholder")} />
                                                        </div> : null}
                                                    <details className="rounded-xl border border-border/60 p-3 text-xs">
                                                        <summary className="cursor-pointer font-medium">{t("components.rpa.RPAWorkbench.studioAdvancedTarget")}</summary>
                                                        <div className="mt-3 space-y-3">
                                                            <div className="grid gap-2">
                                                                <Label>{t("components.rpa.RPAWorkbench.selectorOrAnchor")}</Label>
                                                                <Input className="h-9 text-xs" value={String(selectedBuilderSelector.css || selectedBuilderSelector.xpath || selectedBuilderSelector.role || selectedBuilderSelector.automationId || "")} onChange={event => updateSelectedStepSelector("css", event.target.value)} placeholder="CSS / XPath / Role / automationId" />
                                                            </div>
                                                            <div className="grid grid-cols-2 gap-2">
                                                                <div className="grid gap-2">
                                                                    <Label>X</Label>
                                                                    <Input className="h-9 text-xs" value={selectedBuilderCoordinate.x == null ? "" : String(selectedBuilderCoordinate.x)} onChange={event => updateSelectedStepCoordinate("x", event.target.value)} />
                                                                </div>
                                                                <div className="grid gap-2">
                                                                    <Label>Y</Label>
                                                                    <Input className="h-9 text-xs" value={selectedBuilderCoordinate.y == null ? "" : String(selectedBuilderCoordinate.y)} onChange={event => updateSelectedStepCoordinate("y", event.target.value)} />
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </details>
                                                </div> : null}

                                            {selectedBuilderActionKind === "loop" ? <div className="space-y-3">
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.studioLoopStart")}</Label>
                                                        <select className="h-9 rounded-md border border-input bg-background px-2.5 text-xs" value={selectedLoopStartKey} onChange={event => updateSelectedStepParam("loopStartStepKey", event.target.value)}>
                                                            <option value="">{t("components.rpa.RPAWorkbench.studioSelectLoopNode")}</option>
                                                            {orderedDraftSteps.filter(item => item.key !== selectedDraftStepKey).map((item, index) => <option key={item.key} value={item.key}>{index + 1}. {stepIntentLabel(item.step)}</option>)}
                                                        </select>
                                                    </div>
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.studioLoopEnd")}</Label>
                                                        <select className="h-9 rounded-md border border-input bg-background px-2.5 text-xs" value={selectedLoopEndKey} onChange={event => updateSelectedStepParam("loopEndStepKey", event.target.value)}>
                                                            <option value="">{t("components.rpa.RPAWorkbench.studioSelectLoopNode")}</option>
                                                            {orderedDraftSteps.filter(item => item.key !== selectedDraftStepKey).map((item, index) => <option key={item.key} value={item.key}>{index + 1}. {stepIntentLabel(item.step)}</option>)}
                                                        </select>
                                                    </div>
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.studioLoopCount")}</Label>
                                                        <Input className="h-9 text-xs" value={String(selectedBuilderParams.count || "")} onChange={event => updateSelectedStepParam("count", Number(event.target.value || 0))} placeholder="3" />
                                                    </div>
                                                    {selectedLoopInvalid ? <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">{t("components.rpa.RPAWorkbench.studioLoopInvalid")}</div> : null}
                                                </div> : null}

                                            {["hotkey", "scroll", "data", "generic"].includes(selectedBuilderActionKind) ? <div className="grid gap-2">
                                                    <Label>{t("components.rpa.RPAWorkbench.studioInputParams")}</Label>
                                                    <Textarea className="min-h-[84px] text-xs" value={String(selectedBuilderParams.text || selectedBuilderParams.value || selectedBuilderParams.sequence || selectedBuilderParams.direction || "")} onChange={event => updateSelectedStepParam(selectedBuilderActionKind === "hotkey" ? "sequence" : "text", event.target.value)} placeholder={t("components.rpa.RPAWorkbench.studioInputParamsPlaceholder")} />
                                                </div> : null}

                                            <div className="flex flex-wrap gap-2">
                                                <Button size="sm" variant="outline" onClick={() => void handleValidateSelectedStep("selector")}>{t("components.rpa.RPAWorkbench.studioValidateSelector")}</Button>
                                                <Button size="sm" variant="outline" onClick={() => void handleValidateSelectedStep("assertion")}>{t("components.rpa.RPAWorkbench.studioValidateAssertion")}</Button>
                                                <Button size="sm" variant="ghost" onClick={() => handleDuplicateDraftStep(selectedDraftStepKey)}><Copy className="mr-1 h-4 w-4" />{t("components.rpa.RPAWorkbench.studioDuplicate")}</Button>
                                                <Button size="sm" variant="ghost" onClick={() => handleDeleteDraftStep(selectedDraftStepKey)}><Trash2 className="mr-1 h-4 w-4" />{t("components.rpa.RPAWorkbench.studioDelete")}</Button>
                                            </div>
                                        </> : <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{t("components.rpa.RPAWorkbench.studioSelectNodeHint")}</div>}
                                </div> : null}
                            {studioRightPanel === "variables" ? <div className="space-y-3">
                                    <div className="flex items-center justify-between">
                                        <div>
                                            <div className="text-sm font-semibold">{t("components.rpa.RPAWorkbench.studioVariablesTitle")}</div>
                                            <div className="text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.studioVariablesDescription")}</div>
                                        </div>
                                        <Button size="sm" variant="outline" onClick={handleAddVariableRow}><Plus className="mr-1 h-4 w-4" />{t("components.rpa.RPAWorkbench.studioVariable")}</Button>
                                    </div>
                                    <ScrollArea className="h-[520px] pr-3">
                                        <div className="space-y-3">
                                            {draftVariableRows.map(row => <div key={row.id} className="rounded-xl border border-border/60 p-3">
                                                    <Input className="mb-2" value={row.name} onChange={event => updateVariableRow(row.id, { name: event.target.value })} placeholder="variableName" />
                                                    <div className="grid grid-cols-2 gap-2">
                                                        <select className="h-9 rounded-md border border-input bg-background px-2 text-xs" value={row.type} onChange={event => updateVariableRow(row.id, { type: event.target.value })}>
                                                            <option value="text">string</option>
                                                            <option value="number">number</option>
                                                            <option value="boolean">boolean</option>
                                                            <option value="list">list</option>
                                                            <option value="dict">dict</option>
                                                        </select>
                                                        <Input value={row.secretName} onChange={event => updateVariableRow(row.id, { secretName: event.target.value, sensitive: Boolean(event.target.value) })} placeholder="secret name" />
                                                    </div>
                                                    <Button className="mt-2" size="sm" variant="ghost" onClick={() => handleInsertVariableIntoSelectedStep(row.name)} disabled={!row.name || !selectedBuilderStep}>{t("components.rpa.RPAWorkbench.studioInsertIntoStep")}</Button>
                                                </div>)}
                                            {!draftVariableRows.length ? <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{t("components.rpa.RPAWorkbench.studioNoVariables")}</div> : null}
                                        </div>
                                    </ScrollArea>
                                </div> : null}
                            {studioRightPanel === "elements" ? <div className="space-y-3">
                                    <div>
                                        <div className="text-xs font-semibold">{t("components.rpa.RPAWorkbench.studioElementsTitle")}</div>
                                        <div className="text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.studioElementsDescription")}</div>
                                    </div>
                                    <div className="flex flex-wrap gap-2">
                                        <Button size="sm" variant="outline" onClick={() => void handleCaptureAssistantStart()} disabled={targetLockLooksLikeAdmin || !!busyAction || captureAssistantBusy}>
                                            <Crosshair className="mr-1.5 h-3.5 w-3.5" />
                                            {captureAssistantBusy ? captureAssistantStageLabel : t("components.rpa.RPAWorkbench.studioStartCaptureAssistant")}
                                        </Button>
                                        <Button size="sm" variant="ghost" onClick={() => void handleCaptureAssistantStop()} disabled={!captureAssistantActive}>
                                            {t("components.rpa.RPAWorkbench.studioStopCaptureAssistant")}
                                        </Button>
                                        <Button size="sm" variant="outline" onClick={() => void handlePrepareDesktopLive()} disabled={desktopLiveLoading}>{desktopLiveSessionId ? t("components.rpa.RPAWorkbench.studioRestartPreview") : t("components.rpa.RPAWorkbench.studioStartPreview")}</Button>
                                        <Button size="sm" variant="outline" onClick={() => void handleSampleComputerUse()} disabled={computerSampling}>{t("components.rpa.RPAWorkbench.studioSampleElements")}</Button>
                                        <Button size="sm" variant="outline" onClick={() => void handleBrowserCaptureStart()} disabled={!activeRecording || browserCaptureActive}>{t("components.rpa.RPAWorkbench.studioBrowserCapture")}</Button>
                                    </div>
                                    <label className="flex items-center gap-2 rounded-xl border border-border/60 p-2.5 text-xs">
                                        <input type="checkbox" checked={recordAndForward} onChange={event => setRecordAndForward(event.target.checked)} />
                                        {t("components.rpa.RPAWorkbench.studioRecordAndForwardTargetDebug")}
                                    </label>
                                    <div className="rounded-xl border border-border/60 bg-muted/20 p-2.5 text-[11px] text-muted-foreground">
                                        {t("components.rpa.RPAWorkbench.studioNativeHotkeyBackend")}：
                                        {firstString(nativeHotkeyBackend?.backend) || "fallback_overlay"}
                                        {" · "}
                                        {firstString(nativeHotkeyBackend?.state) || t("components.rpa.RPAWorkbench.studioNativeHotkeyFallback")}
                                    </div>
                                    {desktopLiveSessionId ? <div className="overflow-hidden rounded-xl border border-border/60">
                                            <div className="relative h-[210px] w-full overflow-hidden bg-muted/20">
                                                {/* eslint-disable-next-line @next/next/no-img-element -- multipart desktop live stream cannot use next/image */}
                                                <img ref={liveImageRef} src={`/api/desktop-live/stream?sessionId=${encodeURIComponent(desktopLiveSessionId)}`} alt="Desktop live" className="h-full w-full select-none object-contain" draggable={false} />
                                                <div className="absolute left-2 top-2 rounded-full bg-background/90 px-2 py-1 text-[11px] font-medium text-muted-foreground shadow-sm">
                                                    {t("components.rpa.RPAWorkbench.studioLivePreviewOnly")}
                                                </div>
                                            </div>
                                        </div> : <div className="rounded-xl border border-dashed p-5 text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.studioLiveEmpty")}</div>}
                                    <div className="grid gap-3">
                                        <div className="flex items-center justify-between">
                                            <div>
                                                <div className="text-xs font-semibold">{t("components.rpa.RPAWorkbench.studioCapturePoolTitle")}</div>
                                                <div className="text-[11px] text-muted-foreground">{t("components.rpa.RPAWorkbench.studioCapturePoolDescription")}</div>
                                            </div>
                                            <Badge variant="outline" className="text-[10px]">{capturePoolItems.length}</Badge>
                                        </div>
                                        <ScrollArea className="max-h-44 pr-2">
                                            <div className="space-y-2">
                                                {capturePoolItems.map(item => <div key={item.tempElementId || captureItemLabel(item)} className="rounded-xl border border-border/60 p-2.5 text-xs">
                                                        <div className="flex items-start justify-between gap-2">
                                                            <div className="min-w-0">
                                                                <AdminHoverInfo content={captureItemLabel(item)} panelClassName="w-auto max-w-[26rem] whitespace-normal">
                                                                    <div className="truncate font-medium">{captureItemLabel(item)}</div>
                                                                </AdminHoverInfo>
                                                                <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{captureItemSelectorValue(item) || captureItemCoordinateValue(item) || item.source}</div>
                                                            </div>
                                                            {item.fragileCoordinateFallback ? <Badge variant="outline" className="shrink-0 text-[10px]">{t("components.rpa.RPAWorkbench.fragileCoordinate")}</Badge> : null}
                                                        </div>
                                                        <div className="mt-2 flex flex-wrap gap-1.5">
                                                            <Button size="sm" variant="outline" className="h-7 px-2 text-[11px]" onClick={() => applyElementToSelectedStep(item)} disabled={!selectedBuilderStep}>{t("components.rpa.RPAWorkbench.studioUseElement")}</Button>
                                                            <Button size="sm" variant="ghost" className="h-7 px-2 text-[11px]" onClick={() => void handleSaveCapturePoolItem(item)}>{t("components.rpa.RPAWorkbench.studioSaveElement")}</Button>
                                                        </div>
                                                    </div>)}
                                                {!capturePoolItems.length ? <div className="rounded-xl border border-dashed p-4 text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.studioCapturePoolEmpty")}</div> : null}
                                            </div>
                                        </ScrollArea>
                                    </div>
                                    <div className="grid gap-3">
                                        <div className="flex items-center justify-between">
                                            <div>
                                                <div className="text-xs font-semibold">{t("components.rpa.RPAWorkbench.studioObjectLibraryTitle")}</div>
                                                <div className="text-[11px] text-muted-foreground">{t("components.rpa.RPAWorkbench.studioObjectLibraryDescription")}</div>
                                            </div>
                                            <Badge variant="outline" className="text-[10px]">{objectLibraryItems.length}</Badge>
                                        </div>
                                        <ScrollArea className="max-h-40 pr-2">
                                            <div className="space-y-2">
                                                {objectLibraryItems.map(item => <div key={item.elementId || item.tempElementId || captureItemLabel(item)} className="rounded-xl border border-border/60 p-2.5 text-xs">
                                                        <AdminHoverInfo content={captureItemLabel(item)} panelClassName="w-auto max-w-[26rem] whitespace-normal">
                                                            <div className="truncate font-medium">{captureItemLabel(item)}</div>
                                                        </AdminHoverInfo>
                                                        <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{captureItemSelectorValue(item) || captureItemCoordinateValue(item) || item.source}</div>
                                                        <Button size="sm" variant="outline" className="mt-2 h-7 px-2 text-[11px]" onClick={() => applyElementToSelectedStep(item)} disabled={!selectedBuilderStep}>{t("components.rpa.RPAWorkbench.studioUseElement")}</Button>
                                                    </div>)}
                                                {!objectLibraryItems.length ? <div className="rounded-xl border border-dashed p-4 text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.studioObjectLibraryEmpty")}</div> : null}
                                            </div>
                                        </ScrollArea>
                                    </div>
                                    {stepValidation ? <pre className="max-h-48 overflow-auto rounded-xl bg-muted/30 p-3 text-xs">{prettyJson(stepValidation)}</pre> : null}
                                </div> : null}
                            {studioRightPanel === "runs" ? <div className="space-y-3">
                                    <div className="text-sm font-semibold">{t("components.rpa.RPAWorkbench.studioRunsTitle")}</div>
                                    <Button className="w-full" onClick={() => selectedDraftId ? void handleDraftAction("run") : undefined} disabled={!selectedDraftId || isArchivedDraft(selectedDraft) || !!busyAction}>{t("components.rpa.RPAWorkbench.studioDebugRunDraft")}</Button>
                                    <Button className="w-full" variant="outline" onClick={() => selectedDraftId ? void handleApproveDraftAsTemplate() : undefined} disabled={!selectedDraftId || !selectedDraft || isArchivedDraft(selectedDraft) || !!busyAction}>{t("components.rpa.RPAWorkbench.studioApproveDraftTemplate")}</Button>
                                    <ScrollArea className="h-[470px] pr-3">
                                        <div className="space-y-2">
                                            {rpaRuns.map(run => <div key={run.id} className="rounded-xl border border-border/60 p-3 text-xs">
                                                    <div className="font-medium">{run.status || "queued"}</div>
                                                    <div className="mt-1 break-all text-muted-foreground">{run.id}</div>
                                                </div>)}
                                            {!rpaRuns.length ? <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{t("components.rpa.RPAWorkbench.studioNoRpaRuns")}</div> : null}
                                        </div>
                                    </ScrollArea>
                                </div> : null}
                            {studioRightPanel === "diagnostics" ? <div className="space-y-3">
                                    <div className="text-sm font-semibold">{t("components.rpa.RPAWorkbench.studioDiagnosticsTitle")}</div>
                                    <pre className="max-h-[540px] overflow-auto rounded-xl bg-muted/30 p-3 text-xs">{latestResult ? prettyJson(latestResult) : t("components.rpa.RPAWorkbench.studioNoDiagnostics")}</pre>
                                    <Button variant="outline" onClick={() => setShowLegacyPanel(current => !current)}>{showLegacyPanel ? t("components.rpa.RPAWorkbench.studioHideLegacy") : t("components.rpa.RPAWorkbench.studioOpenLegacy")}</Button>
                                </div> : null}
                        </aside>
                    </div>
                </CardContent>
            </Card>

            {showLegacyPanel ? <>

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <Card className="border-border/60">
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base">Robot Framework</CardTitle>
                        <CardDescription>{tg(t, "06e27187")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2">
                        <Badge variant={probeState(availability.robotFrameworkDetail).variant} className="max-w-full overflow-hidden">
                            <span className="block max-w-[10rem] overflow-hidden whitespace-nowrap [mask-image:linear-gradient(90deg,#000_82%,transparent)]">
                                {t(probeState(availability.robotFrameworkDetail).label)}
                            </span>
                        </Badge>
                        {availability.robotFrameworkDetail?.origin ? <div className="break-all text-xs text-muted-foreground">{availability.robotFrameworkDetail.origin}</div> : null}
                        {availability.robotFrameworkDetail?.error ? <div className="text-xs text-destructive">{availability.robotFrameworkDetail.error}</div> : null}
                    </CardContent>
                </Card>
                <Card className="border-border/60">
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base">RPA Framework</CardTitle>
                        <CardDescription>{tg(t, "4787a117")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2">
                        <Badge variant={probeState(availability.rpaFrameworkDetail).variant} className="max-w-full overflow-hidden">
                            <span className="block max-w-[10rem] overflow-hidden whitespace-nowrap [mask-image:linear-gradient(90deg,#000_82%,transparent)]">
                                {t(probeState(availability.rpaFrameworkDetail).label)}
                            </span>
                        </Badge>
                        {availability.rpaFrameworkDetail?.origin ? <div className="break-all text-xs text-muted-foreground">{availability.rpaFrameworkDetail.origin}</div> : null}
                        {availability.rpaFrameworkDetail?.error ? <div className="text-xs text-destructive">{availability.rpaFrameworkDetail.error}</div> : null}
                    </CardContent>
                </Card>
                <Card className="border-border/60 md:col-span-2">
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base">{tg(t, "e9e8406f")}</CardTitle>
                        <CardDescription>{tg(t, "5f35173c")}</CardDescription>
                    </CardHeader>
                    <CardContent className="grid gap-3 md:grid-cols-2">
                        {Object.entries(availability.libraryDetails || {}).map(([name, detail]) => <div key={name} className="rounded-xl border border-border/60 p-3">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0 flex-1 break-words text-sm font-medium">{name}</div>
                                    <Badge variant={probeState(detail).variant} className="max-w-[11rem] shrink-0 overflow-hidden">
                                        <span className="block overflow-hidden whitespace-nowrap [mask-image:linear-gradient(90deg,#000_82%,transparent)]">
                                            {t(probeState(detail).label)}
                                        </span>
                                    </Badge>
                                </div>
                                {detail?.origin ? <div className="mt-2 break-all text-xs text-muted-foreground">{detail.origin}</div> : null}
                                {detail?.error ? <div className="mt-2 text-xs text-destructive">{detail.error}</div> : null}
                            </div>)}
                    </CardContent>
                </Card>
            </div>

            <Card className="border-border/60">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-lg">
                        <Video className="h-5 w-5 text-primary" />
                        {t("components.rpa.RPAWorkbench.recordingTitle")}
                    </CardTitle>
                    <CardDescription>{t("components.rpa.RPAWorkbench.recordingDescription")}</CardDescription>
                </CardHeader>
                <CardContent className="grid gap-5 xl:grid-cols-[1fr_1.15fr]">
                    <div className="space-y-4">
                        <div className="grid gap-3 md:grid-cols-2">
                            <div className="grid gap-2">
                                <Label htmlFor="recording-name">{t("components.rpa.RPAWorkbench.flowName")}</Label>
                                <Input id="recording-name" value={recordingName} onChange={event => setRecordingName(event.target.value)} placeholder={t("components.rpa.RPAWorkbench.flowNamePlaceholder")} />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="recording-app-id">{t("components.rpa.RPAWorkbench.targetApp")}</Label>
                                <Input id="recording-app-id" value={recordingAppId} onChange={event => setRecordingAppId(event.target.value)} placeholder="desktop / chrome / wechat" />
                            </div>
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="recording-goal">{t("components.rpa.RPAWorkbench.flowGoal")}</Label>
                            <Textarea id="recording-goal" className="min-h-[84px]" value={recordingGoal} onChange={event => setRecordingGoal(event.target.value)} placeholder={t("components.rpa.RPAWorkbench.flowGoalPlaceholder")} />
                        </div>
                        <div className="grid gap-3 md:grid-cols-3">
                            <div className="grid gap-2">
                                <Label htmlFor="recording-target-mode">{t("components.rpa.RPAWorkbench.recordingTarget")}</Label>
                                <select id="recording-target-mode" className="h-10 rounded-md border border-input bg-background px-3 text-sm" value={recordingTargetMode} onChange={event => setRecordingTargetMode(event.target.value)}>
                                    <option value="agent_browser">{t("components.rpa.RPAWorkbench.targetAgentBrowser")}</option>
                                    <option value="desktop_window">{t("components.rpa.RPAWorkbench.targetDesktopWindow")}</option>
                                    <option value="launch_app">{t("components.rpa.RPAWorkbench.targetLaunchApp")}</option>
                                </select>
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="recording-browser-kind">{t("components.rpa.RPAWorkbench.browserKind")}</Label>
                                <select id="recording-browser-kind" className="h-10 rounded-md border border-input bg-background px-3 text-sm" value={recordingBrowserKind} onChange={event => setRecordingBrowserKind(event.target.value)}>
                                    <option value="chrome">Chrome</option>
                                    <option value="edge">Edge</option>
                                </select>
                            </div>
                            <div className="grid gap-2">
                                <Label>{t("components.rpa.RPAWorkbench.recordingState")}</Label>
                                <div className="flex h-10 items-center gap-2 rounded-md border border-border/60 px-3 text-sm">
                                    <Badge variant={activeRecording?.state === "recording" ? "default" : activeRecording?.state === "failed" ? "destructive" : "secondary"}>{activeRecording?.state || "idle"}</Badge>
                                    {activeRecording?.stepCount != null ? <span className="text-muted-foreground">{activeRecording.stepCount} steps</span> : null}
                                </div>
                            </div>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            <Button onClick={() => void handleStartRecording()} disabled={!!busyAction || activeRecording?.state === "recording"}>
                                <Video className="mr-2 h-4 w-4" />
                                {t("components.rpa.RPAWorkbench.startRecording")}
                            </Button>
                            <Button variant="outline" onClick={() => void handleRecordingControl("pause")} disabled={!activeRecording || activeRecording.state !== "recording" || !!busyAction}>
                                <Pause className="mr-2 h-4 w-4" />
                                {t("components.rpa.RPAWorkbench.pauseRecording")}
                            </Button>
                            <Button variant="outline" onClick={() => void handleRecordingControl("resume")} disabled={!activeRecording || activeRecording.state !== "paused" || !!busyAction}>
                                <Play className="mr-2 h-4 w-4" />
                                {t("components.rpa.RPAWorkbench.resumeRecording")}
                            </Button>
                            <Button variant="outline" onClick={() => void handleRecordingControl("stop")} disabled={!activeRecording || !["recording", "paused", "stopped"].includes(activeRecording.state || "") || !!busyAction}>
                                <Square className="mr-2 h-4 w-4" />
                                {t("components.rpa.RPAWorkbench.stopAndCompile")}
                            </Button>
                            <Button variant="ghost" onClick={() => void handleOpenAgentBrowser()} disabled={!!busyAction}>
                                {t("components.rpa.RPAWorkbench.openAgentBrowser")}
                            </Button>
                        </div>
                        <div className="rounded-xl border border-border/60 bg-muted/20 p-3 text-xs text-muted-foreground">
                            {activeRecording ? <>
                                    <div>{t("components.rpa.RPAWorkbench.recordingId")}: <span className="text-foreground">{activeRecording.recordingSessionId}</span></div>
                                    <div>traceRunId: <span className="text-foreground">{activeRecording.traceRunId}</span></div>
                                    {activeRecording.createdDraftId ? <div>{t("components.rpa.RPAWorkbench.createdDraft")}: <span className="text-foreground">{activeRecording.createdDraftId}</span></div> : null}
                                    {activeRecording.compileError ? <div className="text-destructive">{activeRecording.compileError}</div> : null}
                                </> : <>
                                    <div>{t("components.rpa.RPAWorkbench.noRecordingYet")}</div>
                                    <div>{t("components.rpa.RPAWorkbench.recentRecordings")}: <span className="text-foreground">{recordings.length}</span></div>
                                </>}
                        </div>
                    </div>
                    <div className="space-y-4">
                        <div className="rounded-xl border border-dashed border-primary/30 bg-primary/5 p-3 text-sm text-muted-foreground">
                            {t("components.rpa.RPAWorkbench.liveBoundaryNote")}
                        </div>
                        <div className="rounded-xl border border-border/60 bg-muted/20 p-3">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                    <div className="text-sm font-semibold">{t("components.rpa.RPAWorkbench.desktopLiveOverlay")}</div>
                                    <div className="text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.desktopLiveOverlayDescription")}</div>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <Button size="sm" variant="outline" onClick={() => void handlePrepareDesktopLive()} disabled={desktopLiveLoading}>
                                        <Video className="mr-2 h-4 w-4" />
                                        {desktopLiveSessionId ? t("components.rpa.RPAWorkbench.restartLivePreview") : t("components.rpa.RPAWorkbench.startLivePreview")}
                                    </Button>
                                    <Button size="sm" variant="outline" onClick={() => void handleSampleComputerUse()} disabled={computerSampling}>
                                        <RefreshCw className="mr-2 h-4 w-4" />
                                        {t("components.rpa.RPAWorkbench.sampleComputerUse")}
                                    </Button>
                                    {recordingTargetMode === "agent_browser" ? <>
                                            <Button size="sm" variant="outline" onClick={() => void handleBrowserCaptureStart()} disabled={!activeRecording || browserCaptureActive}>
                                                <MousePointerClick className="mr-2 h-4 w-4" />
                                                {t("components.rpa.RPAWorkbench.startBrowserCapture")}
                                            </Button>
                                            <Button size="sm" variant="outline" onClick={() => void handleBrowserCapturePoll()} disabled={!browserCaptureActive || browserCapturePolling}>
                                                <RefreshCw className="mr-2 h-4 w-4" />
                                                {t("components.rpa.RPAWorkbench.pollBrowserCapture")}
                                            </Button>
                                            <Button size="sm" variant="ghost" onClick={() => void handleBrowserCaptureStop()} disabled={!browserCaptureActive}>
                                                {t("components.rpa.RPAWorkbench.stopBrowserCapture")}
                                            </Button>
                                        </> : null}
                                    {desktopLiveSessionId ? <Button size="sm" variant="ghost" onClick={() => void handleReleaseDesktopLive()} disabled={desktopLiveLoading}>
                                            {t("components.rpa.RPAWorkbench.stopLivePreview")}
                                        </Button> : null}
                                </div>
                            </div>
                            <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted-foreground">
                                <label className="flex items-center gap-2 rounded-md border border-border/60 bg-background/70 px-3 py-2">
                                    <input type="checkbox" checked={recordAndForward} onChange={event => setRecordAndForward(event.target.checked)} />
                                    {t("components.rpa.RPAWorkbench.recordAndForward")}
                                </label>
                                <span className="rounded-md border border-border/60 bg-background/70 px-3 py-2">
                                    {browserCaptureActive ? t("components.rpa.RPAWorkbench.browserCaptureActive") : t("components.rpa.RPAWorkbench.browserCaptureIdle")}
                                </span>
                            </div>
                            {desktopLiveError ? <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">{desktopLiveError}</div> : null}
                            {desktopLiveSessionId ? <div className="mt-3 overflow-hidden rounded-lg border border-border/60 bg-background">
                                    <div
                                        role="application"
                                        tabIndex={0}
                                        className="relative h-[280px] w-full cursor-crosshair overflow-hidden outline-none focus:ring-2 focus:ring-primary/40"
                                        onClick={event => void handleLivePreviewClick(event)}
                                        onDoubleClick={event => void handleLivePreviewDoubleClick(event)}
                                        onContextMenu={event => void handleLivePreviewContextMenu(event)}
                                        onWheel={event => void handleLivePreviewWheel(event)}
                                        onPointerDown={handleLivePreviewPointerDown}
                                        onPointerUp={event => void handleLivePreviewPointerUp(event)}
                                        onKeyDown={event => void handleLivePreviewKeyDown(event)}
                                    >
                                        {/* eslint-disable-next-line @next/next/no-img-element -- multipart desktop live stream cannot use next/image */}
                                        <img ref={liveImageRef} src={`/api/desktop-live/stream?sessionId=${encodeURIComponent(desktopLiveSessionId)}`} alt={t("components.rpa.RPAWorkbench.desktopLiveOverlay")} className="h-full w-full select-none object-contain" draggable={false} />
                                        <div className="pointer-events-none absolute bottom-2 left-2 rounded-md bg-background/80 px-2 py-1 text-[11px] text-muted-foreground shadow-sm">
                                            {recordAndForward ? t("components.rpa.RPAWorkbench.overlayRecordForwardHint") : t("components.rpa.RPAWorkbench.overlayRecordOnlyHint")}
                                        </div>
                                    </div>
                                </div> : <div className="mt-3 rounded-lg border border-dashed border-border/70 p-6 text-center text-sm text-muted-foreground">
                                    {t("components.rpa.RPAWorkbench.noLivePreview")}
                                </div>}
                            {latestComputerObservation?.summary && isPlainRecord(latestComputerObservation.summary) ? <div className="mt-3 grid gap-2 rounded-lg border border-border/60 bg-background/70 p-3 text-xs text-muted-foreground md:grid-cols-3">
                                    <div>
                                        <span className="font-medium text-foreground">{t("components.rpa.RPAWorkbench.observedWindow")}</span>
                                        <div className="truncate">{firstString(latestComputerObservation.summary.windowTitle) || "n/a"}</div>
                                    </div>
                                    <div>
                                        <span className="font-medium text-foreground">{t("components.rpa.RPAWorkbench.selectorCandidates")}</span>
                                        <div>{String(latestComputerObservation.summary.candidateCount ?? 0)}</div>
                                    </div>
                                    <div>
                                        <span className="font-medium text-foreground">{t("components.rpa.RPAWorkbench.bestSelector")}</span>
                                        <div className="truncate">{firstString(latestComputerObservation.summary.bestSelector) || "n/a"}</div>
                                    </div>
                                </div> : null}
                        </div>
                        <div className="grid gap-3 md:grid-cols-3">
                            <div className="grid gap-2">
                                <Label htmlFor="recording-action">{t("components.rpa.RPAWorkbench.actionType")}</Label>
                                <select id="recording-action" className="h-10 rounded-md border border-input bg-background px-3 text-sm" value={recordingAction} onChange={event => setRecordingAction(event.target.value)}>
                                    <option value="click">click</option>
                                    <option value="type_text">type_text</option>
                                    <option value="scroll">scroll</option>
                                    <option value="drag">drag</option>
                                    <option value="wait_for_element">wait</option>
                                    <option value="assert_condition">assert</option>
                                    <option value="launch_app">launch_app</option>
                                </select>
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="recording-x">X</Label>
                                <Input id="recording-x" value={recordingX} onChange={event => setRecordingX(event.target.value)} placeholder="124" />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="recording-y">Y</Label>
                                <Input id="recording-y" value={recordingY} onChange={event => setRecordingY(event.target.value)} placeholder="320" />
                            </div>
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="recording-intent">{t("components.rpa.RPAWorkbench.stepIntent")}</Label>
                            <Input id="recording-intent" value={recordingIntent} onChange={event => setRecordingIntent(event.target.value)} placeholder={t("components.rpa.RPAWorkbench.stepIntentPlaceholder")} />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="recording-selector">{t("components.rpa.RPAWorkbench.selectorOrAnchor")}</Label>
                            <Input id="recording-selector" value={recordingSelector} onChange={event => setRecordingSelector(event.target.value)} placeholder="button[aria-label='Submit'] / role=button[name=...]" />
                        </div>
                        <div className="grid gap-3 md:grid-cols-[1fr_14rem]">
                            <div className="grid gap-2">
                                <Label htmlFor="recording-params">{t("components.rpa.RPAWorkbench.eventParams")}</Label>
                                <Textarea id="recording-params" className="min-h-[120px] font-mono text-xs" value={recordingParamsText} onChange={event => setRecordingParamsText(event.target.value)} />
                            </div>
                            <div className="space-y-3">
                                <div className="grid gap-2">
                                    <Label htmlFor="recording-variable">{t("components.rpa.RPAWorkbench.variableName")}</Label>
                                    <Input id="recording-variable" value={recordingVariableName} onChange={event => setRecordingVariableName(event.target.value)} placeholder="user_name" />
                                </div>
                                <label className="flex items-center gap-2 rounded-md border border-border/60 p-3 text-sm">
                                    <input type="checkbox" checked={recordingSensitive} onChange={event => setRecordingSensitive(event.target.checked)} />
                                    {t("components.rpa.RPAWorkbench.markSensitive")}
                                </label>
                            </div>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            <Button onClick={() => void handleAppendRecordingEvent()} disabled={!activeRecording || activeRecording.state !== "recording" || !!busyAction}>
                                {t("components.rpa.RPAWorkbench.addRecordedAction")}
                            </Button>
                            <Button variant="outline" onClick={() => void handleAppendRecordingEvent("wait_for_element")} disabled={!activeRecording || activeRecording.state !== "recording" || !!busyAction}>
                                {t("components.rpa.RPAWorkbench.insertWait")}
                            </Button>
                            <Button variant="outline" onClick={() => void handleAppendRecordingEvent("assert_condition")} disabled={!activeRecording || activeRecording.state !== "recording" || !!busyAction}>
                                {t("components.rpa.RPAWorkbench.insertAssert")}
                            </Button>
                        </div>
                    </div>
                </CardContent>
            </Card>

            <div className="grid gap-6 xl:grid-cols-[1.2fr_1fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-lg">
                            <Wand2 className="h-5 w-5 text-primary" />
                            {tg(t, "83eb2c86")}
                        </CardTitle>
                        <CardDescription>{tg(t, "80b3232f")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-2">
                            <Label htmlFor="compile-run-id">ComputerUse run_id</Label>
                            <Input id="compile-run-id" value={compileRunId} onChange={event => setCompileRunId(event.target.value)} placeholder={t("components.rpa.RPAWorkbench.k5b954856")} />
                        </div>
                        <Button onClick={() => void handleCompile()} disabled={busyAction === "compile"}>
                            <FileCode2 className="mr-2 h-4 w-4" />
                            {tg(t, "49a24181")}
                        </Button>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">{tg(t, "b215c993")}</CardTitle>
                        <CardDescription>{tg(t, "e2efa0cc")}</CardDescription>
                    </CardHeader>
                    <CardContent className="grid gap-4">
                        <div className="grid gap-2">
                            <Label htmlFor="cwd">{tg(t, "a0d7822e")}</Label>
                            <Input id="cwd" value={cwd} onChange={event => setCwd(event.target.value)} placeholder={t("components.rpa.RPAWorkbench.kcb17052e")} />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="output-dir">{tg(t, "fd576472")}</Label>
                            <Input id="output-dir" value={outputDir} onChange={event => setOutputDir(event.target.value)} placeholder={t("components.rpa.RPAWorkbench.kbc389513")} />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="timeout-ms">{tg(t, "a3ce47a7")}</Label>
                            <Input id="timeout-ms" type="number" value={timeoutMs} onChange={event => setTimeoutMs(event.target.value)} />
                        </div>
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 xl:grid-cols-[1.15fr_1fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                                <CardTitle className="text-lg">{tg(t, "17269e32")}</CardTitle>
                                <CardDescription>{tg(t, "f068e67b")}</CardDescription>
                            </div>
                            <Button variant={showArchivedDrafts ? "secondary" : "outline"} size="sm" onClick={() => setShowArchivedDrafts(value => !value)}>
                                {t("components.rpa.RPAWorkbench.includeArchived")}
                            </Button>
                        </div>
                    </CardHeader>
                    <CardContent>
                        <ScrollArea className="h-[420px] pr-4">
                            <div className="space-y-3">
                                {drafts.length === 0 ? <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{tg(t, "ffb6da59")}</div> : drafts.map(draft => {
                const selected = draft.id === selectedDraftId;
                const archived = isArchivedDraft(draft);
                const highRisk = (draft.steps || []).some(item => item.approval?.mode);
                const assessment = draft.assessment;
                const reviewRequired = String(assessment?.status || "").includes("review");
                return <button key={draft.id} type="button" onClick={() => setSelectedDraftId(draft.id)} className={`w-full rounded-2xl border p-4 text-left transition-colors ${selected ? "border-primary bg-primary/5" : "border-border/60 hover:border-primary/40 hover:bg-muted/30"}`}>

                                                <div className="flex flex-wrap items-start justify-between gap-3">
                                                    <div>
                                                        <div className="text-sm font-medium">{draft.name || draft.id}</div>
                                                        <div className="mt-1 text-xs text-muted-foreground">{draft.id}</div>
                                                    </div>
                                                    <div className="flex flex-wrap gap-2">
                                                        <Badge variant="outline">{draft.appId || "desktop"}</Badge>
                                                        <Badge variant={highRisk ? "destructive" : "secondary"}>
                                                            {tg(t, "cc7caf6a")}
                                                        </Badge>
                                                        {assessment?.status ? <Badge variant={reviewRequired ? "destructive" : "secondary"}>
                                                                {assessment.status}{assessment.band ? ` · ${assessment.band}` : ""}
                                                            </Badge> : null}
                                                        {archived ? <Badge variant="outline">{t("components.rpa.RPAWorkbench.archived")}</Badge> : null}
                                                    </div>
                                                </div>
                                                <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
                                                    <span>{(draft.steps || []).length} steps</span>
                                                    <span>·</span>
                                                    <span>{(draft.variables || []).length} vars</span>
                                                    {assessment?.score != null ? <>
                                                            <span>·</span>
                                                            <span>{t("components.memory.MemoryWorkflowsPanel.confidence")} {formatConfidence(assessment.score)}</span>
                                                        </> : null}
                                                </div>
                                                {assessment?.reasons?.length ? <div className="mt-2 text-xs text-muted-foreground">
                                                        {assessment.reasons[0]}
                                                    </div> : null}
                                                {assessment ? <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                                                        <span>accepted {assessment.acceptedSteps ?? 0}</span>
                                                        <span>review {assessment.reviewRequiredSteps ?? 0}</span>
                                                        <span>excluded {assessment.excludedSteps ?? 0}</span>
                                                    </div> : null}
                                                {assessment?.signals?.historicalScriptRuns ? <div className="mt-1 text-[11px] text-muted-foreground">
                                                        {tg(t, "be78b205")} {assessment.signals.historicalScriptRuns} {tg(t, "fcdfff9f")} {formatRatio(assessment.signals.historicalScriptCompletedRate)} {tg(t, "e1765a36")} {formatRatio(assessment.signals.historicalScriptFallbackHeavyRate)}
                                                    </div> : null}
                                                <div className="mt-3 flex flex-wrap gap-1.5">
                                                    {(draft.robot?.tags || []).slice(0, 5).map(tag => <Badge key={`${draft.id}:${tag}`} variant="secondary">{tag}</Badge>)}
                                                </div>
                                            </button>;
              })}
                            </div>
                        </ScrollArea>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">{tg(t, "1ef00e19")}</CardTitle>
                        <CardDescription>{selectedDraft ? `${selectedDraft.name || selectedDraft.id}` : tg(t, "9b331209")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
                            <div className="grid gap-2">
                                <Label htmlFor="draft-vars">{t("components.rpa.RPAWorkbench.runtimeVariablesJson")}</Label>
                                <Textarea id="draft-vars" className="min-h-[150px] font-mono text-xs" value={variablesText} onChange={event => setVariablesText(event.target.value)} />
                                <p className="text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.runtimeVariablesJsonHint")}</p>
                            </div>
                            <div className="rounded-xl border border-border/60 bg-background p-3">
                                <div className="mb-3 flex items-center justify-between gap-2">
                                    <div>
                                        <div className="text-sm font-medium">{t("components.rpa.RPAWorkbench.variableSchema")}</div>
                                        <div className="text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.variableSchemaHint")}</div>
                                    </div>
                                    <Button size="sm" variant="outline" onClick={handleAddVariableRow}>
                                        <Plus className="mr-2 h-4 w-4" />
                                        {t("components.rpa.RPAWorkbench.addVariable")}
                                    </Button>
                                </div>
                                <ScrollArea className="h-[150px] pr-3">
                                    <div className="space-y-2">
                                        {draftVariableRows.map(row => <div key={row.id} className="grid gap-2 rounded-lg border border-border/50 p-2 md:grid-cols-[1fr_7rem_5rem_1fr_auto]">
                                                <Input value={row.name} onChange={event => updateVariableRow(row.id, { name: event.target.value })} placeholder="customer_name" className="h-8" />
                                                <select className="h-8 rounded-md border border-input bg-background px-2 text-xs" value={row.type} onChange={event => updateVariableRow(row.id, { type: event.target.value })}>
                                                    <option value="text">{t("components.rpa.RPAWorkbench.varTypeText")}</option>
                                                    <option value="number">{t("components.rpa.RPAWorkbench.varTypeNumber")}</option>
                                                    <option value="boolean">{t("components.rpa.RPAWorkbench.varTypeBoolean")}</option>
                                                    <option value="secret">{t("components.rpa.RPAWorkbench.varTypeSecret")}</option>
                                                </select>
                                                <label className="flex items-center gap-1 text-xs text-muted-foreground">
                                                    <input type="checkbox" checked={row.required} onChange={event => updateVariableRow(row.id, { required: event.target.checked })} />
                                                    {t("components.rpa.RPAWorkbench.required")}
                                                </label>
                                                <Input value={row.sensitive ? row.secretName : row.defaultValue} onChange={event => updateVariableRow(row.id, row.sensitive ? { secretName: event.target.value } : { defaultValue: event.target.value })} placeholder={row.sensitive ? "secret:github_token" : t("components.rpa.RPAWorkbench.defaultValue")} className="h-8" />
                                                <div className="flex items-center gap-1">
                                                    <Button type="button" variant={row.sensitive ? "secondary" : "ghost"} size="sm" onClick={() => updateVariableRow(row.id, { sensitive: !row.sensitive, type: !row.sensitive ? "secret" : "text" })}>
                                                        {t("components.rpa.RPAWorkbench.secret")}
                                                    </Button>
                                                    <Button type="button" variant="ghost" size="sm" onClick={() => handleDeleteVariableRow(row.id)}>
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                </div>
                                            </div>)}
                                        {draftVariableRows.length === 0 ? <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.noVariables")}</div> : null}
                                    </div>
                                </ScrollArea>
                            </div>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            <Button variant="ghost" onClick={() => void handleViewSourceTrace()} disabled={!selectedDraftId || busyAction === `draft:source:${selectedDraftId}`}>
                                {tg(t, "b1a93773")}
                            </Button>
                            <Button variant="outline" onClick={() => void handleDraftAction("export")} disabled={!selectedDraftId || busyAction === "draft:export"}>
                                {tg(t, "9f9eca77")}
                            </Button>
                            <Button variant="outline" onClick={() => void handleDraftAction("prepare")} disabled={!selectedDraftId || busyAction === "draft:prepare"}>
                                {tg(t, "c6611470")}
                            </Button>
                            <Button onClick={() => void handleDraftAction("run")} disabled={!selectedDraftId || isArchivedDraft(selectedDraft) || busyAction === "draft:run"}>
                                <Play className="mr-2 h-4 w-4" />
                                {tg(t, "8b377b85")}
                            </Button>
                            {isArchivedDraft(selectedDraft) ? <Button variant="outline" onClick={() => void handleDraftGovernanceAction("restore")} disabled={!selectedDraftId || busyAction === `draft:restore:${selectedDraftId}`}>
                                    {t("components.rpa.RPAWorkbench.restore")}
                                </Button> : <Button variant="outline" onClick={() => void handleDraftGovernanceAction("archive")} disabled={!selectedDraftId || busyAction === `draft:archive:${selectedDraftId}`}>
                                    {t("components.rpa.RPAWorkbench.archive")}
                                </Button>}
                            <Button variant="destructive" onClick={() => void handleDraftGovernanceAction("delete")} disabled={!selectedDraftId || busyAction === `draft:delete:${selectedDraftId}`}>
                                <Trash2 className="mr-2 h-4 w-4" />
                                {t("components.rpa.RPAWorkbench.hardDelete")}
                            </Button>
                            <Button variant="default" onClick={() => void handleApproveDraftAsTemplate()} disabled={!selectedDraftId || !selectedDraft || isArchivedDraft(selectedDraft) || busyAction === `draft:approve-template:${selectedDraftId}`}>
                                <CheckCircle2 className="mr-2 h-4 w-4" />
                                {t("components.rpa.RPAWorkbench.approveDraftAsTemplate")}
                            </Button>
                        </div>
                        {selectedDraft ? <div className="rounded-xl border border-border/60 bg-muted/20 p-3 text-xs text-muted-foreground">
                                {selectedDraft.assessment ? <div className="space-y-1 pb-2 text-foreground">
                                        <div>{tg(t, "c1a6f6f5")}{selectedDraft.assessment.status || "unknown"}{selectedDraft.assessment.band ? ` · ${selectedDraft.assessment.band}` : ""} {tg(t, "fe6b1c17")} {formatConfidence(selectedDraft.assessment.score)}</div>
                                        <div className="text-xs text-muted-foreground">
                                            accepted {selectedDraft.assessment.acceptedSteps ?? 0} · review {selectedDraft.assessment.reviewRequiredSteps ?? 0} · excluded {selectedDraft.assessment.excludedSteps ?? 0}
                                        </div>
                                        {selectedDraft.assessment.signals ? <div className="text-xs text-muted-foreground">
                                                acceptedRatio {formatRatio(selectedDraft.assessment.signals.acceptedRatio)} · nativeRatio {formatRatio(selectedDraft.assessment.signals.nativeSemanticRatio)} · recoveryHeavy {formatRatio(selectedDraft.assessment.signals.recoveryHeavyRatio)} · profileAugmented {formatRatio(selectedDraft.assessment.signals.profileAugmentedRatio)}
                                            </div> : null}
                                        {selectedDraft.assessment.signals ? <div className="text-xs text-muted-foreground">
                                                {tg(t, "be78b205")} {selectedDraft.assessment.signals.historicalScriptRuns ?? 0} {tg(t, "fcdfff9f")} {formatRatio(selectedDraft.assessment.signals.historicalScriptCompletedRate)} · review {formatRatio(selectedDraft.assessment.signals.historicalScriptReviewRequiredRate)} · blocked {formatRatio(selectedDraft.assessment.signals.historicalScriptCompileBlockedRate)}
                                            </div> : null}
                                        {selectedDraft.assessment.signals ? <div className="text-xs text-muted-foreground">
                                            {tg(t, "22fc3a85")} {formatCalibrationSource(t, selectedDraft.assessment.signals.historicalScriptCalibrationSource)} · calibratedSteps {selectedDraft.assessment.signals.calibratedSteps ?? 0} · profileSteps {selectedDraft.assessment.signals.profileAugmentedSteps ?? 0} {tg(t, "efd7c6ce")} {formatRatio(selectedDraft.assessment.signals.historicalScriptProfileAugmentedRatio)} {tg(t, "40328829")} {formatRatio(selectedDraft.assessment.signals.historicalScriptNativeSuccessRate ?? selectedDraft.assessment.signals.historicalNativeSuccessRate)}
                                        </div> : null}
                                        {selectedDraft.assessment.trustModel ? <div className="text-xs text-muted-foreground">
                                                {tg(t, "eda0e2b9")} {formatRatio(selectedDraft.assessment.trustModel.effectiveScriptTrustedThreshold)} · review {formatRatio(selectedDraft.assessment.trustModel.effectiveScriptReviewThreshold)} · fallbackHeavy {formatRatio(selectedDraft.assessment.trustModel.effectiveScriptFallbackHeavyThreshold)}
                                            </div> : null}
                                        {selectedDraft.metadata?.templateGovernance ? <div className="pt-2 text-xs text-muted-foreground">
                                                {tg(t, "2538c1e7")}{selectedDraft.metadata.templateGovernance.stage || selectedDraft.metadata.templateGovernanceStage || "unknown"} {tg(t, "4123b632")} 
                  {selectedDraft.metadata.templateGovernance.recommendedDecision || selectedDraft.metadata.templateRecommendedDecision || "n/a"} {tg(t, "1b48a628")} 
                  {selectedDraft.metadata.templateGovernance.rolloutMode || selectedDraft.metadata.templateRolloutMode || "n/a"} {tg(t, "fc038e30")} 
                  {formatConfidence(selectedDraft.metadata.templateGovernance.confidence ?? selectedDraft.metadata.templateTrustConfidence)}
                                            </div> : null}
                                    </div> : null}
                                {(selectedDraft.steps || []).slice(0, 4).map(step => <div key={`${selectedDraft.id}:${step.stepId || step.use}`} className="py-1">
                                        {step.stepId || step.use} · {step.use}
                                        {step.assessment?.status ? ` · ${step.assessment.status}` : ""}
                                        {step.assessment?.score != null ? ` · ${formatConfidence(step.assessment.score)}` : ""}
                                    </div>)}
                                {selectedDraft.source?.traceRunIds?.length ? <div className="pt-2 text-muted-foreground">
                                        {tg(t, "30d5c40f")}{selectedDraft.source.traceRunIds.length} {t("app.admin.dashboard.memory.page.kbcc46b75")}
                                    </div> : selectedDraft.source?.traceRunId ? <div className="pt-2 text-muted-foreground">
                                        {tg(t, "30d5c40f")}{selectedDraft.source.traceRunId}
                                    </div> : null}
                                {(selectedDraft.metadata?.compileIssues || []).slice(0, 2).map((issue, index) => <div key={`${selectedDraft.id}:issue:${index}`} className="pt-1 text-destructive">
                                        {issue}
                                    </div>)}
                            </div> : null}
                        {selectedDraft ? <div className="rounded-xl border border-border/60 bg-background p-3">
                                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                                    <div>
                                        <div className="text-sm font-medium">{t("components.rpa.RPAWorkbench.draftStepEditor")}</div>
                                        <div className="text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.draftStepEditorDescription")}</div>
                                    </div>
                                    <div className="flex flex-wrap items-center gap-2">
                                        <div className="inline-flex rounded-lg border border-border/60 bg-muted/20 p-1">
                                            <Button type="button" size="sm" variant={draftEditorView === "steps" ? "secondary" : "ghost"} onClick={() => setDraftEditorView("steps")}>
                                                {t("components.rpa.RPAWorkbench.editorViewSteps")}
                                            </Button>
                                            <Button type="button" size="sm" variant={draftEditorView === "canvas" ? "secondary" : "ghost"} onClick={() => setDraftEditorView("canvas")}>
                                                <GitBranch className="mr-2 h-4 w-4" />
                                                {t("components.rpa.RPAWorkbench.editorViewCanvas")}
                                            </Button>
                                        </div>
                                        <Button size="sm" onClick={() => void handlePatchDraftSteps()} disabled={busyAction === `draft:patch:${selectedDraft.id}`}>
                                            <Save className="mr-2 h-4 w-4" />
                                            {t("components.rpa.RPAWorkbench.saveDraftSteps")}
                                        </Button>
                                    </div>
                                </div>
                                <div className="grid gap-4 xl:grid-cols-[13rem_1fr_1.15fr]">
                                    <div className="rounded-xl border border-border/50 bg-muted/20 p-3">
                                        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("components.rpa.RPAWorkbench.actionLibrary")}</div>
                                        <div className="grid gap-2">
                                            {draftActionOptions.map(([action, label]) => <Button key={action} type="button" variant="outline" size="sm" className="justify-start" draggable onDragStart={event => {
                                                    event.dataTransfer.setData("application/x-v8-rpa-action", action);
                                                    event.dataTransfer.effectAllowed = "copy";
                                                }} onClick={() => handleAddDraftStep(action)}>
                                                    <Plus className="mr-2 h-4 w-4" />
                                                    {label}
                                                </Button>)}
                                        </div>
                                    </div>
                                    <div className="rounded-xl border border-border/50 p-3">
                                        {draftEditorView === "steps" ? <>
                                                <div className="mb-2 flex items-center justify-between gap-2">
                                                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("components.rpa.RPAWorkbench.stepList")}</div>
                                                    <Badge variant="outline">{orderedDraftSteps.length}</Badge>
                                                </div>
                                                <ScrollArea className="h-[430px] pr-3">
                                                    <div className="space-y-2">
                                                        {orderedDraftSteps.map((item, index) => <div key={item.key} role="button" tabIndex={0} onClick={() => {
                                                                setSelectedDraftStepKey(item.key);
                                                                setStepValidation(null);
                                                            }} onKeyDown={event => {
                                                                if (event.key !== "Enter" && event.key !== " ") return;
                                                                event.preventDefault();
                                                                setSelectedDraftStepKey(item.key);
                                                                setStepValidation(null);
                                                            }} className={`w-full cursor-pointer rounded-xl border p-3 text-left transition focus:outline-none focus:ring-2 focus:ring-primary/30 ${selectedDraftStepKey === item.key ? "border-primary bg-primary/5" : "border-border/60 hover:border-primary/40 hover:bg-muted/30"}`}>
                                                                <div className="flex items-start justify-between gap-2">
                                                                    <div className="min-w-0">
                                                                        <div className="truncate text-sm font-medium">{index + 1}. {stepIntentLabel(item.step)}</div>
                                                                        <div className="mt-1 truncate text-xs text-muted-foreground">{stepActionName(item.step)}{stepSelectorValue(item.step) ? ` · ${stepSelectorValue(item.step)}` : ""}</div>
                                                                    </div>
                                                                    <div className="flex shrink-0 items-center gap-1">
                                                                        <Button type="button" variant="ghost" size="sm" onClick={event => {
                                                                            event.stopPropagation();
                                                                            handleMoveDraftStep(item.key, -1);
                                                                        }} disabled={index === 0}>
                                                                            <ArrowUp className="h-4 w-4" />
                                                                        </Button>
                                                                        <Button type="button" variant="ghost" size="sm" onClick={event => {
                                                                            event.stopPropagation();
                                                                            handleMoveDraftStep(item.key, 1);
                                                                        }} disabled={index === orderedDraftSteps.length - 1}>
                                                                            <ArrowDown className="h-4 w-4" />
                                                                        </Button>
                                                                    </div>
                                                                </div>
                                                            </div>)}
                                                        {orderedDraftSteps.length === 0 ? <div className="rounded-xl border border-dashed p-4 text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.noDraftSteps")}</div> : null}
                                                    </div>
                                                </ScrollArea>
                                            </> : <>
                                                <div className="mb-2 flex items-center justify-between gap-2">
                                                    <div>
                                                        <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("components.rpa.RPAWorkbench.canvasFlowView")}</div>
                                                        <div className="text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.canvasFlowDescription")}</div>
                                                    </div>
                                                    <Badge variant="outline">{orderedDraftSteps.length}</Badge>
                                                </div>
                                                <div className="relative h-[430px] overflow-auto rounded-xl border border-dashed border-border/70 bg-muted/10 p-4" onDragOver={event => event.preventDefault()} onDrop={event => handleDraftCanvasDrop(event)}>
                                                    {orderedDraftSteps.length > 1 ? <svg className="pointer-events-none absolute left-1/2 top-16 h-[calc(100%-8rem)] w-1 -translate-x-1/2 text-primary/30" aria-hidden="true">
                                                            <line x1="2" y1="0" x2="2" y2="100%" stroke="currentColor" strokeWidth="2" strokeDasharray="6 8" />
                                                        </svg> : null}
                                                    <div className="relative z-10 mx-auto flex max-w-xl flex-col gap-4">
                                                        {orderedDraftSteps.map((item, index) => {
                                                            const badges = draftStepBadges(item.step);
                                                            return <div key={item.key} draggable onDragStart={event => {
                                                                event.dataTransfer.setData("application/x-v8-rpa-step", item.key);
                                                                event.dataTransfer.effectAllowed = "move";
                                                            }} onDragOver={event => event.preventDefault()} onDrop={event => handleDraftCanvasDrop(event, item.key)} className="relative">
                                                                    <button type="button" onClick={() => {
                                                                        setSelectedDraftStepKey(item.key);
                                                                        setStepValidation(null);
                                                                    }} className={`w-full rounded-2xl border bg-background/95 p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md ${selectedDraftStepKey === item.key ? "border-primary ring-2 ring-primary/15" : "border-border/70"}`}>
                                                                        <div className="flex items-start justify-between gap-3">
                                                                            <div className="flex min-w-0 gap-3">
                                                                                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{index + 1}</div>
                                                                                <div className="min-w-0">
                                                                                    <div className="truncate text-sm font-semibold">{stepIntentLabel(item.step)}</div>
                                                                                    <div className="mt-1 truncate text-xs text-muted-foreground">{stepActionName(item.step)}{stepSelectorValue(item.step) ? ` · ${stepSelectorValue(item.step)}` : ""}</div>
                                                                                </div>
                                                                            </div>
                                                                            <GitBranch className="h-4 w-4 shrink-0 text-muted-foreground" />
                                                                        </div>
                                                                        {badges.length ? <div className="mt-3 flex flex-wrap gap-1">
                                                                                {badges.map(badge => <span key={`${item.key}:${badge.key}`} className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${badge.tone}`}>{badge.label}</span>)}
                                                                            </div> : null}
                                                                    </button>
                                                                </div>;
                                                        })}
                                                        {orderedDraftSteps.length === 0 ? <div className="flex h-[360px] items-center justify-center rounded-xl border border-dashed bg-background/70 text-center text-xs text-muted-foreground">{t("components.rpa.RPAWorkbench.dropActionHere")}</div> : null}
                                                    </div>
                                                </div>
                                            </>}
                                    </div>
                                    <div className="rounded-xl border border-border/50 p-3">
                                        {selectedBuilderStep ? <div className="space-y-3">
                                                <div className="flex flex-wrap items-center justify-between gap-2">
                                                    <div>
                                                        <div className="text-sm font-medium">{t("components.rpa.RPAWorkbench.stepProperties")}</div>
                                                        <div className="text-xs text-muted-foreground">{selectedBuilderStep.stepId || selectedDraftStepKey}</div>
                                                    </div>
                                                    <div className="flex flex-wrap gap-1">
                                                        <Button type="button" variant="ghost" size="sm" onClick={() => handleDuplicateDraftStep(selectedDraftStepKey)}>
                                                            <Copy className="mr-1 h-4 w-4" />
                                                            {t("components.rpa.RPAWorkbench.duplicateStep")}
                                                        </Button>
                                                        <Button type="button" variant="ghost" size="sm" onClick={() => handleDeleteDraftStep(selectedDraftStepKey)}>
                                                            <Trash2 className="mr-1 h-4 w-4" />
                                                            {t("components.rpa.RPAWorkbench.deleteNoiseStep")}
                                                        </Button>
                                                    </div>
                                                </div>
                                                <div className="grid gap-3 md:grid-cols-2">
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.actionType")}</Label>
                                                        <select className="h-10 rounded-md border border-input bg-background px-3 text-sm" value={stepActionName(selectedBuilderStep)} onChange={event => updateSelectedDraftStep(step => {
                                                            const nextStep = {
                                                                ...step,
                                                                action: event.target.value
                                                            };
                                                            delete nextStep.use;
                                                            return nextStep;
                                                        })}>
                                                            <option value="open_app">{t("components.rpa.RPAWorkbench.actionOpenApp")}</option>
                                                            <option value="wait">{t("components.rpa.RPAWorkbench.actionWait")}</option>
                                                            <option value="click">{t("components.rpa.RPAWorkbench.actionClick")}</option>
                                                            <option value="type_text">{t("components.rpa.RPAWorkbench.actionTypeText")}</option>
                                                            <option value="hotkey">{t("components.rpa.RPAWorkbench.actionHotkey")}</option>
                                                            <option value="scroll">{t("components.rpa.RPAWorkbench.actionScroll")}</option>
                                                            <option value="screenshot">{t("components.rpa.RPAWorkbench.actionScreenshot")}</option>
                                                        </select>
                                                    </div>
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.stepIntent")}</Label>
                                                        <Input value={selectedBuilderStep.intent || ""} onChange={event => updateSelectedDraftStep(step => ({ ...step, intent: event.target.value }))} />
                                                    </div>
                                                </div>
                                                <div className="grid gap-3 md:grid-cols-[8rem_1fr]">
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.selectorKind")}</Label>
                                                        <select className="h-10 rounded-md border border-input bg-background px-3 text-sm" value={selectedBuilderSelector.xpath ? "xpath" : selectedBuilderSelector.role ? "role" : "css"} onChange={event => updateSelectedStepSelector(event.target.value, stepSelectorValue(selectedBuilderStep))}>
                                                            <option value="css">CSS</option>
                                                            <option value="xpath">XPath</option>
                                                            <option value="role">Role</option>
                                                        </select>
                                                    </div>
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.selectorOrAnchor")}</Label>
                                                        <Input value={stepSelectorValue(selectedBuilderStep)} onChange={event => updateSelectedStepSelector(selectedBuilderSelector.xpath ? "xpath" : selectedBuilderSelector.role ? "role" : "css", event.target.value)} placeholder="button[aria-label='Submit'] / //button[text()='Submit']" />
                                                    </div>
                                                </div>
                                                <div className="grid gap-3 md:grid-cols-3">
                                                    <div className="grid gap-2">
                                                        <Label>X</Label>
                                                        <Input value={selectedBuilderCoordinate.x == null ? "" : String(selectedBuilderCoordinate.x)} onChange={event => updateSelectedStepCoordinate("x", event.target.value)} />
                                                    </div>
                                                    <div className="grid gap-2">
                                                        <Label>Y</Label>
                                                        <Input value={selectedBuilderCoordinate.y == null ? "" : String(selectedBuilderCoordinate.y)} onChange={event => updateSelectedStepCoordinate("y", event.target.value)} />
                                                    </div>
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.waitMs")}</Label>
                                                        <Input value={selectedBuilderParams.timeoutMs == null ? "" : String(selectedBuilderParams.timeoutMs)} onChange={event => updateSelectedStepParam("timeoutMs", Number(event.target.value || 0))} />
                                                    </div>
                                                </div>
                                                <div className="grid gap-2">
                                                    <div className="flex items-center justify-between gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.inputTextOrVariable")}</Label>
                                                        <select className="h-8 rounded-md border border-input bg-background px-2 text-xs" value="" onChange={event => {
                                                            handleInsertVariableIntoSelectedStep(event.target.value);
                                                            event.currentTarget.value = "";
                                                        }}>
                                                            <option value="">{t("components.rpa.RPAWorkbench.insertVariable")}</option>
                                                            {draftVariableRows.filter(row => row.name.trim()).map(row => <option key={row.id} value={row.name.trim()}>{row.name.trim()}</option>)}
                                                        </select>
                                                    </div>
                                                    <Textarea className="min-h-[80px]" value={stepTextValue(selectedBuilderStep)} onChange={event => updateSelectedStepParam("text", event.target.value)} placeholder="{{customer_name}} / ${secret:github_token}" />
                                                </div>
                                                <div className="grid gap-3 md:grid-cols-2">
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.assertionType")}</Label>
                                                        <select className="h-10 rounded-md border border-input bg-background px-3 text-sm" value={firstString(selectedBuilderVerification.type)} onChange={event => updateSelectedStepVerification("type", event.target.value)}>
                                                            <option value="">{t("components.rpa.RPAWorkbench.noAssertion")}</option>
                                                            <option value="text_exists">{t("components.rpa.RPAWorkbench.assertTextExists")}</option>
                                                            <option value="element_visible">{t("components.rpa.RPAWorkbench.assertElementVisible")}</option>
                                                            <option value="url_matches">{t("components.rpa.RPAWorkbench.assertUrlMatches")}</option>
                                                            <option value="window_exists">{t("components.rpa.RPAWorkbench.assertWindowExists")}</option>
                                                        </select>
                                                    </div>
                                                    <div className="grid gap-2">
                                                        <Label>{t("components.rpa.RPAWorkbench.assertionExpected")}</Label>
                                                        <Input value={firstString(selectedBuilderVerification.expected, selectedBuilderVerification.text)} onChange={event => updateSelectedStepVerification("expected", event.target.value)} />
                                                    </div>
                                                </div>
                                                <div className="flex flex-wrap gap-2">
                                                    <Button type="button" variant="outline" onClick={() => void handleValidateSelectedStep("dry_run")} disabled={busyAction === `draft:validate:${selectedDraft.id}:dry_run`}>
                                                        <MousePointerClick className="mr-2 h-4 w-4" />
                                                        {t("components.rpa.RPAWorkbench.stepDryRun")}
                                                    </Button>
                                                    <Button type="button" variant="outline" onClick={() => void handleValidateSelectedStep("selector")} disabled={busyAction === `draft:validate:${selectedDraft.id}:selector`}>
                                                        {t("components.rpa.RPAWorkbench.validateSelector")}
                                                    </Button>
                                                    <Button type="button" variant="outline" onClick={() => void handleValidateSelectedStep("assertion")} disabled={busyAction === `draft:validate:${selectedDraft.id}:assertion`}>
                                                        {t("components.rpa.RPAWorkbench.validateAssertion")}
                                                    </Button>
                                                </div>
                                                {stepValidation ? <div className={`rounded-xl border p-3 text-xs ${stepValidation.ok ? "border-emerald-500/30 bg-emerald-500/10" : "border-destructive/30 bg-destructive/10"}`}>
                                                        <div className="font-medium">{stepValidation.summary || (stepValidation.ok ? t("components.rpa.RPAWorkbench.validationPassed") : t("components.rpa.RPAWorkbench.validationFailed"))}</div>
                                                        <div className="mt-2 space-y-1">
                                                            {(stepValidation.checks || []).map((check, index) => <div key={`${check.name || "check"}:${index}`} className={check.ok ? "text-emerald-700 dark:text-emerald-300" : "text-destructive"}>
                                                                    {check.ok ? "✓" : "!"} {check.message || check.name}
                                                                </div>)}
                                                            {(stepValidation.warnings || []).map((warning, index) => <div key={`warning:${index}`} className="text-amber-700 dark:text-amber-300">! {warning}</div>)}
                                                        </div>
                                                    </div> : null}
                                            </div> : <div className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">{t("components.rpa.RPAWorkbench.selectStepToEdit")}</div>}
                                    </div>
                                </div>
                            </div> : null}
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 xl:grid-cols-[1.05fr_1fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">{tg(t, "a498e2b7")}</CardTitle>
                        <CardDescription>{tg(t, "7b5192bb")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-2">
                            <Label htmlFor="existing-robot">{tg(t, "d4f3228c")}</Label>
                            <Input id="existing-robot" value={existingRobotFile} onChange={event => setExistingRobotFile(event.target.value)} placeholder={t("components.rpa.RPAWorkbench.k0f5296b5")} />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="existing-vars">{tg(t, "c0e67c85")}</Label>
                            <Textarea id="existing-vars" className="min-h-[160px] font-mono text-xs" value={existingVariablesText} onChange={event => setExistingVariablesText(event.target.value)} />
                        </div>
                        <div className="flex flex-wrap gap-2">
                            <Button variant="outline" onClick={() => void handleExistingAction("prepare-existing")} disabled={busyAction === "prepare-existing"}>
                                {tg(t, "8d37c8f0")}
                            </Button>
                            <Button onClick={() => void handleExistingAction("run-existing")} disabled={busyAction === "run-existing"}>
                                <Play className="mr-2 h-4 w-4" />
                                {tg(t, "52818c1e")}
                            </Button>
                        </div>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">{tg(t, "9c772e23")}</CardTitle>
                        <CardDescription>{tg(t, "f4fdb48f")}</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <ScrollArea className="h-[340px] pr-4">
                            <div className="space-y-3">
                                {scripts.length === 0 ? <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{tg(t, "aa0ca7a9")}</div> : scripts.map(script => <div key={script.path} className="rounded-2xl border border-border/60 p-4">
                                            <div className="text-sm font-medium">{script.name}</div>
                                            <div className="mt-1 break-all text-xs text-muted-foreground">{script.path}</div>
                                            <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
                                                {script.updatedAt ? <span>{script.updatedAt}</span> : null}
                                                {typeof script.size === "number" ? <span>· {script.size} bytes</span> : null}
                                            </div>
                                        </div>)}
                            </div>
                        </ScrollArea>
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 xl:grid-cols-[1.05fr_1fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                                <CardTitle className="text-lg">{tg(t, "34369e72")}</CardTitle>
                                <CardDescription>{tg(t, "d420bf42")}</CardDescription>
                            </div>
                            <Button variant={showArchivedTemplates ? "secondary" : "outline"} size="sm" onClick={() => setShowArchivedTemplates(value => !value)}>
                                {t("components.rpa.RPAWorkbench.includeArchived")}
                            </Button>
                        </div>
                    </CardHeader>
                    <CardContent>
                        <div className="mb-4 flex flex-wrap gap-2 text-xs text-muted-foreground">
                            <Badge variant="outline">{tg(t, "06d0f38d")} {templateSummary.total ?? 0}</Badge>
                            <Badge variant="secondary">{tg(t, "e585b2a8")} {templateSummary.templatePreferredCount ?? 0}</Badge>
                            <Badge variant="secondary">{tg(t, "f79637ac")} {templateSummary.computerUseFirstCount ?? 0}</Badge>
                            <Badge variant={templateSummary.atRiskCount ? "destructive" : "outline"}>{tg(t, "f79fbe32")} {templateSummary.atRiskCount ?? 0}</Badge>
                            <Badge variant="outline">{tg(t, "4607babd")} {templateSummary.reviewRequiredCount ?? 0}</Badge>
                        </div>
                        <ScrollArea className="h-[420px] pr-4">
                            <div className="space-y-3">
                                {templates.length === 0 ? <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{tg(t, "41f86afb")}</div> : templates.map(template => {
                const selected = template.id === selectedTemplateId;
                const archived = isArchivedTemplate(template);
                return <button key={template.id} type="button" onClick={() => setSelectedTemplateId(template.id)} className={`w-full rounded-2xl border p-4 text-left transition-colors ${selected ? "border-primary bg-primary/5" : "border-border/60 hover:border-primary/40 hover:bg-muted/30"}`}>

                                                <div className="flex flex-wrap items-start justify-between gap-3">
                                                    <div>
                                                        <div className="text-sm font-medium">{template.name || template.id}</div>
                                                        <div className="mt-1 text-xs text-muted-foreground">{template.id}</div>
                                                    </div>
                                                    <div className="flex flex-wrap gap-2">
                                                        <Badge variant="outline">{template.appId || "desktop"}</Badge>
                                                        <Badge variant="secondary">{template.view?.statusLabel || template.status || "unknown"}</Badge>
                                                        <Badge variant={template.view?.executionPath === "computer_use_first" ? "destructive" : "outline"}>
                                                            {template.view?.executionPathLabel || template.view?.rolloutModeLabel || t("components.plugin.host.PluginHostWorkbench.k54745147")}
                                                        </Badge>
                                                        {archived ? <Badge variant="outline">{t("components.rpa.RPAWorkbench.archived")}</Badge> : null}
                                                    </div>
                                                </div>
                                                <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
                                                    <span>{template.view?.stageLabel || template.governance?.stage || tg(t, "3b8aa569")}</span>
                                                    <span>·</span>
                                                    <span>{template.view?.recommendedDecisionLabel || template.governance?.recommendedDecision || tg(t, "9191e379")}</span>
                                                    <span>·</span>
                                                    <span>{tg(t, "5d540fae")} {template.view?.confidenceLabel || formatConfidence(template.governance?.confidence)}</span>
                                                    <span>·</span>
                                                    <span>rev {template.metadata?.revision ?? 0}</span>
                                                </div>
                                                {template.governance?.reasons?.length ? <div className="mt-2 text-xs text-muted-foreground">{template.governance.reasons[0]}</div> : null}
                                                <div className="mt-2 flex flex-wrap gap-1.5">
                                                    {(template.view?.riskFlagLabels || []).slice(0, 4).map(flag => <Badge key={`${template.id}:${flag}`} variant="outline">{flag}</Badge>)}
                                                </div>
                                            </button>;
              })}
                            </div>
                        </ScrollArea>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">{tg(t, "00681781")}</CardTitle>
                        <CardDescription>{selectedTemplate ? `${selectedTemplate.name || selectedTemplate.id}` : tg(t, "db138e40")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {selectedTemplate ? <>
                                <div className="rounded-xl border border-border/60 bg-muted/20 p-3 text-xs text-muted-foreground">
                                    <div className="flex flex-wrap gap-2 pb-2">
                                        <Badge variant="secondary">{selectedTemplate.view?.statusLabel || selectedTemplate.status || "unknown"}</Badge>
                                        <Badge variant="outline">{selectedTemplate.view?.stageLabel || selectedTemplate.governance?.stage || "unknown"}</Badge>
                                        <Badge variant={selectedTemplate.view?.executionPath === "computer_use_first" ? "destructive" : "outline"}>
                                            {selectedTemplate.view?.executionPathLabel || selectedTemplate.view?.rolloutModeLabel || t("components.plugin.host.PluginHostWorkbench.k54745147")}
                                        </Badge>
                                    </div>
                                    <div>{tg(t, "3e403dc5")}<span className="text-foreground">{selectedTemplate.view?.recommendedDecisionLabel || selectedTemplate.governance?.recommendedDecision || tg(t, "9191e379")}</span></div>
                                    <div>{tg(t, "35124d6d")}<span className="text-foreground">{selectedTemplate.view?.confidenceLabel || formatConfidence(selectedTemplate.governance?.confidence)}</span></div>
                                    <div>{tg(t, "dd515e1a")}<span className="text-foreground">{selectedTemplate.source?.draftId || "n/a"}</span></div>
                                    <div className="pt-2">{tg(t, "e97b8331")}<span className="text-foreground">
                                        {selectedTemplate.view?.reviewSummary?.total ?? 0} {tg(t, "f7576770")} {selectedTemplate.view?.reviewSummary?.approveCount ?? 0} {tg(t, "f5355174")} {selectedTemplate.view?.reviewSummary?.freezeCount ?? 0} {tg(t, "d6409da0")} {selectedTemplate.view?.reviewSummary?.rollbackCount ?? 0}
                                    </span></div>
                                    {selectedTemplate.view?.reviewSummary?.lastReviewedAt ? <div>{tg(t, "6ff3be27")}<span className="text-foreground">{formatWhen(selectedTemplate.view.reviewSummary.lastReviewedAt)}</span>{selectedTemplate.view.reviewSummary.lastReviewer ? ` · ${selectedTemplate.view.reviewSummary.lastReviewer}` : ""}</div> : null}
                                    {selectedTemplate.governance?.reasons?.length ? <div className="pt-2 space-y-1">
                                            {(selectedTemplate.governance.reasons || []).slice(0, 4).map((reason, index) => <div key={`${selectedTemplate.id}:reason:${index}`}>- {reason}</div>)}
                                        </div> : null}
                                    {(selectedTemplate.view?.riskFlagLabels || []).length ? <div className="pt-2 flex flex-wrap gap-1.5">
                                            {(selectedTemplate.view?.riskFlagLabels || []).map(flag => <Badge key={`${selectedTemplate.id}:risk:${flag}`} variant="outline">{flag}</Badge>)}
                                        </div> : null}
                                </div>
                                <div className="grid gap-2">
                                    <Label htmlFor="template-note">{tg(t, "e0361480")}</Label>
                                    <Textarea id="template-note" className="min-h-[96px]" value={templateNote} onChange={event => setTemplateNote(event.target.value)} placeholder={t("components.rpa.RPAWorkbench.k3d5ec5ad")} />

                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <Button variant="outline" onClick={() => void handleTemplateAction("review_required")} disabled={busyAction === `template:review_required:${selectedTemplate.id}`}>
                                        {tg(t, "11ab7032")}
                                    </Button>
                                    <Button variant="outline" onClick={() => void handleTemplateAction("freeze")} disabled={busyAction === `template:freeze:${selectedTemplate.id}`}>
                                        {tg(t, "c5d4e94b")}
                                    </Button>
                                    <Button onClick={() => void handleTemplateAction("approve")} disabled={busyAction === `template:approve:${selectedTemplate.id}`}>
                                        {tg(t, "10a18390")}
                                    </Button>
                                    {isArchivedTemplate(selectedTemplate) ? <Button variant="outline" onClick={() => void handleTemplateGovernanceAction("restore")} disabled={busyAction === `template:restore:${selectedTemplate.id}`}>
                                            {t("components.rpa.RPAWorkbench.restore")}
                                        </Button> : <Button variant="outline" onClick={() => void handleTemplateGovernanceAction("archive")} disabled={busyAction === `template:archive:${selectedTemplate.id}`}>
                                            {t("components.rpa.RPAWorkbench.archive")}
                                        </Button>}
                                    <Button variant="destructive" onClick={() => void handleTemplateGovernanceAction("delete")} disabled={busyAction === `template:delete:${selectedTemplate.id}`}>
                                        <Trash2 className="mr-2 h-4 w-4" />
                                        {t("components.rpa.RPAWorkbench.hardDelete")}
                                    </Button>
                                </div>
                                <div className="space-y-3">
                                    <div className="text-sm font-medium">{tg(t, "7f928d3f")}</div>
                                    <ScrollArea className="h-[220px] pr-4">
                                        <div className="space-y-2">
                                            {templateHistory.length === 0 ? <div className="rounded-xl border border-dashed p-4 text-xs text-muted-foreground">{tg(t, "2901f955")}</div> : templateHistory.map(item => <div key={item.path || `${selectedTemplate.id}:${item.revision}`} className="rounded-xl border border-border/60 p-3 text-xs text-muted-foreground">
                                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                                            <div className="flex flex-wrap gap-2">
                                                                <Badge variant="outline">rev {item.historyView?.revision ?? item.revision ?? 0}</Badge>
                                                                <Badge variant="secondary">{item.historyView?.statusLabel || item.template?.view?.statusLabel || "unknown"}</Badge>
                                                            </div>
                                                            <Button variant="ghost" size="sm" onClick={() => void handleTemplateRollback(item.revision, item.path)} disabled={busyAction === `template:rollback:${selectedTemplate.id}:${item.revision || item.path || "latest"}`}>
                                                                {tg(t, "5fad9bec")}
                                                            
                        </Button>
                                                        </div>
                                                        <div className="mt-2">{tg(t, "0f93c2bb")}<span className="text-foreground">{item.historyView?.reason || item.reason || "snapshot"}</span></div>
                                                        <div>{tg(t, "8c56cb7b")}<span className="text-foreground">{item.historyView?.actor || item.actor || "system"}</span></div>
                                                        <div>{tg(t, "32d77333")}<span className="text-foreground">{formatWhen(item.historyView?.at || item.at)}</span></div>
                                                        {item.template?.view?.executionPathLabel ? <div>{tg(t, "d72a54ed")}<span className="text-foreground">{item.template.view.executionPathLabel}</span></div> : null}
                                                    </div>)}
                                        </div>
                                    </ScrollArea>
                                </div>
                            </> : <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{tg(t, "49b46706")}</div>}
                    </CardContent>
                </Card>
            </div>

            <Card className="border-border/60">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-lg">
                        {latestResult && typeof (latestResult as {
            status?: string;
          }).status === "string" && ["completed", "ready", "completed_with_fallback"].includes((latestResult as {
            status?: string;
          }).status || "") ? <CheckCircle2 className="h-5 w-5 text-emerald-500" /> : latestResult ? <ShieldAlert className="h-5 w-5 text-amber-500" /> : <AlertCircle className="h-5 w-5 text-muted-foreground" />}
                        {tg(t, "40852947")}
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <pre className="max-h-[420px] overflow-auto rounded-xl bg-muted/30 p-4 text-xs leading-6">
                        {latestResult ? prettyJson(latestResult) : tg(t, "e14a0bde")}
                    </pre>
                </CardContent>
            </Card>

            <div className="grid gap-6 xl:grid-cols-2">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">{tg(t, "ae650bf3")}</CardTitle>
                        <CardDescription>{tg(t, "fa0e6e54")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {rpaApprovals.length === 0 ? <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{tg(t, "c3194cc7")}</div> : rpaApprovals.map(approval => {
            const busyApprove = busyAction === `approval:approve:${approval.id}`;
            const busyReject = busyAction === `approval:reject:${approval.id}`;
            const question = approval.request?.question || approval.request?.prompt || tg(t, "7f180ac0");
            return <div key={approval.id} className="rounded-2xl border border-border/60 p-4">
                                        <div className="flex flex-wrap gap-2">
                                            <Badge variant="outline">{approval.approval_kind || "approval"}</Badge>
                                            {approval.run_id ? <Badge variant="secondary">Run {approval.run_id}</Badge> : null}
                                            {approval.session_id ? <Badge variant="secondary">Session {approval.session_id}</Badge> : null}
                                        </div>
                                        <p className="mt-3 whitespace-pre-wrap text-sm leading-6">{question}</p>
                                        {approval.request?.rpa?.requiredApprovals?.length ? <div className="mt-3 flex flex-wrap gap-2">
                                                {approval.request.rpa.requiredApprovals.map((item, index) => <Badge key={`${approval.id}:${item.stepId || index}`} variant="outline">
                                                        {item.stepId || item.use || "step"} · {item.mode || "review"}
                                                        {item.confidence != null ? ` · ${formatConfidence(item.confidence)}` : ""}
                                                    </Badge>)}
                                            </div> : null}
                                        <div className="mt-2 text-xs text-muted-foreground">
                                            {t("components.runtime.PendingApprovalsPanel.k84eb0077")} {formatWhen(approval.created_at)}
                                        </div>
                                        <Textarea className="mt-3 min-h-[96px]" placeholder={t("components.rpa.RPAWorkbench.k5d7ae816")} value={approvalDrafts[approval.id] || ""} onChange={event => setApprovalDrafts(current => ({
                ...current,
                [approval.id]: event.target.value
              }))} />

                                        <div className="mt-3 flex justify-end gap-2">
                                            <Button variant="outline" onClick={() => void handleApproval(approval.id, false)} disabled={busyApprove || busyReject}>
                                                {t("components.runtime.PendingApprovalsPanel.kf069e51c")}
                                            </Button>
                                            <Button onClick={() => void handleApproval(approval.id, true)} disabled={busyApprove || busyReject}>
                                                {t("components.runtime.PendingApprovalsPanel.kfeb050f7")}
                                            </Button>
                                        </div>
                                    </div>;
          })}
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">{tg(t, "3df5bcab")}</CardTitle>
                        <CardDescription>{tg(t, "4300eeee")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {rpaRuns.length === 0 ? <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{tg(t, "8262eaa3")}</div> : rpaRuns.map(run => {
            const interruptBusy = busyAction === `run:interrupt:${run.id}`;
            const retryBusy = busyAction === `run:retry:${run.id}`;
            const status = run.status || "queued";
            const scriptName = readRunScriptName(run.metadata);
            const robotFile = readRunRobotFile(run.metadata);
            const approvalCount = readRunApprovalCount(run.metadata);
            const missingLibraries = readRunUnavailableLibraries(run.metadata);
            const assessment = readRunAssessment(run.metadata);
            const executionState = readRunExecutionState(run.metadata);
            const fallback = readRunFallback(run.metadata);
            const templatePolicy = readRunTemplatePolicy(run.metadata);
            return <div key={run.id} className="rounded-2xl border border-border/60 p-4">
                                        <div className="flex flex-wrap gap-2">
                                            <Badge>{t(RUN_LABELS[status] || status)}</Badge>
                                            {run.metadata?.mode ? <Badge variant="outline">{String(run.metadata.mode)}</Badge> : null}
                                            {run.trigger_source ? <Badge variant="secondary">{run.trigger_source}</Badge> : null}
                                            {approvalCount > 0 ? <Badge variant="destructive">{approvalCount} {tg(t, "ad766906")}</Badge> : null}
                                            {executionState ? <Badge variant="outline">{executionState}</Badge> : null}
                                            {templatePolicy?.executionPath ? <Badge variant="outline">route:{templatePolicy.executionPath}</Badge> : null}
                                            {fallback?.type ? <Badge variant="secondary">fallback:{fallback.type}</Badge> : null}
                                        </div>
                                        <div className="mt-3 space-y-1 text-sm text-muted-foreground">
                                            <div>Run ID: <span className="text-foreground">{run.id}</span></div>
                                            {run.session_id ? <div>Session: <span className="text-foreground">{run.session_id}</span></div> : null}
                                            {scriptName ? <div>{tg(t, "e4dce09c")} <span className="text-foreground">{scriptName}</span></div> : null}
                                            {robotFile ? <div>Robot: <span className="break-all text-foreground">{robotFile}</span></div> : null}
                                            {assessment ? <div>{tg(t, "144967bc")} <span className="text-foreground">{assessment.status || "unknown"}{assessment.band ? ` · ${assessment.band}` : ""} · {formatConfidence(assessment.score)}</span></div> : null}
                                            {assessment?.signals ? <div>acceptedRatio: <span className="text-foreground">{formatRatio(assessment.signals.acceptedRatio)}</span> · nativeRatio: <span className="text-foreground">{formatRatio(assessment.signals.nativeSemanticRatio)}</span> · recoveryHeavy: <span className="text-foreground">{formatRatio(assessment.signals.recoveryHeavyRatio)}</span> · profileAugmented: <span className="text-foreground">{formatRatio(assessment.signals.profileAugmentedRatio)}</span></div> : null}
                                            {assessment ? <div>accepted/review/excluded: <span className="text-foreground">{assessment.acceptedSteps ?? 0}/{assessment.reviewRequiredSteps ?? 0}/{assessment.excludedSteps ?? 0}</span></div> : null}
                                            {assessment?.signals ? <div>{tg(t, "a1242e26")} <span className="text-foreground">{assessment.signals.historicalScriptRuns ?? 0}</span> {tg(t, "fcdfff9f")} <span className="text-foreground">{formatRatio(assessment.signals.historicalScriptCompletedRate)}</span> {tg(t, "451d6141")} <span className="text-foreground">{formatCalibrationSource(t, assessment.signals.historicalScriptCalibrationSource)}</span></div> : null}
                                            {templatePolicy ? <div>{tg(t, "52818247")} <span className="text-foreground">{templatePolicy.executionPath || "robot"}</span> {tg(t, "ce991a38")} <span className="text-foreground">{templatePolicy.stage || "unknown"}</span> {tg(t, "4123b632")} <span className="text-foreground">{templatePolicy.recommendedDecision || "n/a"}</span></div> : null}
                                            {fallback?.sourceTraceRunId ? <div>Fallback Trace: <span className="text-foreground">{fallback.sourceTraceRunId}</span></div> : null}
                                            {fallback?.sourceScriptId ? <div>Fallback Script: <span className="text-foreground">{fallback.sourceScriptId}</span></div> : null}
                                            <div>{tg(t, "4c2869e5")} <span className="text-foreground">{formatWhen(run.created_at)}</span></div>
                                        </div>
                                        {missingLibraries.length ? <div className="mt-3 flex flex-wrap gap-2">
                                                {missingLibraries.map(item => <Badge key={`${run.id}:${item}`} variant="outline">
                                                        {tg(t, "423b830d")} {item}
                                                    </Badge>)}
                                            </div> : null}
                                        <div className="mt-3 flex justify-end gap-2">
                                            {status === "running" ? <Button variant="outline" onClick={() => void handleRunCommand(run.id, "interrupt")} disabled={interruptBusy || retryBusy}>
                                                    {t("components.runtime.RecentRunsPanel.k31af3bc0")}
                                                </Button> : null}
                                            {["paused", "failed", "cancelled", "waiting_input"].includes(status) ? <Button variant="outline" onClick={() => void handleRunCommand(run.id, "retry")} disabled={interruptBusy || retryBusy}>
                                                    {t("components.runtime.RecentRunsPanel.kcd92b799")}
                                                </Button> : null}
                                        </div>
                                    </div>;
          })}
                    </CardContent>
                </Card>
            </div>
            </> : null}
        </div>;
}
