"use server";

import { revalidatePath } from "next/cache";

import { decideRemediation } from "@/lib/remediations";

/** Same UPDATE evaluation/approve.py's CLI issues -- this is just a second,
 * browser-based front end onto the same pending_approval queue. Nothing here
 * grants the console any privilege the CLI didn't already have: it can only
 * flip status on a row that mcp/remediation-server already policy-approved
 * and is polling execute_remediation against. */
export async function approveRemediation(id: string, decidedBy: string) {
  const row = await decideRemediation(id, "approved", decidedBy || "console");
  revalidatePath("/remediations");
  return row;
}

export async function denyRemediation(id: string, decidedBy: string) {
  const row = await decideRemediation(id, "denied_by_human", decidedBy || "console");
  revalidatePath("/remediations");
  return row;
}
