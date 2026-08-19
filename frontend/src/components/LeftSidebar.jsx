import React, { useState, useMemo } from "react";
import { Input } from "./ui/input.jsx";
import { Badge } from "./ui/badge.jsx";

const CATEGORY_NAMES = {
  tournament_loop: "Tournament Pipeline",
  supporting_infrastructure: "Supporting Infrastructure",
  models_and_engines: "Models & Engines",
};

export function LeftSidebar({
  subsystems,
  selectedNodeId,
  onSelectSubsystem,
  epochState,
}) {
  const [searchQuery, setSearchQuery] = useState("");

  const groupedSubsystems = useMemo(() => {
    if (!subsystems) return {};
    const filtered = subsystems.filter((sub) => {
      const q = searchQuery.toLowerCase();
      return (
        sub.label.toLowerCase().includes(q) ||
        (sub.subtitle && sub.subtitle.toLowerCase().includes(q)) ||
        (sub.summary && sub.summary.toLowerCase().includes(q))
      );
    });

    const groups = {};
    for (const sub of filtered) {
      const cat = sub.category || "tournament_loop";
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(sub);
    }
    return groups;
  }, [subsystems, searchQuery]);

  return (
    <aside className="flex flex-col w-72 h-full bg-card border-r border-border font-mono z-20 shrink-0">
      <div className="p-4 border-b border-border">
        <div className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-2">
          Subsystem Directory
        </div>
        <Input
          placeholder="Filter subsystems..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="h-8 text-xs"
        />
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-5">
        {Object.entries(groupedSubsystems).map(([catKey, items]) => (
          <div key={catKey} className="space-y-2">
            <div className="flex items-center justify-between text-[10px] font-bold text-muted-foreground uppercase tracking-wider px-1">
              <span>{CATEGORY_NAMES[catKey] || catKey}</span>
              <Badge variant="outline" className="text-[9px] px-1.5 py-0 h-4">
                {items.length}
              </Badge>
            </div>

            <div className="space-y-1.5">
              {items.map((sub) => {
                const isSelected = selectedNodeId === sub.id;
                const status = sub.status || "idle";

                return (
                  <div
                    key={sub.id}
                    className={`flex items-center justify-between p-3 rounded-none border transition-all cursor-pointer min-h-[46px] ${
                      isSelected
                        ? "bg-accent/80 border-primary text-foreground shadow-xs"
                        : "bg-muted/30 border-border/40 hover:bg-muted/70 hover:border-border text-muted-foreground hover:text-foreground"
                    }`}
                    onClick={() => onSelectSubsystem(sub.id)}
                  >
                    <div className="flex flex-col min-w-0 pr-2">
                      <span className="text-xs font-semibold truncate text-foreground">
                        {sub.label}
                      </span>
                      <span className="text-[10px] text-muted-foreground truncate">
                        {sub.subtitle}
                      </span>
                    </div>

                    <div
                      className={`h-2 w-2 rounded-none shrink-0 ${
                        status === "active"
                          ? "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]"
                          : status === "running"
                          ? "bg-sky-400 shadow-[0_0_8px_rgba(56,189,248,0.6)]"
                          : status === "warning"
                          ? "bg-amber-400"
                          : "bg-muted-foreground/40"
                      }`}
                      title={`Status: ${status}`}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
