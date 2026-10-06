"""The soft decision points that use a fixed-answer scorer, with their options and wording.

Each is one bounded question with a stable id and a version. Change the wording or the options and
you change the version, because thresholds and eval results belong to one version of a decision.

Only soft semantic judgments are here. Allergies, diets, budget ceilings, and every other hard
constraint stay in plain code and are never decided by a score, and a scorer never reads or
weakens a stated requirement.
"""

from __future__ import annotations

from dataclasses import dataclass

from makan.providers.scoring import Option, Question


@dataclass(frozen=True)
class Decision:
    name: str
    version: str
    instructions: str
    options: tuple[Option, ...]

    def question(self, text: str) -> Question:
        return Question(self.name, self.version, self.instructions, text, self.options)


# Whether remembered taste could change the answer. Skipping the lookup is the risky direction, so
# the gate skips only on a clear margin and looks up on anything else.
RETRIEVAL_GATE = Decision(
    "retrieval_gate",
    "1",
    "Decide whether this message is about food, a place to eat, or what to eat, so that remembered "
    "tastes could change the answer. A bare greeting, thanks, or acknowledgement in any language "
    "has nothing to personalize.",
    (
        Option("personalize", "A request or comment about eating, food, or a place to eat."),
        Option("skip", "Only a greeting, thanks, or acknowledgement, with nothing to personalize."),
    ),
)

# The first soft attributes of a request. Each is its own decision with a not_stated option, so a
# request that says nothing about it is not forced into a band. A budget ceiling stated as a limit
# is a hard constraint, which stays in the requirements path and in plain code.
BUDGET_BAND = Decision(
    "budget_band",
    "1",
    "Decide which price feel the request is asking for. Use not_stated when the text does not say.",
    (
        Option("cheap", "Asks for cheap, budget, or affordable food."),
        Option("moderate", "Asks for an ordinary mid-priced meal."),
        Option("splurge", "Asks for upscale, special-occasion, or fine dining."),
        Option("not_stated", "Says nothing about price."),
    ),
)

MEAL_PERIOD = Decision(
    "meal_period",
    "1",
    "Decide which meal the request is for. Use not_stated when the text does not say.",
    (
        Option("breakfast", "Asks for breakfast or a morning meal."),
        Option("lunch", "Asks for lunch or a midday meal."),
        Option("dinner", "Asks for dinner or an evening meal."),
        Option("late_night", "Asks for supper or food late at night."),
        Option("not_stated", "Says nothing about the meal or time of day."),
    ),
)

SOFT_ATTRIBUTES = (BUDGET_BAND, MEAL_PERIOD)
# What the "nothing stated" answer is called, which never counts as a signal.
NOT_STATED = "not_stated"
DECISIONS = {d.name: d for d in (RETRIEVAL_GATE, *SOFT_ATTRIBUTES)}
