import fs from "node:fs";
import path from "node:path";

import { REPORTS_DIR } from "./paths";

/** Mirrors the JSON emitted by evaluation/harness.py into evaluation/reports/. */
export interface EvalSummary {
  total_runs: number;
  scored_runs: number;
  failed_runs: number;
  root_cause_accuracy: number;
  affected_service_accuracy: number;
  triggering_change_accuracy: number;
  total_unsupported_claims: number;
  avg_diagnosis_time_seconds: number;
}

export interface EvalDiagnosis {
  root_cause: string;
  affected_service: string;
  triggering_change: string | null;
  confidence: number;
  evidence_summary: string;
  hypotheses_considered: string[];
}

export interface EvalGroundTruth {
  root_cause: string;
  triggering_change: string | null;
  affected_service: string;
}

export interface EvalScore {
  affected_service_correct: boolean;
  triggering_change_correct: boolean;
  root_cause_correct: boolean;
  root_cause_rationale: string;
  unsupported_claims: string[];
}

export interface EvalRunResult {
  scenario_id: string;
  seed: number;
  started_at: string;
  run_id: string;
  scenario_run_succeeded: boolean;
  alert_name: string | null;
  alert_condition: string | null;
  diagnosis_time_seconds: number | null;
  diagnosis: EvalDiagnosis | null;
  ground_truth: EvalGroundTruth;
  score: EvalScore | null;
  error?: string;
}

export interface EvalReport {
  generated_at: string;
  summary: EvalSummary;
  runs: EvalRunResult[];
}

/** File name embeds a sortable UTC timestamp (eval-20260730T014311Z.json), so a
 * plain reverse-lexicographic sort gives newest-first without parsing dates. */
export function listReportFiles(): string[] {
  if (!fs.existsSync(REPORTS_DIR)) return [];
  return fs
    .readdirSync(REPORTS_DIR)
    .filter((name) => name.startsWith("eval-") && name.endsWith(".json"))
    .sort()
    .reverse();
}

export function loadReport(fileName: string): EvalReport {
  const full = path.join(REPORTS_DIR, fileName);
  return JSON.parse(fs.readFileSync(full, "utf-8")) as EvalReport;
}

export function getLatestReport(): { fileName: string; report: EvalReport } | null {
  const [latest] = listReportFiles();
  if (!latest) return null;
  return { fileName: latest, report: loadReport(latest) };
}

export function listAllReports(): { fileName: string; report: EvalReport }[] {
  return listReportFiles().map((fileName) => ({ fileName, report: loadReport(fileName) }));
}
