import stat
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.api.models import MAX_UPLOAD_BYTES, _allowed_member, _unpack_package
from app.api import models
from app.main import app


class ModelPackageTests(unittest.TestCase):
    def test_rollback_version_requires_managed_directory_and_matching_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "versions"
            model_dir = store / "baseline"
            model_dir.mkdir(parents=True)
            model_file = model_dir / "model.onnx"
            model_file.write_bytes(b"baseline-model")
            model_hash = hashlib.sha256(model_file.read_bytes()).hexdigest()
            (model_dir / "manifest.json").write_text(json.dumps({
                "model_version": "baseline-v1", "model_sha256": model_hash,
            }))

            original_store = models._store_dir
            models._store_dir = lambda: store
            try:
                self.assertEqual(
                    models._validate_managed_version(model_dir, "baseline-v1", model_hash),
                    model_dir,
                )
                with self.assertRaisesRegex(ValueError, "不匹配"):
                    models._validate_managed_version(model_dir, "baseline-v1", "0" * 64)
                with self.assertRaisesRegex(ValueError, "受控版本目录"):
                    models._validate_managed_version(root, "baseline-v1", model_hash)
            finally:
                models._store_dir = original_store

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
        # FastAPI stores included routers lazily; inspect the router-level
        # admin dependency instead of relying on eager app.routes expansion.
        self.assertTrue(models.router.dependencies)
        self.assertEqual(MAX_UPLOAD_BYTES, 100 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
