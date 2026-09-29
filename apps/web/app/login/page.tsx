"use client";

import { ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Button, Card, Input } from "@/components/ui/primitives";
import { useAuth } from "@/lib/hooks";

const DEMO_USERS = [
  { username: "alice", role: "approver" },
  { username: "bob", role: "engineer" },
  { username: "victor", role: "viewer" },
  { username: "admin", role: "administrator" },
];

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("alice");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      router.replace("/dashboard");
    } catch (e) {
      setError(e instanceof Error ? e.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 px-4">
      <Card className="w-full max-w-sm p-6">
        <div className="mb-5 flex items-center gap-2">
          <ShieldCheck className="h-6 w-6 text-emerald-700" />
          <div>
            <div className="font-semibold">VeriOps AI</div>
            <div className="text-xs text-slate-500">Autonomous investigation · verified remediation</div>
          </div>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <label className="block text-xs font-medium text-slate-600">
            Username
            <Input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" className="mt-1" />
          </label>
          <label className="block text-xs font-medium text-slate-600">
            Password
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className="mt-1"
            />
          </label>
          {error && <div className="rounded bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</div>}
          <Button type="submit" disabled={busy} className="w-full">
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
        <div className="mt-5 border-t border-slate-100 pt-4 text-xs text-slate-500">
          Local demo users (password from <code>DEMO_USER_PASSWORD</code>, default <code>veriops-demo</code>):
          <div className="mt-2 flex flex-wrap gap-1.5">
            {DEMO_USERS.map((u) => (
              <button
                key={u.username}
                type="button"
                onClick={() => setUsername(u.username)}
                className="rounded border border-slate-200 px-2 py-0.5 hover:bg-slate-50"
              >
                {u.username} · {u.role}
              </button>
            ))}
          </div>
        </div>
      </Card>
    </div>
  );
}
