import { describe, expect, it } from "vitest";

import { fmtMs, fmtPct, fmtRatio, fmtUsd, titleCase } from "@/lib/format";

describe("formatters", () => {
  it("formats latency", () => {
    expect(fmtMs(310)).toBe("310 ms");
    expect(fmtMs(4200)).toBe("4.20 s");
    expect(fmtMs(null)).toBe("—");
  });
  it("formats percentages and money", () => {
    expect(fmtPct(11.8)).toBe("11.8%");
    expect(fmtRatio(0.933)).toBe("93%");
    expect(fmtUsd(0.0239)).toBe("$0.024");
    expect(fmtUsd(0.0012)).toBe("$0.0012");
    expect(titleCase("db_query_regression")).toBe("Db Query Regression");
  });
});
