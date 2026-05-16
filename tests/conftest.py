"""
Shared pytest configuration for SmartApplyAI test suite.
"""
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--extension-path",
        action="store",
        default="./extension",
        help="Path to unpacked Chrome extension directory",
    )
    parser.addoption(
        "--backend-url",
        action="store",
        default="http://127.0.0.1:5001",
        help="Backend FastAPI URL",
    )


@pytest.fixture(scope="session")
def extension_path(request):
    return request.config.getoption("--extension-path")


@pytest.fixture(scope="session")
def backend_url(request):
    return request.config.getoption("--backend-url")
