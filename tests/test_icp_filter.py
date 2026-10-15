import pytest

from jobs.common import load_config
from jobs.icp_filter import IcpFilter

ICP = IcpFilter(load_config("icp_filter.yaml"))


@pytest.mark.parametrize("name, naics, source, expected", [
    # NAICS wins for any source.
    ("GREAT CLIPS AT POINT WEST", "722511", "comptroller", ("restaurant", "naics:722511")),
    ("ANYTHING LLC", "238220", "sos", ("contractor", "naics:238220")),
    ("ANYTHING LLC", "721110", "comptroller", ("hotel", "naics:721110")),
    ("ANYTHING LLC", "621111", "comptroller", ("medical", "naics:621111")),
    ("ANYTHING LLC", "531210", "sos", ("re_investor", "naics:531210")),
    # Sales tax rows don't use name rules.
    ("TAQUERIA EL ZACATECANO LLC", "454110", "comptroller", None),
    # Franchise names.
    ("TAQUERIA EL ZACATECANO LLC", None, "sos", ("restaurant", "name:restaurant:TAQUERIA")),
    ("CEDAR LINE CONSTRUCTION LLC", None, "sos", ("contractor", "name:contractor:CONSTRUCTION")),
    ("ARCHER FAMILY MEDICAL GROUP, PLLC", None, "sos", ("medical", "name:medical:MEDICAL")),
    ("TORO ANESTHESIA PLLC", None, "sos", ("medical", "name:medical:ANESTHESIA")),
    ("LONGVIEW NORTH HOTEL LLC", None, "sos", ("hotel", "name:hotel:HOTEL")),
    ("GANTA HOSPITALITY LLC", None, "sos", ("hotel", "name:hotel:HOSPITALITY")),
    ("JW CLEBURNE PROPERTIES, LLC", None, "sos", ("re_investor", "name:re_investor:PROPERTIES")),
    ("TRIANGLE HULEN RE, LLC", None, "sos", ("re_investor", "name:re_investor:RE")),
    ("PRESTON RE LP", None, "sos", ("re_investor", "name:re_investor:RE")),
    # A non-ICP NAICS on a franchise row still falls back to the name.
    ("PIPESTYLE PLUMBING, LLC", "541990", "sos", ("contractor", "name:contractor:PLUMBING")),
    # PLLC counts only with a medical word.
    ("LUMIN CARE PLLC", None, "sos", ("medical", "pllc:medical:CARE")),
    ("KIDZ LINGO PEDIATRIC SPEECH THERAPY PLLC", None, "sos", ("medical", "name:medical:PEDIATRIC")),
    ("NORTH TEXAS COUNSELING, P.L.L.C.", None, "sos", ("medical", "pllc:medical:COUNSELING")),
    ("METCALF LEGAL, PLLC", None, "sos", None),
    ("BRIAN ROBISON ADR, PLLC", None, "sos", None),
    # Without PLLC, the weaker medical words don't count.
    ("DALLAS SCHOOL OF MASSAGE THERAPY, LLC", None, "sos", None),
    # Rejected by the strict list.
    ("SIGNATURE CARE HOLDINGS, LLC", None, "sos", None),
    ("ANSARI VENTURES LLC", None, "sos", None),
    ("NU SALON SUITES LLC", None, "sos", None),
    ("CONTY HOSPITALITY & JANITORIAL LLC", None, "sos", None),
    ("SPRING BAR TOOL LLC", None, "sos", None),
    ("APEX EQUIPMENT RENTAL LLC", None, "sos", None),
    ("WC AUTO ELECTRICAL LLC", None, "sos", None),
    # "RE" only at the end before the entity suffix.
    ("RE MAX FANS LLC", None, "sos", None),
    ("KOBA SOLUTIONS LLC", None, "sos", None),
])
def test_classify(name, naics, source, expected):
    result = ICP.classify(name, naics, source)
    if expected is None:
        assert (result.candidate, result.industry, result.reason) == (False, None, "no_match")
    else:
        assert result.candidate
        assert (result.industry, result.reason) == expected


def test_longest_naics_prefix_wins():
    icp = IcpFilter({
        "version": "t", "name_rule_sources": [], "name_rules": {},
        "naics_prefixes": {"72": "restaurant", "7211": "hotel"},
    })
    assert icp.classify("X", "721110", "sos").industry == "hotel"
