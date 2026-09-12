"""Strict admission control for a synthetic offline agent budget.

Every monetary quantity is an integer count of micro-USD and every conversion uses
ceiling division, so no rounding path can admit more than the declared ceiling. There are
no floats in this module for exactly that reason.

Nothing here is a billing guarantee. `estimate_prompt_tokens` is a local character
heuristic, not a provider tokenizer, and in this slice no request leaves the host, so
`actual_charged_micros` is always zero while the simulated reported counts come from a
recorded fixture cassette.
"""

import json
import threading

MICROS_PER_USD = 1_000_000
TOKENS_PER_MTOK = 1_000_000
CHARS_PER_TOKEN_ESTIMATE = 4
PER_MESSAGE_OVERHEAD_TOKENS = 8

SCOPE = ("Synthetic offline ledger. Simulated reported token counts come from a recorded "
         "fixture cassette and are priced with the configuration's declared table. No "
         "provider request was made, so the actual charge is zero. A prompt estimate is a "
         "local character heuristic, not a provider tokenizer, and is not a guarantee "
         "about any real provider's billed cost.")


class BudgetError(RuntimeError):
    pass


class BudgetRefused(BudgetError):
    pass


def cost_micros(tokens, micros_per_mtok):
    if tokens < 0 or micros_per_mtok < 0:
        raise BudgetError("Token counts and prices must be non-negative integers")
    return -(-(tokens * micros_per_mtok) // TOKENS_PER_MTOK)


def estimate_prompt_tokens(messages):
    encoded = json.dumps(messages, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return -(-len(encoded) // CHARS_PER_TOKEN_ESTIMATE) + PER_MESSAGE_OVERHEAD_TOKENS * len(messages)


class Ledger:
    """One per run, shared across profile workers, so concurrency cannot over-admit."""

    def __init__(self, budget, prices):
        self.budget = budget
        self.prices = prices
        self._guard = threading.Lock()
        self.calls = 0
        self.steps = 0
        # Ceilings are enforced against the committed counters, which rise at admission and
        # are reconciled to the observed figures at settlement. Checking observed counters
        # alone would let a second worker admit against stale totals, and would lose the
        # commitment entirely whenever a call failed before it could report usage.
        self.committed_input_tokens = 0
        self.committed_output_tokens = 0
        self.committed_micros = 0
        self.committed_attempts = 0
        self.attempts = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cache_tokens = 0
        self.reported_micros = 0
        self.refusals = []
        self.accounting_errors = []
        self.outstanding = 0

    def _price(self, model):
        price = self.prices.get(model)
        if price is None:
            raise BudgetError(f"No declared price for model {model!r}; refusing to admit a call")
        return price

    def _refuse(self, reason, detail):
        record = {"reason": reason, "detail": detail}
        self.refusals.append(record)
        raise BudgetRefused(f"{reason}: {detail}")

    def admit_call(self, model, messages, max_output_tokens, max_attempts):
        """Reserve the worst case for every ceiling before the provider is contacted.

        Reserving `max_attempts` per call is deliberately conservative: a call that would
        have needed one attempt still holds the full per-call attempt allowance until it
        settles. That direction is the safe one for a ceiling, and it is the only bound
        available before the provider has answered.
        """
        price = self._price(model)
        estimate = estimate_prompt_tokens(messages)
        worst = (cost_micros(estimate, price["input_micros_per_mtok"])
                 + cost_micros(max_output_tokens, price["output_micros_per_mtok"]))
        with self._guard:
            if self.calls + 1 > self.budget["max_calls"]:
                self._refuse("call_limit", f"{self.calls} calls already admitted of "
                                           f"{self.budget['max_calls']}")
            if self.committed_attempts + max_attempts > self.budget["max_attempts"]:
                self._refuse("attempt_limit",
                             f"reserving {max_attempts} attempts on top of "
                             f"{self.committed_attempts} would exceed "
                             f"{self.budget['max_attempts']}")
            if self.committed_input_tokens + estimate > self.budget["max_input_tokens"]:
                self._refuse("input_token_limit",
                             f"estimated {estimate} prompt tokens on top of "
                             f"{self.committed_input_tokens} would exceed "
                             f"{self.budget['max_input_tokens']}")
            if self.committed_output_tokens + max_output_tokens > self.budget["max_output_tokens"]:
                self._refuse("output_token_limit",
                             f"a worst-case {max_output_tokens} output tokens on top of "
                             f"{self.committed_output_tokens} would exceed "
                             f"{self.budget['max_output_tokens']}")
            if self.committed_micros + worst > self.budget["max_cost_micros"]:
                self._refuse("cost_limit",
                             f"worst-case {worst} micro-USD on top of {self.committed_micros} "
                             f"would exceed {self.budget['max_cost_micros']}")
            self.calls += 1
            self.outstanding += 1
            self.committed_input_tokens += estimate
            self.committed_output_tokens += max_output_tokens
            self.committed_micros += worst
            self.committed_attempts += max_attempts
            return {"estimated_prompt_tokens": estimate, "max_output_tokens": max_output_tokens,
                    "worst_case_micros": worst, "reserved_attempts": max_attempts,
                    "call_index": self.calls}

    def _settle(self, reservation, attempts, usage, reported):
        """Replace a reservation with what was observed. Observed evidence is never dropped."""
        self.outstanding -= 1
        self.committed_attempts += attempts - reservation["reserved_attempts"]
        self.attempts += attempts
        observations = [("attempts", attempts, reservation["reserved_attempts"])]
        if usage is None:
            self.committed_input_tokens -= reservation["estimated_prompt_tokens"]
            self.committed_output_tokens -= reservation["max_output_tokens"]
            self.committed_micros -= reservation["worst_case_micros"]
        else:
            self.committed_input_tokens += usage.prompt_tokens - reservation["estimated_prompt_tokens"]
            self.committed_output_tokens += usage.completion_tokens - reservation["max_output_tokens"]
            self.committed_micros += reported - reservation["worst_case_micros"]
            self.prompt_tokens += usage.prompt_tokens
            self.completion_tokens += usage.completion_tokens
            self.cache_tokens += usage.cache_tokens
            self.reported_micros += reported
            observations += [
                ("prompt_tokens", usage.prompt_tokens, reservation["estimated_prompt_tokens"]),
                ("completion_tokens", usage.completion_tokens, reservation["max_output_tokens"]),
                ("cost_micros", reported, reservation["worst_case_micros"])]
        overruns = [f"{name}: recorded {observed} above the reserved {reserved}"
                    for name, observed, reserved in observations if observed > reserved]
        for name, committed, ceiling in (
                ("input_tokens", self.committed_input_tokens, self.budget["max_input_tokens"]),
                ("output_tokens", self.committed_output_tokens, self.budget["max_output_tokens"]),
                ("cost_micros", self.committed_micros, self.budget["max_cost_micros"]),
                ("attempts", self.committed_attempts, self.budget["max_attempts"])):
            if committed > ceiling:
                overruns.append(f"{name}: committed {committed} now exceeds the ceiling {ceiling}")
        if not overruns:
            return None
        error = {"call_index": reservation["call_index"], "overruns": overruns,
                 "note": ("Recorded usage exceeded its reservation. The usage above is retained "
                          "as observed evidence; the run stops spending rather than discarding it.")}
        self.accounting_errors.append(error)
        return error

    def commit_call(self, model, usage, attempts, reservation):
        price = self._price(model)
        reported = (cost_micros(usage.prompt_tokens, price["input_micros_per_mtok"])
                    + cost_micros(usage.completion_tokens, price["output_micros_per_mtok"]))
        with self._guard:
            error = self._settle(reservation, attempts, usage, reported)
        return {"simulated_reported_micros": reported, "price_source": "declared_config_table",
                "attempts": attempts, "accounting_error": error, **usage.data()}

    def commit_failed_call(self, attempts, reservation):
        """A call that produced attempts but no usage still consumes its attempt allowance.

        The settlement verdict is returned rather than assumed: more attempts than were
        reserved is an accounting error on this path too, and the step trace must agree
        with the ledger instead of reporting a hardcoded absence of error.
        """
        with self._guard:
            error = self._settle(reservation, attempts, None, 0)
        return {"attempts": attempts, "simulated_reported_micros": 0,
                "price_source": "declared_config_table", "accounting_error": error,
                "prompt_tokens": 0, "completion_tokens": 0, "cache_tokens": 0}

    def admit_step(self):
        with self._guard:
            if self.steps + 1 > self.budget["max_tool_steps"]:
                self._refuse("tool_step_limit",
                             f"{self.steps} tool containers already admitted of "
                             f"{self.budget['max_tool_steps']}")
            self.steps += 1
            return {"step_index": self.steps}

    def data(self):
        with self._guard:
            return {"schema_version": 1, "declared": dict(self.budget),
                    "prices": {model: dict(price) for model, price in self.prices.items()},
                    "calls_admitted": self.calls, "provider_attempts": self.attempts,
                    "tool_steps_admitted": self.steps,
                    "simulated_reported": {"prompt_tokens": self.prompt_tokens,
                                           "completion_tokens": self.completion_tokens,
                                           "cache_tokens": self.cache_tokens,
                                           "cost_micros": self.reported_micros},
                    "committed": {"input_tokens": self.committed_input_tokens,
                                  "output_tokens": self.committed_output_tokens,
                                  "cost_micros": self.committed_micros,
                                  "attempts": self.committed_attempts,
                                  "calls_outstanding": self.outstanding},
                    "actual_charged_micros": 0, "provider_requests_sent": 0,
                    "refusals": list(self.refusals),
                    "accounting_errors": list(self.accounting_errors),
                    "reconciliation": (
                        "Committed figures are the live commitment: a worst-case reservation for "
                        "every call still in flight plus the observed figures for every settled "
                        "call. They are what the ceilings are enforced against, never the observed "
                        "totals. Because a settled call replaces its reservation, "
                        "committed.cost_micros equals the observed simulated cost once no call is "
                        "outstanding; it is therefore not a record of peak worst-case admission, "
                        "and it is not a spend figure. Nothing was spent: see "
                        "actual_charged_micros and provider_requests_sent."),
                    "scope": SCOPE}


def admit_plan(experiment, batches, budget, prices):
    """Refuse an unaffordable plan before the run directory or any container exists."""
    planned_calls = 0
    planned_steps = 0
    planned_attempts = 0
    planned_output_tokens = 0
    for batch in batches:
        for entry in batch["trials"]:
            task = next(t for t in experiment.tasks if t.id == entry["task"])
            if task.agent is None:
                continue
            planned_calls += task.agent.max_steps
            planned_steps += task.agent.max_steps
            planned_attempts += task.agent.max_steps * task.agent.max_attempts
            planned_output_tokens += (task.agent.max_steps
                                      * task.agent.parameters["max_output_tokens"])
    refusals = []
    for planned, ceiling, label in (
            (planned_calls, budget["max_calls"], "provider calls"),
            (planned_steps, budget["max_tool_steps"], "tool containers"),
            (planned_attempts, budget["max_attempts"], "provider attempts"),
            (planned_output_tokens, budget["max_output_tokens"], "worst-case output tokens")):
        if planned > ceiling:
            refusals.append(f"the plan can require {planned} {label} but the budget "
                            f"allows {ceiling}")
    for task in experiment.tasks:
        if task.agent is None:
            continue
        price = prices.get(task.agent.model)
        if price is None:
            refusals.append(f"model {task.agent.model!r} has no declared price")
            continue
        floor = (cost_micros(1, price["input_micros_per_mtok"])
                 + cost_micros(task.agent.parameters["max_output_tokens"],
                               price["output_micros_per_mtok"]))
        if floor > budget["max_cost_micros"]:
            refusals.append(f"a single minimal call for {task.agent.model!r} costs at least "
                            f"{floor} micro-USD, above the {budget['max_cost_micros']} ceiling")
    if refusals:
        raise BudgetRefused("Refusing to start: " + "; ".join(refusals))
    return {"planned_provider_calls": planned_calls, "planned_tool_containers": planned_steps,
            "planned_provider_attempts": planned_attempts,
            "planned_worst_case_output_tokens": planned_output_tokens}
