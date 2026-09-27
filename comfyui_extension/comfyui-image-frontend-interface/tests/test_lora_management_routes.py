"""The browser publisher lease must not weaken the secret-only management API."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = types.ModuleType("cif_management_routes_under_test")
PACKAGE.__path__ = [str(ROOT)]
sys.modules[PACKAGE.__name__] = PACKAGE


class Routes:
    def __init__(self):
        self.paths = []

    def post(self, path):
        self.paths.append(("POST", path))
        return lambda handler: handler

    def get(self, path):
        self.paths.append(("GET", path))
        return lambda handler: handler

    def put(self, path):
        self.paths.append(("PUT", path))
        return lambda handler: handler


REGISTERED = Routes()
FOLDER_PATHS = types.ModuleType("folder_paths")
WEB = types.SimpleNamespace(Request=object, Response=object)
AIOHTTP = types.ModuleType("aiohttp")
AIOHTTP.web = WEB
SERVER = types.ModuleType("server")
SERVER.PromptServer = types.SimpleNamespace(
    instance=types.SimpleNamespace(routes=REGISTERED, prompt_queue=None)
)
SPEC = importlib.util.spec_from_file_location(
    PACKAGE.__name__ + ".lora_management_routes", ROOT / "lora_management_routes.py"
)
routes = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
with patch.dict(
    sys.modules,
    {"folder_paths": FOLDER_PATHS, "aiohttp": AIOHTTP, "server": SERVER},
):
    SPEC.loader.exec_module(routes)


def request(headers: dict[str, str], host: str = "comfy.example"):
    return types.SimpleNamespace(headers=headers, host=host)


class RouteBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.models = root / "loras"
        self.models.mkdir()
        self.userdata = root / "userdata"
        (self.userdata / "default").mkdir(parents=True)
        FOLDER_PATHS.get_folder_paths = lambda _: [str(self.models)]
        FOLDER_PATHS.get_user_directory = lambda: str(self.userdata)
        self.environment = patch.dict(
            os.environ,
            {
                "CIF_LORA_MANAGEMENT_SECRET": "s" * 32,
                "CIF_LORA_MANAGEMENT_ROOT": str(self.models),
                "CIF_LORA_MANAGEMENT_USER": "default",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_lease_routes_are_outside_the_management_namespace(self):
        lease_paths = [path for _, path in REGISTERED.paths if "lock" in path]
        self.assertEqual(
            lease_paths,
            [
                "/cif/publisher/lock/acquire",
                "/cif/publisher/lock/renew",
                "/cif/publisher/lock/release",
            ],
        )
        self.assertTrue(
            all(
                path.startswith("/cif/lora-management")
                for _, path in REGISTERED.paths
                if path not in lease_paths
            )
        )

    def test_management_route_rejects_browser_lease_header_without_secret(self):
        browser = request(
            {
                "X-CIF-Publisher-Lease": "1",
                "Sec-Fetch-Site": "same-origin",
                "Origin": "https://comfy.example",
            }
        )
        with self.assertRaisesRegex(routes.ManagementError, "authentication failed"):
            routes._service(browser)
        self.assertIsNotNone(routes._service(request({"X-CIF-Management-Token": "s" * 32})))

    def test_publisher_route_requires_same_origin_browser_metadata(self):
        valid = {
            "X-CIF-Publisher-Lease": "1",
            "Sec-Fetch-Site": "same-origin",
            "Origin": "https://comfy.example",
        }
        self.assertIsNotNone(routes._service(request(valid), publisher=True))
        invalid = [
            {"X-CIF-Publisher-Lease": "1"},
            {**valid, "Sec-Fetch-Site": "cross-site"},
            {**valid, "Origin": "https://other.example"},
            {**valid, "Origin": "https://comfy.example.evil.invalid"},
            {**valid, "Origin": "https://comfy.example/extra"},
        ]
        for headers in invalid:
            with self.subTest(headers=headers), self.assertRaises(routes.ManagementError):
                routes._service(request(headers), publisher=True)


if __name__ == "__main__":
    unittest.main()
