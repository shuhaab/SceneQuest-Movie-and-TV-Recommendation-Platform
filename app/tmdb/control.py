"""Shared deadline and HTTP attempt budget for one application request."""

from time import monotonic

from app.tmdb.errors import TMDBError


class RequestBudget:
    def __init__(self, attempts, seconds=8):
        self.deadline = monotonic() + seconds
        self.limit = attempts
        self.attempts = 0

    def remaining(self):
        return max(0, self.deadline - monotonic())

    def check(self):
        if self.remaining() <= 0.05 or self.attempts >= self.limit:
            raise TMDBError("WORK_LIMIT_REACHED", "The request work budget was reached.")

    def consume(self):
        self.check()
        self.attempts += 1
