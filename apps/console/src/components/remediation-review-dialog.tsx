"use client";

import { useState, useTransition } from "react";
import { CheckCircle2, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { RemediationRow } from "@/lib/remediations";
import { approveRemediation, denyRemediation } from "@/app/remediations/actions";

export function RemediationReviewDialog({
  remediation,
}: {
  remediation: RemediationRow;
}) {
  const [open, setOpen] = useState(false);
  const [decidedBy, setDecidedBy] = useState("");
  const [pending, startTransition] = useTransition();

  function handle(action: "approve" | "deny") {
    startTransition(async () => {
      if (action === "approve") {
        await approveRemediation(remediation.id, decidedBy);
      } else {
        await denyRemediation(remediation.id, decidedBy);
      }
      setOpen(false);
    });
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">Review</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {remediation.tool} → {remediation.target}
          </DialogTitle>
          <DialogDescription>
            Class {remediation.class ?? "?"} · run {remediation.run_id ?? "unknown"}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <div className="text-muted-foreground mb-1 text-xs font-medium uppercase tracking-wide">
              Agent&apos;s justification
            </div>
            <p className="bg-muted rounded-md p-3 text-sm">
              {remediation.justification}
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="decided-by">Your name (recorded as decided_by)</Label>
            <Input
              id="decided-by"
              placeholder="e.g. schon"
              value={decidedBy}
              onChange={(e) => setDecidedBy(e.target.value)}
            />
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="destructive"
            disabled={pending}
            onClick={() => handle("deny")}
          >
            <XCircle /> Deny
          </Button>
          <Button
            variant="success"
            disabled={pending}
            onClick={() => handle("approve")}
          >
            <CheckCircle2 /> Approve
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
