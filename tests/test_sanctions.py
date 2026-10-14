"""Sanctions and the companies behind vessels, from OpenSanctions' FollowTheMoney exports."""

from datetime import date

from uatu.ais.sanctions import DETENTIONS, GUR, PORT_BANS, SANCTIONS, Graph, Tables, assess, explain

TODAY = date(2026, 10, 14)


def vessel(id: str, name: str, imo: int, *topics: str, **props: list[str]) -> dict:
    return {"id": id, "schema": "Vessel", "caption": name,
            "properties": {"name": [name], "imoNumber": [f"IMO{imo}"], "topics": list(topics), **props}}  # fmt: skip


def company(id: str, name: str, *topics: str, schema: str = "Company", **props: list[str]) -> dict:
    return {
        "id": id,
        "schema": schema,
        "caption": name,
        "properties": {"name": [name], "topics": list(topics), **props},
    }


def runs(company_id: str, vessel_id: str, role: str, **props: list[str]) -> dict:
    return {"id": f"{company_id}>{vessel_id}>{role}", "schema": "UnknownLink", "caption": "link",
            "properties": {"subject": [company_id], "object": [vessel_id], "role": [role], **props}}  # fmt: skip


def measure(target: str, **props: list[str]) -> dict:
    return {
        "id": f"sanction-{target}",
        "schema": "Sanction",
        "caption": "Sanction",
        "properties": {"entity": [target], **props},
    }


def build() -> Tables:
    graph = Graph(today=TODAY)
    graph.add(
        [
            vessel("NK-tex", "TEX", 9246322, "sanction", programId=["US-IRAN"], mmsi=["636021547", "bad"]),
            vessel("NK-sat", "SATURN", 9421972, "sanction", programId=["EU-RUS", "GB-RUS"]),
            company("NK-marvise", "MARVISE SMC DMCC", "sanction", programId=["US-IRAN"]),
            company("NK-counter", "Sanctioned by Russia", "sanction.counter"),
            {"id": "own-1", "schema": "Ownership", "caption": "Ownership",
             "properties": {"owner": ["NK-marvise"], "asset": ["NK-tex"], "role": ["Property in the interest of"]}},
            measure("NK-tex", authority=["Office of Foreign Assets Control"], reason=["Executive Order 13902 (Iran)"],
                    startDate=["2025-07-30"]),
        ],
        SANCTIONS,
    )  # fmt: skip
    graph.add(
        [
            vessel("NK-astra", "ASTRA", 9000001, "mare.shadow", "poi", description=["The tanker exports Urals."]),
            vessel("NK-coal", "COALER", 9000002, "poi"),
            vessel("NK-sat", "SATURN", 9421972, "poi"),
            company("NK-marvise", "MARVISE SMC DMCC", "poi"),  # GUR's copy: not where sanctions come from
            company("NK-xilong", "Xi Long Shan Shipmanagement", "poi"),
            company("NK-greek", "Big Greek Managers", "poi"),
            company("NK-counter", "Sanctioned by Russia", "poi"),
            runs("NK-marvise", "NK-astra", "security manager", startDate=["2024-05"]),
            runs("NK-xilong", "NK-sat", "commerce manager"),
            runs("NK-xilong", "NK-tex", "commerce manager"),
            runs("NK-xilong", "NK-coal", "commerce manager", startDate=["2025"]),
            runs("NK-greek", "NK-tex", "security manager"),
            runs("NK-counter", "NK-coal", "owner"),
            runs("NK-astra", "NK-pandi", "P&I Club"),  # vessel first: either way round
            company("NK-pandi", "Some P&I Club"),
        ],
        GUR,
    )  # fmt: skip
    graph.add(
        [
            vessel("NK-old", "OLD TIMER", 9000003, "mare.detained"),
            vessel("NK-new", "NEW HAND", 9000004, "mare.detained"),
            company("NK-greek", "Big Greek Managers"),
            company("NK-dnv", "DNV AS", schema="Organization"),
            runs("NK-marvise", "NK-old", "Company", date=["2021-03-01"]),  # 5 years ago: left out
            runs("NK-greek", "NK-new", "Company", date=["2026-06-03"]),
            runs("NK-greek", "NK-new", "Company", date=["2025-01-10"]),
            runs("NK-dnv", "NK-new", "Classification society", date=["2026-06-03"]),
            company("NK-marvise", "MARVISE SMC DMCC"),
        ],
        DETENTIONS[0],
    )  # fmt: skip
    graph.add(
        [
            vessel("NK-banned", "BANNED ONE", 9000005, "reg.warn"),
            measure("NK-banned", authority=["Paris MoU on Port State Control", "Italy"],
                    reason=["Multiple detentions"], startDate=["2026-08-05"], duration=["Twelve months"]),
        ],
        PORT_BANS,
    )  # fmt: skip
    return graph.tables()


def test_sanctioned_vessels_are_keyed_by_imo_and_say_why():
    sanctioned = {v["imo"]: v for v in build().sanctioned}
    assert sorted(sanctioned) == [9246322, 9421972]
    tex = sanctioned[9246322]
    assert (tex["name"], tex["mmsis"], tex["programs"], tex["source_id"]) == ("TEX", "636021547", "US-IRAN", "NK-tex")
    assert tex["sanctions"] == "2025-07-30 · Office of Foreign Assets Control · Executive Order 13902 (Iran)"
    assert sanctioned[9421972]["programs"] == "EU-RUS;GB-RUS"


def test_who_runs_a_vessel_by_role_and_source():
    tables = build()
    links = {(link["vessel_imo"], link["company_id"], link["role"], link["source"]): link for link in tables.links}
    assert links[9246322, "NK-marvise", "owner", SANCTIONS]["role_label"] == "property in the interest of"
    assert links[9000001, "NK-marvise", "ism_manager", GUR]["since"] == date(2024, 5, 1)
    assert links[9000001, "NK-pandi", "insurer", GUR]
    # Repeated detentions are one link, as of the latest; surveyors and old detentions are left out.
    assert links[9000004, "NK-greek", "ism_manager", DETENTIONS[0]]["as_of"] == date(2026, 6, 3)
    assert not any(company == "NK-dnv" for _, company, _, _ in links)
    assert (9000003, "NK-marvise", "ism_manager", DETENTIONS[0]) not in links

    companies = {c["id"]: c for c in tables.companies}
    assert companies["NK-marvise"]["sanctioned"] and companies["NK-marvise"]["programs"] == "US-IRAN"
    assert not companies["NK-counter"]["sanctioned"]  # under Russian counter-sanctions: not the same thing
    assert (companies["NK-xilong"]["vessels"], companies["NK-xilong"]["sanctioned_vessels"]) == (3, 2)
    assert (companies["NK-greek"]["vessels"], companies["NK-greek"]["sanctioned_vessels"]) == (2, 1)


def test_watchlists_say_what_the_source_says():
    watchlists = {(w["imo"], w["watchlist"]): w for w in build().watchlists}
    assert watchlists[9000001, "shadow_fleet"]["note"] == "The tanker exports Urals."
    assert (9000002, "war_and_sanctions") in watchlists
    assert (9421972, "war_and_sanctions") in watchlists  # on GUR's list and sanctioned
    banned = watchlists[9000005, "port_ban"]["note"]
    assert banned == "2026-08-05 · Paris MoU on Port State Control, Italy · Multiple detentions · Twelve months"


def test_vessels_off_the_lists_worth_a_look():
    risks = {r["imo"]: r for r in build().risks}
    assert 9246322 not in risks and 9421972 not in risks  # sanctioned: alerts of their own

    astra = risks[9000001]  # managed by a sanctioned company, and on GUR's shadow fleet list
    assert astra["level"] == "sanctioned_owner"
    assert [r["kind"] for r in astra["reasons"]] == ["sanctioned_company", "shadow_fleet"]
    assert explain(astra["reasons"][0]) == ("su gestor ISM, MARVISE SMC DMCC, está en listas de sanciones (US-IRAN)")

    coal = risks[9000002]  # its manager runs 2 sanctioned vessels out of 3
    assert coal["level"] == "watchlisted"
    assert [r["kind"] for r in coal["reasons"]] == ["sanctioned_fleet", "war_and_sanctions"]
    assert explain(coal["reasons"][0]) == (
        "su gestor comercial, Xi Long Shan Shipmanagement, tiene sancionados 2 de sus 3 barcos conocidos"
    )

    # A manager with one sanctioned vessel out of two is not a sanctioned fleet's manager...
    assert 9000004 not in risks
    # ...nor is a detention from years ago a manager today.
    assert 9000003 not in risks
    assert risks[9000005]["level"] == "watchlisted"
    assert explain(risks[9000005]["reasons"][0]) == "tiene prohibida la entrada en los puertos europeos (Paris MoU)"


def test_an_insurer_is_not_a_manager():
    companies = {
        "club": {"id": "club", "name": "Sanctioned Club", "sanctioned": True, "programs": "GB-RUS", "vessels": 0,
                 "sanctioned_vessels": 0},
        "big": {"id": "big", "name": "Big Fleet", "sanctioned": False, "programs": None, "vessels": 3,
                "sanctioned_vessels": 3},
    }  # fmt: skip
    insured = [{"company_id": "club", "role": "insurer"}, {"company_id": "big", "role": "insurer"}]
    level, reasons = assess(insured, companies, [])
    assert level == "watchlisted" and [r["kind"] for r in reasons] == ["sanctioned_insurer"]
    assert assess([], companies, []) is None
