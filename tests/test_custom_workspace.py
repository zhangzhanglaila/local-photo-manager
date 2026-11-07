"""测试自定义工作目录功能。"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

from iPhoto.settings.schema import merge_with_defaults, DEFAULT_SETTINGS
from iPhoto.utils.pathutils import (
    set_custom_workspace_base,
    get_custom_workspace_base,
    get_custom_workspace_dir,
    ensure_work_dir,
    resolve_work_dir,
    _generate_library_name,
)
from iPhoto.config import WORK_DIR_NAME


class TestCustomWorkspace(TestCase):
    """测试自定义工作目录功能。"""

    def setUp(self):
        """设置测试环境。"""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        # 清除任何已设置的自定义工作目录
        set_custom_workspace_base(None)

    def tearDown(self):
        """清理测试环境。"""
        set_custom_workspace_base(None)
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def test_custom_workspace_base_setting(self):
        """测试自定义工作目录基础路径设置。"""
        # 初始状态应该没有自定义工作目录
        self.assertIsNone(get_custom_workspace_base())

        # 设置自定义工作目录
        custom_base = self.temp_path / "workspace"
        set_custom_workspace_base(custom_base)
        self.assertEqual(get_custom_workspace_base(), custom_base)

    def test_library_name_generation(self):
        """测试照片库名称生成。"""
        # 正常名称
        library = self.temp_path / "Photos"
        name = _generate_library_name(library)
        self.assertEqual(name, "Photos")

        # 带特殊字符的名称应该使用哈希
        library_special = self.temp_path / "Photos (2024)"
        name_special = _generate_library_name(library_special)
        self.assertNotEqual(name_special, "Photos (2024)")
        self.assertEqual(len(name_special), 16)  # MD5 hash prefix

    def test_custom_workspace_dir_generation(self):
        """测试自定义工作目录路径生成。"""
        library_root = self.temp_path / "Photos"
        custom_base = self.temp_path / "workspace"

        # 未设置自定义工作目录时应该返回 None
        self.assertIsNone(get_custom_workspace_dir(library_root))

        # 设置自定义工作目录后应该返回正确路径
        set_custom_workspace_base(custom_base)
        workspace_dir = get_custom_workspace_dir(library_root)
        self.assertIsNotNone(workspace_dir)
        self.assertEqual(workspace_dir.parent.name, "workspace")
        # The work directory is <workspace>/<library> — one level, not two.
        self.assertEqual(workspace_dir.name, "Photos")
        self.assertEqual(workspace_dir.name, "Photos")

    def test_ensure_work_dir_with_custom_workspace(self):
        """测试使用自定义工作目录的 ensure_work_dir。"""
        library_root = self.temp_path / "Photos"
        custom_base = self.temp_path / "workspace"

        # 未设置自定义工作目录时，应该使用照片文件夹内的传统位置
        work_dir = ensure_work_dir(library_root)
        self.assertEqual(work_dir.parent, library_root)
        self.assertEqual(work_dir.name, WORK_DIR_NAME)

        # 设置自定义工作目录后，应该使用自定义位置
        set_custom_workspace_base(custom_base)
        custom_work_dir = ensure_work_dir(library_root)
        self.assertTrue(custom_work_dir.is_relative_to(custom_base))
        self.assertEqual(custom_work_dir.name, "Photos")
        self.assertTrue(custom_work_dir.exists())

    def test_resolve_work_dir_with_existing_custom_dir(self):
        """测试解析已存在的自定义工作目录。"""
        library_root = self.temp_path / "Photos"
        custom_base = self.temp_path / "workspace"

        # 设置自定义工作目录并创建工作目录
        set_custom_workspace_base(custom_base)
        custom_work_dir = ensure_work_dir(library_root)
        self.assertTrue(custom_work_dir.exists())

        # resolve_work_dir 应该返回自定义工作目录
        resolved = resolve_work_dir(library_root)
        self.assertEqual(resolved, custom_work_dir)

    def test_resolve_work_dir_fallback_to_traditional(self):
        """测试回退到传统工作目录。"""
        library_root = self.temp_path / "Photos"

        # 创建传统工作目录
        traditional_dir = library_root / WORK_DIR_NAME
        traditional_dir.mkdir(parents=True)
        self.assertTrue(traditional_dir.exists())

        # resolve_work_dir 应该返回传统工作目录
        resolved = resolve_work_dir(library_root)
        self.assertEqual(resolved, traditional_dir)

    def test_settings_schema_includes_workspace_base(self):
        """测试设置 schema 包含 workspace_base 字段。"""
        settings = merge_with_defaults({
            "workspace_base": str(self.temp_path / "workspace")
        })
        self.assertIn("workspace_base", settings)
        self.assertEqual(settings["workspace_base"], str(self.temp_path / "workspace"))

    def test_multiple_libraries_separation(self):
        """测试多个照片库的隔离。"""
        custom_base = self.temp_path / "workspace"
        set_custom_workspace_base(custom_base)

        # 创建多个照片库
        library1 = self.temp_path / "Photos1"
        library2 = self.temp_path / "Photos2"
        library1.mkdir()
        library2.mkdir()

        # 获取各自的工作目录
        workspace1 = get_custom_workspace_dir(library1)
        workspace2 = get_custom_workspace_dir(library2)

        # 验证它们是不同的目录
        self.assertNotEqual(workspace1, workspace2)
        self.assertEqual(workspace1.name, "Photos1")
        self.assertEqual(workspace2.name, "Photos2")


if __name__ == "__main__":
    import unittest
    unittest.main()
