import { describe, expect, it } from "vitest";
import { parse, sectionOf } from "./router";

describe("routes", () => {
  it("reads the hash", () => {
    expect(parse("#/passes/S1D-0097DF-alboran")).toEqual({ section: "passes", passId: "S1D-0097DF-alboran" });
    expect(parse("#/vessels/667002310")).toEqual({ section: "vessels", mmsi: 667002310 });
    expect(parse("#/vessels/abc")).toEqual({ section: "vessels", mmsi: null });
    expect(parse("#/companies/NK-a%2Fb")).toEqual({ section: "company", companyId: "NK-a/b" });
    expect(parse("")).toEqual({ section: "passes", passId: null });
    expect(parse("#/nowhere")).toEqual({ section: "passes", passId: null });
  });

  it("puts a company under vessels", () => {
    expect(sectionOf({ section: "company", companyId: "x" })).toBe("vessels");
  });
});
