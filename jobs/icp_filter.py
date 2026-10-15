"""ICP pre-filter: decides which raw_entities are worth a paid Places search in Job 2.

Rules live in config/icp_filter.yaml. NAICS prefixes are checked first; business-name
keywords are a fallback for sources without reliable NAICS.
"""
import re
from dataclasses import dataclass

# PLLC, P.L.L.C., P L L C.
PLLC = re.compile(r"\bp\.?\s?l\.?\s?l\.?\s?c\b", re.IGNORECASE)


@dataclass(frozen=True)
class IcpResult:
    candidate: bool
    industry: str | None
    # 'naics:<code>', 'name:<industry>:<WORD>', 'pllc:<industry>:<WORD>' or 'no_match'.
    reason: str


@dataclass(frozen=True)
class _NameRule:
    industry: str
    any: re.Pattern
    pllc_with_any: re.Pattern | None
    unless_any: re.Pattern | None


def _whole_words(patterns: list[str] | None) -> re.Pattern | None:
    if not patterns:
        return None
    alternatives = "|".join(f"(?:{p})" for p in patterns)
    return re.compile(rf"\b(?:{alternatives})\b", re.IGNORECASE)


class IcpFilter:
    def __init__(self, config: dict):
        self.version = str(config["version"])
        # Longest prefix first, so a more specific prefix wins.
        self.naics_prefixes = sorted(
            config["naics_prefixes"].items(), key=lambda item: -len(item[0])
        )
        self.name_rule_sources = set(config["name_rule_sources"])
        self.name_rules = [
            _NameRule(
                industry,
                _whole_words(rule["any"]),
                _whole_words(rule.get("pllc_with_any")),
                _whole_words(rule.get("unless_any")),
            )
            for industry, rule in config["name_rules"].items()
        ]

    def classify(self, business_name: str, naics_code: str | None,
                 source_system: str) -> IcpResult:
        if naics_code:
            for prefix, industry in self.naics_prefixes:
                if naics_code.startswith(prefix):
                    return IcpResult(True, industry, f"naics:{naics_code}")
        if source_system in self.name_rule_sources:
            is_pllc = PLLC.search(business_name) is not None
            for rule in self.name_rules:
                if rule.unless_any and rule.unless_any.search(business_name):
                    continue
                if m := rule.any.search(business_name):
                    return IcpResult(True, rule.industry,
                                     f"name:{rule.industry}:{m.group(0).upper()}")
                if is_pllc and rule.pllc_with_any and (m := rule.pllc_with_any.search(business_name)):
                    return IcpResult(True, rule.industry,
                                     f"pllc:{rule.industry}:{m.group(0).upper()}")
        return IcpResult(False, None, "no_match")
