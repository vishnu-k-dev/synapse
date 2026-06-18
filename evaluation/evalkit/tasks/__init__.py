"""Task suites + the success-oracle schema. Suites are part of the benchmark artifact."""

from evalkit.tasks.schema import Ref, StateAssertion, Task, load_suite

__all__ = ["Ref", "StateAssertion", "Task", "load_suite"]
