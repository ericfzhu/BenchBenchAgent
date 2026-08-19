import React, { useState } from "react";
import { Button } from "./ui/button.jsx";
import { Badge } from "./ui/badge.jsx";
import { Progress } from "./ui/progress.jsx";
import { Input } from "./ui/input.jsx";
import {
  Dialog,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "./ui/dialog.jsx";

const PHASE_BADGE_VARIANTS = {
  created: "default",
  public_running: "success",
  audit_running: "warning",
  completed: "success",
  failed: "destructive",
};

export function TopHUD({
  epochs,
  activeEpochId,
  onSelectEpoch,
  onCreateEpoch,
  epochState,
  systemState,
  onTriggerAction,
  onOpenJobs,
  hasActiveJob,
  isEditMode,
  onToggleEditMode,
}) {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newEpochId, setNewEpochId] = useState("");

  const phase = epochState?.phase || systemState?.phase || "created";
  const budget = epochState?.usage?.budget_usd || systemState?.usage?.budget_usd || 500;
  const spend = epochState?.usage?.spent_usd || systemState?.usage?.spent_usd || 0;
  const inputTokens = epochState?.usage?.input_tokens || systemState?.usage?.input_tokens || 0;
  const outputTokens = epochState?.usage?.output_tokens || systemState?.usage?.output_tokens || 0;

  const spendPct = Math.min(100, Math.max(0, (spend / (budget || 1)) * 100));
  const progressVariant = spendPct > 85 ? "danger" : spendPct > 65 ? "warning" : "default";

  const handleCreateSubmit = (e) => {
    e.preventDefault();
    if (!newEpochId.trim()) return;
    onCreateEpoch(newEpochId.trim());
    setNewEpochId("");
    setShowCreateModal(false);
  };

  return (
    <header className="flex h-14 w-full items-center justify-between border-b border-border bg-card px-4 font-mono z-50 gap-4">
      {/* Left side: Brand, Epoch selector, Phase badge */}
      <div className="flex items-center gap-3">
        <a href="/" className="flex h-8 items-center text-xs font-bold tracking-wider text-foreground hover:text-primary transition-colors no-underline">
          OPERATOR
        </a>

        <div className="flex items-center gap-2">
          <select
            className="flex h-8 w-56 rounded-none border border-input bg-muted/40 px-3 text-xs font-mono text-foreground shadow-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring cursor-pointer"
            value={activeEpochId || ""}
            onChange={(e) => onSelectEpoch(e.target.value)}
          >
            {epochs && epochs.length > 0 ? (
              epochs.map((ep) => (
                <option key={ep.epoch_id} value={ep.epoch_id}>
                  {ep.epoch_id} ({ep.phase || "ready"})
                </option>
              ))
            ) : (
              <option value="">No epochs registered</option>
            )}
          </select>

          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowCreateModal(true)}
            className="h-8"
          >
            + New Epoch
          </Button>

          <Badge variant={PHASE_BADGE_VARIANTS[phase] || "default"} className="h-8 px-3 text-xs flex items-center">
            ● {phase.replace("_", " ")}
          </Badge>
        </div>
      </div>

      {/* Center: Budget Meter and Tokens */}
      <div className="flex items-center">
        <div className="flex h-8 items-center gap-3 rounded-none border border-border bg-muted/30 px-3 shadow-xs">
          <div className="flex items-center gap-1.5 min-w-[110px]">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              SPEND:
            </span>
            <span className="text-xs font-bold text-foreground">
              ${spend.toFixed(2)} / ${budget.toFixed(2)}
            </span>
          </div>

          <div className="w-20 flex items-center">
            <Progress value={spendPct} variant={progressVariant} />
          </div>

          <div className="flex items-center gap-1.5 pl-3 border-l border-border/60 min-w-[110px]">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              TOKENS:
            </span>
            <span className="text-xs text-muted-foreground font-medium">
              {(inputTokens / 1000).toFixed(1)}k / {(outputTokens / 1000).toFixed(1)}k
            </span>
          </div>
        </div>
      </div>

      {/* Right: Tournament Action Controls */}
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onTriggerAction("preflight")}
          title="Run preflight diagnostics and quota check"
          className="h-8"
        >
          ⚙ Preflight
        </Button>

        {phase === "public_running" || phase === "audit_running" ? (
          <Button
            variant="destructive"
            size="sm"
            onClick={() => onTriggerAction("pause")}
            title="Pause tournament execution"
            className="h-8"
          >
            ⏸ Pause
          </Button>
        ) : (
          <Button
            variant="default"
            size="sm"
            onClick={() => onTriggerAction("run")}
            title="Run or resume autonomous tournament"
            className="h-8"
          >
            ▶ Run Tournament
          </Button>
        )}

        <Button
          variant="outline"
          size="sm"
          onClick={() => onTriggerAction("step")}
          title="Advance single trace step"
          className="h-8"
        >
          ⏭ Step
        </Button>

        <Button
          variant={isEditMode ? "default" : "outline"}
          size="sm"
          onClick={onToggleEditMode}
          title={isEditMode ? "Canvas is editable. Click to lock view mode." : "Canvas is locked. Click to enable editing."}
          className="h-8"
        >
          {isEditMode ? "🔓 Edit Canvas" : "🔒 Locked Canvas"}
        </Button>

        <Button
          variant={hasActiveJob ? "default" : "secondary"}
          size="sm"
          onClick={onOpenJobs}
          className="h-8"
        >
          {hasActiveJob && <span className="text-emerald-400">● </span>}
          Operations
        </Button>
      </div>

      {/* Create Epoch Modal */}
      <Dialog open={showCreateModal} onOpenChange={setShowCreateModal}>
        <form onSubmit={handleCreateSubmit}>
          <DialogHeader>
            <DialogTitle>Create New Benchmark Epoch</DialogTitle>
            <DialogDescription>
              Enter a unique identifier for the new benchmark epoch (e.g. <code className="text-primary">epoch-expense-forensics-v1</code>).
            </DialogDescription>
          </DialogHeader>

          <Input
            value={newEpochId}
            onChange={(e) => setNewEpochId(e.target.value)}
            placeholder="epoch-..."
            autoFocus
            className="my-4"
          />

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setShowCreateModal(false)}
            >
              Cancel
            </Button>
            <Button type="submit" variant="default">
              Create Epoch
            </Button>
          </DialogFooter>
        </form>
      </Dialog>
    </header>
  );
}
