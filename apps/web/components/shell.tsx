"use client";

import {
  Activity,
  BookOpen,
  CheckCircle2,
  FlaskConical,
  LayoutDashboard,
  LogOut,
  Radar,
  Settings,
  ShieldCheck,
  Siren,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { cn } from "@/lib/format";
import { useAuth, usePoll } from "@/lib/hooks";
import type { Incident } from "@/lib/types";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/incidents", label: "Incidents", icon: Siren },
  { href: "/investigations", label: "Investigations", icon: Radar },
  { href: "/approvals", label: "Approvals", icon: CheckCircle2 },
  { href: "/knowledge", label: "Knowledge", icon: BookOpen },
  { href: "/evaluations", label: "Evaluations", icon: FlaskConical },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Shell({ children }: { children: ReactNode }) {
  const { user, ready, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const pending = usePoll<Incident[]>(user ? "/api/incidents?status=AWAITING_APPROVAL&limit=50" : null, 5000);

  useEffect(() => {
    if (ready && !user) router.replace("/login");
  }, [ready, user, router]);

  if (!ready || !user) {
    return <div className="flex h-screen items-center justify-center text-sm text-slate-500">Loading…</div>;
  }

  return (
    <div className="flex min-h-screen">
      <aside className="fixed inset-y-0 left-0 flex w-56 flex-col border-r border-slate-800 bg-slate-900 text-slate-300">
        <div className="flex items-center gap-2 px-4 py-4">
          <ShieldCheck className="h-5 w-5 text-emerald-400" />
          <div>
            <div className="text-sm font-semibold text-white">VeriOps AI</div>
            <div className="text-[11px] text-slate-400">Verified remediation</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-2">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || (href !== "/dashboard" && pathname.startsWith(href));
            const count = href === "/approvals" ? pending.data?.length ?? 0 : 0;
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm",
                  active ? "bg-slate-800 text-white" : "hover:bg-slate-800/60 hover:text-white",
                )}
              >
                <Icon className="h-4 w-4" />
                <span className="flex-1">{label}</span>
                {count > 0 && <span className="rounded bg-amber-500 px-1.5 text-[11px] font-semibold text-slate-900">{count}</span>}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-slate-800 px-4 py-3 text-xs">
          <div className="flex items-center gap-2">
            <Activity className="h-3.5 w-3.5 text-emerald-400" />
            <span className="text-slate-400">Signed in as</span>
          </div>
          <div className="mt-1 font-medium text-white">{user.display_name}</div>
          <div className="text-slate-400">{user.role}</div>
          <button onClick={logout} className="mt-2 inline-flex items-center gap-1 text-slate-400 hover:text-white">
            <LogOut className="h-3.5 w-3.5" /> Sign out
          </button>
        </div>
      </aside>
      <main className="ml-56 flex-1 px-6 py-5">{children}</main>
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex items-end justify-between gap-4">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
        {description && <p className="mt-0.5 text-sm text-slate-500">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
