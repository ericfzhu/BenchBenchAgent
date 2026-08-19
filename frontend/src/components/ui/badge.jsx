import * as React from "react";
import { cn } from "../../lib/utils";

const badgeVariants = {
  default: "border-transparent bg-primary text-primary-foreground shadow-sm",
  secondary: "border-transparent bg-secondary text-secondary-foreground",
  destructive: "border-transparent bg-destructive text-destructive-foreground shadow-sm",
  outline: "text-foreground border-border bg-card",
  success: "border-transparent bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30",
  warning: "border-transparent bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30",
};

function Badge({ className, variant = "default", ...props }) {
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-none border px-2.5 py-0.5 text-[10px] font-mono font-semibold uppercase tracking-wider transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
        badgeVariants[variant] || badgeVariants.default,
        className
      )}
      {...props}
    />
  );
}

export { Badge, badgeVariants };
