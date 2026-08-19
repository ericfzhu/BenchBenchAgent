import React, { useState, useEffect, useCallback, useRef } from "react";
import { TopHUD } from "./components/TopHUD.jsx";
import { LeftSidebar } from "./components/LeftSidebar.jsx";
import { ExcalidrawCanvas } from "./components/ExcalidrawCanvas.jsx";
import { InspectorDrawer } from "./components/InspectorDrawer.jsx";
import { JobsDrawer } from "./components/JobsDrawer.jsx";
import { ToastContainer } from "./components/ToastContainer.jsx";

export function App() {
  const [systemState, setSystemState] = useState(null);
  const [activeEpochId, setActiveEpochId] = useState(null);
  const [epochState, setEpochState] = useState(null);
  const [selectedSubsystemId, setSelectedSubsystemId] = useState(null);
  const [isJobsOpen, setIsJobsOpen] = useState(false);
  const [isEditMode, setIsEditMode] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [loading, setLoading] = useState(true);

  const excalidrawAPIRef = useRef(null);

  const addToast = useCallback((message, type = "info") => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4500);
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // Fetch initial system state
  const fetchSystemState = useCallback(async () => {
    try {
      const res = await fetch("/api/system/state");
      if (res.ok) {
        const data = await res.json();
        setSystemState(data);
        if (!activeEpochId && data.epochs && data.epochs.length > 0) {
          setActiveEpochId(data.epochs[0].epoch_id);
        }
      }
    } catch (err) {
      addToast("Failed to connect to BBA API: " + err.message, "error");
    } finally {
      setLoading(false);
    }
  }, [activeEpochId, addToast]);

  // Fetch active epoch state
  const fetchEpochState = useCallback(async (epochId) => {
    if (!epochId) return;
    try {
      const res = await fetch(`/api/epoch/${epochId}/state`);
      if (res.ok) {
        const data = await res.json();
        setEpochState(data);
      }
    } catch (err) {
      // Quiet fail during background polling
    }
  }, []);

  useEffect(() => {
    fetchSystemState();
  }, [fetchSystemState]);

  useEffect(() => {
    if (activeEpochId) {
      fetchEpochState(activeEpochId);
    }
  }, [activeEpochId, fetchEpochState]);

  // Dynamic background polling
  useEffect(() => {
    const hasActive = Boolean(
      epochState?.active_job ||
      epochState?.recent_jobs?.some((j) => j.status === "running" || j.status === "queued")
    );
    const interval = setInterval(() => {
      fetchSystemState();
      if (activeEpochId) fetchEpochState(activeEpochId);
    }, hasActive ? 1500 : 5000);

    return () => clearInterval(interval);
  }, [fetchSystemState, fetchEpochState, activeEpochId, epochState]);

  // Handle Subsystem Selection (from Sidebar or Canvas double-click)
  const handleSelectSubsystem = useCallback((nodeId) => {
    setSelectedSubsystemId(nodeId);
  }, []);

  const handleCloseInspector = useCallback(() => {
    setSelectedSubsystemId(null);
  }, []);

  // Trigger Operator Actions
  const handleTriggerAction = useCallback(async (action) => {
    if (!activeEpochId) {
      addToast("Please select or create an epoch first.", "error");
      return;
    }

    try {
      const csrfToken = systemState?.csrf_token || "";
      const res = await fetch(`/api/epoch/${activeEpochId}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action,
          csrf_token: csrfToken,
          confirmed: "yes",
        }),
      });

      const data = await res.json();
      if (res.ok && data.status === "ok") {
        addToast(`Dispatched ${data.label || action} (Job: ${data.job_id})`, "success");
        setIsJobsOpen(true);
        fetchEpochState(activeEpochId);
      } else {
        addToast(`Action failed: ${data.message || data.detail || "Unknown error"}`, "error");
      }
    } catch (err) {
      addToast(`Action error: ${err.message}`, "error");
    }
  }, [activeEpochId, systemState, addToast, fetchEpochState]);

  // Create New Epoch
  const handleCreateEpoch = useCallback(async (newId) => {
    try {
      const csrfToken = systemState?.csrf_token || "";
      const res = await fetch("/api/epochs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          epoch_id: newId,
          csrf_token: csrfToken,
        }),
      });

      const data = await res.json();
      if (res.ok && data.status === "ok") {
        addToast(`Created epoch ${newId}`, "success");
        await fetchSystemState();
        setActiveEpochId(newId);
      } else {
        addToast(`Failed to create epoch: ${data.message || data.detail}`, "error");
      }
    } catch (err) {
      addToast(`Error creating epoch: ${err.message}`, "error");
    }
  }, [systemState, addToast, fetchSystemState]);

  const selectedNode = (epochState?.graph?.nodes || systemState?.graph?.nodes || []).find(
    (n) => n.id === selectedSubsystemId
  );

  const subsystems = epochState?.graph?.nodes || systemState?.graph?.nodes || [];
  const initialScene = epochState?.excalidraw || systemState?.excalidraw;
  const recentJobs = epochState?.recent_jobs || systemState?.recent_jobs || [];
  const hasActiveJob = Boolean(
    epochState?.active_job ||
    recentJobs.some((j) => j.status === "running" || j.status === "queued")
  );

  if (loading && !systemState) {
    return (
      <div style={{ padding: "40px", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
        Connecting to BBA Operator Console...
      </div>
    );
  }

  return (
    <div className="flex flex-col w-screen h-screen overflow-hidden bg-background text-foreground font-mono">
      <TopHUD
        epochs={systemState?.epochs || []}
        activeEpochId={activeEpochId}
        onSelectEpoch={setActiveEpochId}
        onCreateEpoch={handleCreateEpoch}
        epochState={epochState}
        systemState={systemState}
        onTriggerAction={handleTriggerAction}
        onOpenJobs={() => setIsJobsOpen(!isJobsOpen)}
        hasActiveJob={hasActiveJob}
        isEditMode={isEditMode}
        onToggleEditMode={() => setIsEditMode(!isEditMode)}
      />

      <div className="flex flex-1 w-full h-[calc(100vh-3.5rem)] relative overflow-hidden">
        <LeftSidebar
          subsystems={subsystems}
          selectedNodeId={selectedSubsystemId}
          onSelectSubsystem={handleSelectSubsystem}
          epochState={epochState}
        />

        <ExcalidrawCanvas
          initialData={initialScene}
          sceneData={epochState?.excalidraw || systemState?.excalidraw}
          onNodeSelect={handleSelectSubsystem}
          onReady={(api) => { excalidrawAPIRef.current = api; }}
          isEditMode={isEditMode}
        />

        {selectedNode && (
          <InspectorDrawer
            node={selectedNode}
            epochState={epochState}
            csrfToken={systemState?.csrf_token}
            onClose={handleCloseInspector}
            onTriggerAction={handleTriggerAction}
          />
        )}

        {isJobsOpen && (
          <JobsDrawer
            jobs={recentJobs}
            activeJobId={epochState?.active_job?.job_id}
            onClose={() => setIsJobsOpen(false)}
          />
        )}
      </div>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
