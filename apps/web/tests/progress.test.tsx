import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { deriveStages, LiveProgress } from "@/components/incident/progress";
import type { TimelineEvent } from "@/lib/types";

let seq = 0;
const ev = (type: string, node: string | null, status: TimelineEvent["status"] = "info", title = type): TimelineEvent => ({
  seq: ++seq,
  id: `evt-${seq}`,
  ts: new Date().toISOString(),
  type,
  title,
  detail: "",
  node,
  status,
  data: {},
});

describe("deriveStages", () => {
  it("marks the active stage while investigating and notes rejected AI proposals", () => {
    const events = [
      ev("incident_created", "create_incident"),
      ev("evidence", "query_metrics"),
      ev("evidence", "query_logs"),
      ev("hypotheses_generated", "generate_hypotheses"),
      ev("remediation_proposed", "generate_remediation_plan"),
      ev("remediation_rejected", "validate_remediation", "danger"),
    ];
    const stages = deriveStages(events, "INVESTIGATING", "test_hypothesis");
    const byId = Object.fromEntries(stages.map((s) => [s.id, s]));
    expect(byId.alert.state).toBe("done");
    expect(byId.metrics.state).toBe("done");
    expect(byId.testing.state).toBe("active");
    expect(byId.validation.note).toBe("1 AI proposal rejected");
    expect(byId.execute.state).toBe("pending");
  });

  it("shows approval as active while awaiting a human", () => {
    const stages = deriveStages([ev("risk_calculated", "calculate_risk")], "AWAITING_APPROVAL", "request_human_approval");
    expect(stages.find((s) => s.id === "approval")?.state).toBe("active");
  });

  it("marks failed verification and skips unreached stages when terminal", () => {
    const stages = deriveStages([ev("verification_failed", "verify_recovery", "danger")], "ROLLED_BACK", "close_incident");
    expect(stages.find((s) => s.id === "verify")?.state).toBe("failed");
    expect(stages.find((s) => s.id === "hypotheses")?.state).toBe("skipped");
  });
});

describe("LiveProgress", () => {
  it("renders every stage", () => {
    render(<LiveProgress events={[ev("incident_created", "create_incident")]} status="INVESTIGATING" currentNode="query_logs" />);
    expect(screen.getByText("Alert received")).toBeInTheDocument();
    expect(screen.getByTestId("stage-alert")).toHaveAttribute("data-state", "done");
    expect(screen.getByTestId("stage-logs")).toHaveAttribute("data-state", "active");
  });
});
