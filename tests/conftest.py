"""
conftest.py — Shared pytest fixtures.

The most important fixture here is mock_mlflow, which patches out all
MLflow calls for every test. This means tests never need Databricks
credentials or a running MLflow server.

How pytest fixtures work:
- A fixture is a function decorated with @pytest.fixture.
- Any test function (or other fixture) that names the fixture as a
  parameter automatically receives its return/yield value.
- scope="session" means the fixture runs once for the entire test run.
- autouse=True means it runs automatically without being requested.
"""

import pytest
from unittest.mock import patch


@pytest.fixture(scope="session", autouse=True)
def mock_mlflow():
    """
    Patches all MLflow calls globally so no Databricks connection is needed.

    mlflow.start_run() is used as a context manager in app.py:
        with mlflow.start_run(...):
            ...
    MagicMock (the default mock object) automatically supports __enter__
    and __exit__, so the 'with' block works without any real MLflow server.
    """
    with (
        patch("mlflow.set_tracking_uri"),
        patch("mlflow.set_experiment"),
        patch("mlflow.log_param"),
        patch("mlflow.log_params"),
        patch("mlflow.log_metrics"),
        patch("mlflow.log_metric"),
        patch("mlflow.start_run"),
        patch("mlflow.crewai.autolog"),
    ):
        yield
