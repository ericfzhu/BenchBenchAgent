import React, { useState, useEffect, useRef } from "react";
import { Button } from "./ui/button.jsx";
import { Badge } from "./ui/badge.jsx";

const JOB_BADGE_VARIANTS = {
  running: "default",
  done: "success",
  failed: "destructive",
  queued: "warning",
};

export function JobsDrawer({
  jobs,
  activeJobId,
  onClose,
}) {
  const [selectedJob, setSelectedJob] = useState(null);
  const [logContent, setLogContent] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const logEndRef = useRef(null);

  useEffect(() => {
    if (jobs && jobs.length > 0) {
      if (!selectedJob || !jobs.find((j) => j.job_id === selectedJob.job_id)) {
        setSelectedJob(jobs[0]);
      } else {
        const updated = jobs.find((j) => j.job_id === selectedJob.job_id);
        if (updated) setSelectedJob(updated);
      }
    }
  }, [jobs]);

  useEffect(() => {
    if (!selectedJob) return;

    let isMounted = true;
    const fetchJobDetails = async () => {
      try {
        const res = await fetch(`/api/jobs/${selectedJob.job_id}`);
        if (res.ok) {
          const data = await res.json();
          if (isMounted) {
            setLogContent(data.stdout || data.stderr || data.error || data.result || "No log output recorded yet.");
          }
        }
      } catch (err) {
        if (isMounted) setLogContent("Error fetching job logs: " + err.message);
      }
    };

    fetchJobDetails();
    const interval = setInterval(fetchJobDetails, selectedJob.status === "running" ? 1500 : 5000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [selectedJob?.job_id, selectedJob?.status]);

  useEffect(() => {
    if (autoScroll && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logContent, autoScroll]);

  return (
    <div className="absolute bottom-0 left-72 right-0 h-96 bg-card border-t border-border shadow-2xl flex flex-col font-mono z-40 animate-in slide-in-from-bottom duration-200">
      {/* Header */}
      <div className="flex h-12 items-center justify-between px-4 bg-muted/30 border-b border-border">
        <div className="flex items-center gap-2.5">
          <span className="text-xs font-bold text-foreground">
            OPERATIONS & TASK LOGS
          </span>
          <Badge variant="outline" className="text-[10px] h-6 px-2">
            {jobs?.length || 0} jobs
          </Badge>
        </div>

        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer hover:text-foreground">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
              className="rounded-none border-border bg-muted/50"
            />
            Auto-scroll
          </label>
          <Button variant="ghost" size="icon" onClick={onClose} className="h-8 w-8 text-muted-foreground hover:text-foreground">
            ✕
          </Button>
        </div>
      </div>

      {/* Main Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Jobs List Sidebar */}
        <div className="w-64 border-r border-border overflow-y-auto p-3 space-y-1.5 bg-muted/10">
          {(!jobs || jobs.length === 0) ? (
            <div className="p-3 text-xs text-muted-foreground">
              No operations run yet.
            </div>
          ) : (
            jobs.map((j) => (
              <div
                key={j.job_id}
                className={`p-2.5 rounded-none border text-xs cursor-pointer transition-colors ${
                  selectedJob?.job_id === j.job_id
                    ? "bg-accent/80 border-primary text-foreground font-semibold shadow-xs"
                    : "bg-muted/30 border-border/40 hover:bg-muted/60 hover:border-border text-muted-foreground hover:text-foreground"
                }`}
                onClick={() => setSelectedJob(j)}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="truncate text-foreground font-bold text-xs">
                    {j.label || j.job_id}
                  </span>
                  <Badge variant={JOB_BADGE_VARIANTS[j.status] || "default"} className="text-[9px] px-1 py-0 h-4">
                    {j.status}
                  </Badge>
                </div>
                <div className="text-[10px] text-muted-foreground">
                  {j.started_at ? new Date(j.started_at).toLocaleTimeString() : "—"}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Live Output Terminal */}
        <div className="flex-1 bg-black/80 p-4 overflow-y-auto font-mono text-xs leading-relaxed text-zinc-300">
          <pre className="whitespace-pre-wrap font-mono">{logContent}</pre>
          <div ref={logEndRef} />
        </div>
      </div>
    </div>
  );
}
