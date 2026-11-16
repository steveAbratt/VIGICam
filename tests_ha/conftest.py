"""Home Assistant tests (need pytest-homeassistant-custom-component).

Run from the repo root:  pytest tests_ha -p homeassistant
Kept apart from tests/, which runs without Home Assistant in CI.
"""
import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
