"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  FlaskConical,
  ShieldCheck,
  ClipboardList,
  Radio,
} from "lucide-react";

import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/", label: "Scenarios", icon: FlaskConical },
  { href: "/runs", label: "Live Investigation", icon: Radio },
  { href: "/remediations", label: "Remediations", icon: ShieldCheck },
  { href: "/scorecards", label: "Scorecards", icon: ClipboardList },
] as const;

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="bg-sidebar border-border flex w-60 shrink-0 flex-col border-r">
      <div className="border-border flex items-center gap-3 border-b px-5 py-4">
        <div className="from-primary flex size-8 items-center justify-center rounded-lg bg-gradient-to-br to-indigo-400 shadow-[0_0_16px_-2px_var(--primary)]">
          <Activity className="size-4 text-white" strokeWidth={2.5} />
        </div>
        <div>
          <div className="text-sm font-semibold leading-tight">FaultLine</div>
          <div className="text-muted-foreground text-xs leading-tight">
            Incident-response arena
          </div>
        </div>
      </div>

      <nav className="flex flex-1 flex-col gap-1 p-3">
        {NAV_ITEMS.map((item) => {
          const active =
            item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2.5 rounded-md border-l-2 px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "border-primary bg-primary/10 text-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground border-transparent"
              )}
            >
              <Icon className={cn("size-4", active && "text-primary")} />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-border text-muted-foreground flex items-center gap-2 border-t px-5 py-3 text-xs">
        <span className="bg-success relative flex size-1.5 rounded-full">
          <span className="bg-success absolute inline-flex size-full animate-ping rounded-full opacity-75" />
        </span>
        System online
      </div>
    </aside>
  );
}
