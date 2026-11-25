"""Guardrails around the model.

Input: cheap deterministic checks before the model sees a message (size, prompt injection, other
customers' data, emergencies). Output: every reply is checked against what the tools actually
returned. Products it names must be ones the tools showed, every dollar amount must come from a
tool result, policy sections must exist, and it may never promise allergy safety.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..helpdesk import policy

MAX_INPUT_CHARS = 2000

_INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above|your) (instructions|rules|prompt)|system prompt|"
    r"you are now|developer mode|jailbreak|disregard (your|the|all) (rules|instructions|policy)|"
    r"act as (an? )?(admin|manager|developer)|pretend (you are|to be)|override (the )?(policy|limit)",
    re.I,
)
_OTHER_CUSTOMER = re.compile(
    r"(someone|somebody|another (customer|person)|other customer'?s?|my (neighbou?r|friend|partner)'?s?) "
    r"(order|account|address|details|phone)",
    re.I,
)
_EMERGENCY = re.compile(
    r"can'?t breathe|anaphyla|throat (is )?(closing|swelling)|unconscious|passed out|epipen|"
    r"severe (allergic )?reaction|chest pain",
    re.I,
)

EMERGENCY_MESSAGE = (
    "This sounds like an emergency. Please call 000 now, and use an adrenaline injector if one has been "
    "prescribed. When everyone is safe, I can report the product to our team (policy P7)."
)
OTHER_CUSTOMER_MESSAGE = (
    "I can only help with orders on the account you're signed in to, so I can't look up anyone else's "
    "order or details (policy P11). If they contact us from their own account, we'll gladly help."
)
INJECTION_MESSAGE = (
    "I can't change how I work or the store's rules, but I'm happy to help with products, your basket, "
    "orders or a problem with a delivery."
)


@dataclass
class InputCheck:
    allowed: bool
    flags: list[str] = field(default_factory=list)
    response: str | None = None


def check_input(text: str) -> InputCheck:
    if len(text) > MAX_INPUT_CHARS:
        return InputCheck(False, ["too_long"], f"Please keep messages under {MAX_INPUT_CHARS} characters.")
    if _EMERGENCY.search(text):
        return InputCheck(False, ["emergency"], EMERGENCY_MESSAGE)
    if _OTHER_CUSTOMER.search(text):
        return InputCheck(False, ["other_customer_data"], OTHER_CUSTOMER_MESSAGE)
    if _INJECTION.search(text):
        return InputCheck(False, ["prompt_injection"], INJECTION_MESSAGE)
    return InputCheck(True)


# ---------- output grounding ----------

_CODE = re.compile(r"\[\[(\d{6,14})\]\]")
_MONEY = re.compile(r"\$\s?(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)")
_POLICY = re.compile(r"\bP(\d{1,2})\b")
_ASSURANCE = re.compile(
    r"\b(safe for (you|your|someone|people|anyone|a|an|coeliacs?|celiacs?)|allergy[- ]safe|allergen[- ]free|"
    r"guarantee[sd]?|100% (free|safe)|completely (free|safe)|definitely (free|safe|doesn'?t contain|has no)|"
    r"no risk|can'?t (possibly )?contain)\b",
    re.I,
)


@dataclass
class OutputCheck:
    ok: bool
    problems: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def _amounts(text: str) -> set[str]:
    out = set()
    for m in _MONEY.finditer(text):
        v = float(m.group(1).replace(",", ""))
        out.add(f"{v:.2f}")
    return out


def check_output(reply: str, seen_codes: set[str], tool_text: str) -> OutputCheck:
    problems, flags = [], []
    for code in set(_CODE.findall(reply)):
        if code not in seen_codes:
            problems.append(f"[[{code}]] wasn't returned by a tool. Only cite products from tool results.")
            flags.append("ungrounded_product")
    known = _amounts(tool_text)
    for amt in sorted(_amounts(reply) - known):
        problems.append(f"${amt} doesn't appear in any tool result. Use the exact figures the tools returned.")
        flags.append("ungrounded_amount")
    valid = {s.id for s in policy()}
    for n in set(_POLICY.findall(reply)):
        if f"P{n}" not in valid:
            problems.append(f"P{n} isn't a policy section. Cite only sections returned by search_help.")
            flags.append("unknown_policy")
    m = _ASSURANCE.search(reply)
    if m:
        problems.append(
            f'"{m.group(0)}" is an allergy assurance we can\'t give. Report the label status (contains, may '
            "contain, not listed) and ask them to check the pack."
        )
        flags.append("unsafe_assurance")
    return OutputCheck(not problems, problems, sorted(set(flags)))


def scrub(reply: str, seen_codes: set[str]) -> str:
    """Last resort after a failed retry: drop citations to products the tools never returned."""
    return _CODE.sub(lambda m: m.group(0) if m.group(1) in seen_codes else "", reply)
