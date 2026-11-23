"""pytest 夹具：保证测试使用的样例文件存在且为最新。"""

import os
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from scripts.generate_samples import main as generate  # noqa: E402

SAMPLE_DIR = os.path.join(BACKEND, "samples")


@pytest.fixture(scope="session", autouse=True)
def ensure_samples():
    needed = [
        "continuous_plain.edf",
        "continuous_plusc.edf",
        "discontinuous_plusd.edf",
        "truncated_plusc.edf",
        "bad_calibration_plusc.edf",
    ]
    if not all(os.path.exists(os.path.join(SAMPLE_DIR, n)) for n in needed):
        generate()
    yield


@pytest.fixture(scope="session")
def sample_dir():
    return SAMPLE_DIR
