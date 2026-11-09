"""robots.txt read the way RFC 9309 says: groups by token, longest match wins, allow wins ties."""

from makan.places.robots import DISALLOW_ALL, RobotsRules, parse_robots


def test_no_rules_allow_everything() -> None:
    assert RobotsRules().allows("MakanBot", "/menu")
    assert parse_robots("").allows("MakanBot", "/menu")
    assert parse_robots("Sitemap: https://venue.example/sitemap.xml").allows("MakanBot", "/")


def test_the_star_group_applies_when_no_group_names_the_agent() -> None:
    rules = parse_robots("User-agent: *\nDisallow: /private\n")
    assert not rules.allows("MakanBot", "/private/menu")
    assert rules.allows("MakanBot", "/menu")


def test_a_group_naming_the_agent_replaces_the_star_group() -> None:
    rules = parse_robots(
        "User-agent: *\nDisallow: /\n\nUser-agent: makanbot\nAllow: /menu\nDisallow: /admin\n"
    )
    assert rules.allows("MakanBot", "/menu")
    assert rules.allows("MakanBot", "/about")  # nothing in its own group says no
    assert not rules.allows("MakanBot", "/admin")
    assert not rules.allows("OtherBot", "/menu")


def test_consecutive_agents_share_a_group_and_repeated_groups_merge() -> None:
    rules = parse_robots(
        "User-agent: GPTBot\nUser-agent: CCBot\nDisallow: /\n\nUser-agent: CCBot\nAllow: /open\n"
    )
    assert not rules.allows("GPTBot", "/menu")
    assert not rules.allows("ccbot", "/menu")
    assert rules.allows("CCBot", "/open/menu")
    assert rules.allows("MakanBot", "/menu")


def test_the_longest_match_wins_and_allow_wins_a_tie() -> None:
    rules = parse_robots(
        "User-agent: *\nDisallow: /menu\nAllow: /menu/dinner\nDisallow: /a\nAllow: /a\n"
    )
    assert not rules.allows("MakanBot", "/menu/lunch")
    assert rules.allows("MakanBot", "/menu/dinner")
    assert rules.allows("MakanBot", "/a")


def test_wildcards_and_end_anchors() -> None:
    rules = parse_robots("User-agent: *\nDisallow: /*.pdf$\nDisallow: /*?order=\n")
    assert not rules.allows("MakanBot", "/files/menu.pdf")
    assert rules.allows("MakanBot", "/files/menu.pdf.html")
    assert not rules.allows("MakanBot", "/menu?order=1")
    assert rules.allows("MakanBot", "/menu")


def test_comments_blank_values_and_stray_rules_are_ignored() -> None:
    rules = parse_robots(
        "Disallow: /before-any-agent\n# a comment\nUser-agent: * # everyone\nDisallow:\n"
    )
    assert rules.allows("MakanBot", "/before-any-agent")
    assert rules.allows("MakanBot", "/menu")


def test_robots_txt_itself_is_always_allowed() -> None:
    assert DISALLOW_ALL.allows("MakanBot", "/robots.txt")
    assert not DISALLOW_ALL.allows("MakanBot", "/")
