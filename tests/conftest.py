"""Shared pytest fixtures for the W2 tests.

``w2_router`` is a private respx router: every request of the test must match a route
registered on it (unmatched requests raise), so a W2 test can never reach a real server.
"""

import pytest
import respx


@pytest.fixture
def w2_router():
    """Active respx router with ``assert_all_called=False`` (alias routes are optional for the dev)."""
    with respx.MockRouter(assert_all_called=False) as router:
        yield router
