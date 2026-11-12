import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.api.models import MAX_UPLOAD_BYTES, _allowed_member, _unpack_package
from app.main import app


class ModelPackageTests(unittest.TestCase):
    def test_only_expected_model_package_files_are_allowed(self):
        self.assertTrue(_allowed_member("model.onnx"))
        self.assertTrue(_allowed_member("manifest.json"))
        for name in ("../model.onnx", "/tmp/model.onnx", "predict.py", "Dockerfile", "a/model.onnx"):
            self.assertFalse(_allowed_member(name), name)

    def test_upload_rejects_path_traversal_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            target = root / "package"
            target.mkdir()
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../manifest.json", "{}")
            with self.assertRaisesRegex(ValueError, "路径或文件类型"):
                _unpack_package(archive, target)

            archive2 = root / "link.zip"
            with zipfile.ZipFile(archive2, "w") as bundle:
                entry = zipfile.ZipInfo("manifest.json")
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                bundle.writestr(entry, "../../outside")
            with self.assertRaisesRegex(ValueError, "路径或文件类型"):
                _unpack_package(archive2, target)

    def test_admin_model_endpoints_exist_and_are_dependency_guarded(self):
        paths = app.openapi()["paths"]
        self.assertIn("/api/admin/models", paths)
        self.assertIn("/api/admin/models/vision4/upload", paths)
        route = next(route for route in app.routes if getattr(route, "path", None) == "/api/admin/models/vision4/upload")
        self.assertTrue(route.dependencies)
        self.assertEqual(MAX_UPLOAD_BYTES, 100 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
