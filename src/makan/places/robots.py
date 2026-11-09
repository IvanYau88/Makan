"""robots.txt rules, read the way RFC 9309 says, for the one page a menu read asks for.

Python's `urllib.robotparser` matches the first rule rather than the longest and ignores `*` and
`$` in paths, so a site's real rules could be misread. This is the small part of RFC 9309 a single
page fetch needs: groups by product token, longest match wins, `allow` wins a tie, and `*` and `$`
in paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_PATTERN_CHARS = 2048


@dataclass(frozen=True)
class RobotsRules:
    """The groups of one robots.txt: each a set of lowercase product tokens and its rules."""

    groups: tuple[tuple[frozenset[str], tuple[tuple[bool, str], ...]], ...] = ()

    def names(self, agent: str) -> bool:
        """Whether a group names the crawler `agent` itself, not only through `*`."""
        token = agent.lower()
        return any(token in agents for agents, _ in self.groups)

    def allows(self, agent: str, path: str) -> bool:
        """Whether the crawler named `agent` may fetch `path` (the path and query of a URL)."""
        if path == "/robots.txt":
            return True
        token = agent.lower()
        rules = [rule for agents, group in self.groups if token in agents for rule in group]
        if not self.names(token):
            rules = [rule for agents, group in self.groups if "*" in agents for rule in group]
        best: tuple[int, bool] | None = None
        for allow, pattern in rules:
            if not _matches(pattern, path):
                continue
            candidate = (len(pattern), allow)
            if best is None or candidate > best:  # longer wins, and allow (True) wins a tie
                best = candidate
        return True if best is None else best[1]


def parse_robots(text: str) -> RobotsRules:
    """Read robots.txt text. Unknown lines, such as `sitemap`, are skipped, as the RFC says."""
    groups: list[tuple[set[str], list[tuple[bool, str]]]] = []
    agents: set[str] | None = None
    rules: list[tuple[bool, str]] = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if agents is None or in_rules:
                agents, rules, in_rules = set(), [], False
                groups.append((agents, rules))
            agents.add(value.lower())
        elif key in ("allow", "disallow") and agents is not None:
            in_rules = True
            if value and len(value) <= MAX_PATTERN_CHARS:
                rules.append((key == "allow", value))
    return RobotsRules(tuple((frozenset(a), tuple(r)) for a, r in groups))


DISALLOW_ALL = RobotsRules(((frozenset({"*"}), ((False, "/"),)),))


def _matches(pattern: str, path: str) -> bool:
    return _compile(pattern).match(path) is not None


_compiled: dict[str, re.Pattern[str]] = {}


def _compile(pattern: str) -> re.Pattern[str]:
    found = _compiled.get(pattern)
    if found is None:
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        regex = ".*".join(re.escape(part) for part in body.split("*"))
        found = re.compile(regex + ("$" if anchored else ""))
        if len(_compiled) < 4096:
            _compiled[pattern] = found
    return found
