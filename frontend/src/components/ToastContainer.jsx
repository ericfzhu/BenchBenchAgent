import React from "react";

const TOAST_BORDER_COLORS = {
  success: "border-l-4 border-l-emerald-500",
  error: "border-l-4 border-l-destructive",
  info: "border-l-4 border-l-primary",
};

export function ToastContainer({ toasts, onDismiss }) {
  if (!toasts || toasts.length === 0) return null;

  return (
    <div className="fixed bottom-5 right-5 flex flex-col gap-2 z-50 pointer-events-none font-mono">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto min-w-[280px] max-w-md p-3 rounded-none bg-card border border-border text-foreground shadow-xl flex items-center justify-between gap-3 text-xs animate-in slide-in-from-bottom-2 ${
            TOAST_BORDER_COLORS[t.type] || TOAST_BORDER_COLORS.info
          }`}
        >
          <span>{t.message}</span>
          <button
            onClick={() => onDismiss(t.id)}
            className="text-muted-foreground hover:text-foreground text-sm font-bold cursor-pointer"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
