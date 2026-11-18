/* ── Section — collapsible flat list section (Pinned / Recent) ──────── */

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export function Section({
  title,
  count,
  empty,
  children,
}: {
  title: string;
  count: number;
  empty?: string;
  children?: React.ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div className="august-sidebar-section">
      <div className="section-head">
        <Button
          variant="bare"
          type="button"
          className="section-head-toggle"
          onClick={() => setCollapsed((v) => !v)}
          aria-expanded={!collapsed}
          title={collapsed ? `Show ${title}` : `Hide ${title}`}
        >
          <ChevronRight
            className={cn('section-head-chev', !collapsed && 'is-open')}
            aria-hidden
          />
          <span className="section-head-label">{title}</span>
        </Button>
      </div>
      {!collapsed &&
        (count === 0 && title === "Pinned" ? (
          <p className="section-empty">{empty ?? "No items"}</p>
        ) : (
          <div className="space-y-px">{children}</div>
        ))}
    </div>
  );
}
