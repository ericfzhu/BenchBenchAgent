import React, { useState } from "react";
import { Button } from "./ui/button.jsx";
import { Badge } from "./ui/badge.jsx";
import { Card, CardHeader, CardTitle, CardContent } from "./ui/card.jsx";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./ui/tabs.jsx";

export function InspectorDrawer({
  node,
  epochState,
  csrfToken,
  onClose,
  onTriggerAction,
}) {
  const [activeTab, setActiveTab] = useState("what_it_does");
  const [showRawJson, setShowRawJson] = useState(false);
  const [selectedCandidate, setSelectedCandidate] = useState(null);

  if (!node) return null;

  const candidates = epochState?.candidates || [];
  const usage = epochState?.usage || {};
  const manifest = epochState?.manifest || {};
  const observability = epochState?.observability || {};

  return (
    <div className="absolute top-0 right-0 w-[520px] h-full bg-card border-l border-border shadow-2xl flex flex-col font-mono z-40 animate-in slide-in-from-right duration-200">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-border bg-muted/20">
        <div className="flex flex-col min-w-0 pr-2">
          <span className="text-[10px] font-bold text-primary uppercase tracking-wider">
            {node.category?.replace("_", " ")}
          </span>
          <h2 className="text-sm font-bold text-foreground truncate">
            {node.label}
          </h2>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} className="h-8 w-8 text-muted-foreground hover:text-foreground">
          ✕
        </Button>
      </div>

      {/* 3-Tab Interface */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 overflow-hidden flex flex-col">
        <TabsList className="shrink-0">
          <TabsTrigger value="what_it_does">What It Does</TabsTrigger>
          <TabsTrigger value="live_state">Live State & Artifacts</TabsTrigger>
          <TabsTrigger value="actions">Operator Actions</TabsTrigger>
        </TabsList>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Tab 1: What It Does */}
          <TabsContent value="what_it_does" className="space-y-4 m-0">
            <div className="space-y-2">
              <div className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
                Architectural Summary
              </div>
              <div className="text-xs text-foreground leading-relaxed bg-muted/30 p-3.5 border border-border">
                {node.summary || node.description}
              </div>
            </div>

            {node.description && node.summary && (
              <div className="space-y-2">
                <div className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
                  Detailed Mechanism
                </div>
                <div className="text-xs text-muted-foreground leading-relaxed bg-muted/20 p-3.5 border border-border">
                  {node.description}
                </div>
              </div>
            )}

            {node.invariants && node.invariants.length > 0 && (
              <div className="space-y-2">
                <div className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
                  System Invariants & Boundaries
                </div>
                <div className="space-y-2">
                  {node.invariants.map((inv, idx) => (
                    <div
                      key={idx}
                      className="flex items-start gap-2.5 bg-muted/30 border border-border p-3 text-xs text-foreground"
                    >
                      <span className="text-emerald-500 font-bold shrink-0">✓</span>
                      <span>{inv}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </TabsContent>

          {/* Tab 2: Live State & Artifacts */}
          <TabsContent value="live_state" className="space-y-4 m-0">
            {node.id === "session_budget" && (
              <div className="space-y-2">
                <div className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
                  Live Token & Cost Budget
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <Card className="bg-muted/30">
                    <CardHeader className="p-3">
                      <span className="text-[9px] uppercase tracking-wider text-muted-foreground">
                        Spent / Ceiling
                      </span>
                      <CardTitle className="text-sm text-foreground font-bold">
                        ${(usage.spent_usd || 0).toFixed(2)} / ${(usage.budget_usd || 500).toFixed(2)}
                      </CardTitle>
                    </CardHeader>
                  </Card>

                  <Card className="bg-muted/30">
                    <CardHeader className="p-3">
                      <span className="text-[9px] uppercase tracking-wider text-muted-foreground">
                        Incremental Input
                      </span>
                      <CardTitle className="text-sm text-emerald-400 font-bold">
                        {((usage.input_tokens || 0) / 1000).toFixed(1)}k tokens
                      </CardTitle>
                    </CardHeader>
                  </Card>
                </div>
              </div>
            )}

            {(node.id === "promotion_registry" || node.id === "archive_vault") && (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
                  <span>Candidate Benchmarks</span>
                  <span className="text-muted-foreground lowercase font-normal">({candidates.length} total)</span>
                </div>
                {candidates.length === 0 ? (
                  <p className="text-xs text-muted-foreground p-3.5 bg-muted/20 border border-border">
                    No candidates registered yet. Run tournament or generation step to populate.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {candidates.map((cand) => (
                      <div
                        key={cand.snapshot_id}
                        className="flex items-center justify-between p-3 bg-muted/30 hover:bg-muted/60 border border-border cursor-pointer transition-colors"
                        onClick={() => setSelectedCandidate(selectedCandidate === cand.snapshot_id ? null : cand.snapshot_id)}
                      >
                        <div className="flex flex-col min-w-0 pr-2">
                          <div className="text-xs font-bold text-foreground truncate">
                            {cand.snapshot_id}
                          </div>
                          <div className="text-[10px] text-muted-foreground">
                            Creator: {cand.creator || "—"} | Solvers: {cand.solver_count || 0}
                          </div>
                        </div>
                        <Badge variant={cand.reviewed ? "success" : "default"}>
                          {cand.reviewed ? "Approved" : "Pending"}
                        </Badge>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {node.id === "matrix_scorer" && (
              <div className="space-y-3">
                <div className="flex items-center justify-between text-[10px] font-bold text-primary uppercase tracking-wider">
                  <span>Bradley-Terry Elo Ranking</span>
                  <Badge variant="outline" className="text-[9px]">9 Models</Badge>
                </div>
                <div className="rounded-none border border-border overflow-hidden bg-card">
                  <table className="w-full text-xs font-mono text-left border-collapse">
                    <thead className="bg-muted/60 text-muted-foreground uppercase text-[10px] border-b border-border">
                      <tr>
                        <th className="px-3 py-2">Cohort Model</th>
                        <th className="px-2 py-2 text-center">Acc</th>
                        <th className="px-2 py-2 text-center">Elo</th>
                        <th className="px-3 py-2 text-right">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {[
                        "gemini-3.6-flash",
                        "claude-opus-5",
                        "claude-sonnet-5",
                        "grok-4.3",
                        "claude-opus-4-8",
                        "gemini-3.1-pro",
                        "claude-opus-4-7",
                        "gemini-3.5-flash",
                        "gemini-3.5-lite",
                      ].map((model) => (
                        <tr key={model} className="hover:bg-muted/30">
                          <td className="px-3 py-1.5 font-medium text-foreground text-[11px]">{model}</td>
                          <td className="px-2 py-1.5 text-center text-muted-foreground text-[11px]">—</td>
                          <td className="px-2 py-1.5 text-center text-muted-foreground text-[11px]">—</td>
                          <td className="px-3 py-1.5 text-right">
                            <span className="inline-block px-1.5 py-0.5 rounded-none text-[9px] bg-muted text-muted-foreground">Pending</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {node.id === "adk_runtime" && (
              <div className="space-y-2">
                <div className="text-[10px] font-bold text-primary uppercase tracking-wider">
                  OpenTelemetry Trace Metrics
                </div>
                <pre className="p-3 bg-black/60 rounded-none border border-border text-[11px] text-emerald-400 overflow-x-auto whitespace-pre-wrap font-mono">
                  {JSON.stringify(observability, null, 2)}
                </pre>
              </div>
            )}

            <div className="space-y-2 pt-2">
              <Button
                variant="outline"
                size="sm"
                className="w-full text-xs"
                onClick={() => setShowRawJson(!showRawJson)}
              >
                {showRawJson ? "▼ Hide Raw Node State" : "▶ View Raw JSON State"}
              </Button>

              {showRawJson && (
                <pre className="p-3 bg-black/60 rounded-none border border-border text-[11px] text-muted-foreground overflow-x-auto whitespace-pre-wrap font-mono">
                  {JSON.stringify({ node, epochStateSummary: { manifest, phase: epochState?.phase, usage } }, null, 2)}
                </pre>
              )}
            </div>
          </TabsContent>

          {/* Tab 3: Operator Actions */}
          <TabsContent value="actions" className="space-y-4 m-0">
            <div className="space-y-3">
              <div className="text-[10px] font-bold text-primary uppercase tracking-wider">
                Subsystem Operations
              </div>
              <p className="text-xs text-muted-foreground">
                Execute operator actions directly against this subsystem:
              </p>

              <div className="flex flex-col gap-2 pt-1">
                {node.id === "quota_governor" && (
                  <Button
                    variant="default"
                    size="sm"
                    onClick={() => onTriggerAction("quotas")}
                    className="h-8 text-xs justify-center"
                  >
                    Check Vertex AI Quota Availability
                  </Button>
                )}

                {node.id === "promotion_registry" && (
                  <Button
                    variant="default"
                    size="sm"
                    onClick={() => onTriggerAction("review")}
                    className="h-8 text-xs justify-center"
                  >
                    Trigger Review & Construct Validity Audit
                  </Button>
                )}

                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onTriggerAction("preflight")}
                  className="h-8 text-xs justify-center"
                >
                  Run Preflight Diagnostic
                </Button>

                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onTriggerAction("step")}
                  className="h-8 text-xs justify-center"
                >
                  Step Tournament Trajectory
                </Button>
              </div>
            </div>
          </TabsContent>
        </div>
      </Tabs>
    </div>
  );
}
