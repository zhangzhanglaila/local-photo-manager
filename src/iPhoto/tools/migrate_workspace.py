"""数据迁移工具：将现有的 .iPhoto 数据迁移到自定义工作目录。

使用方法:
    python -m iPhoto.tools.migrate_workspace <照片文件夹路径> <目标工作目录基础路径>

示例:
    python -m iPhoto.tools.migrate_workspace "D:/Photos" "C:/iPhotronWorkspace"
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from iPhoto.utils.pathutils import (
    ALL_WORK_DIR_NAMES,
    WORK_DIR_NAME,
    _generate_library_name,
)


def _calculate_size(path: Path) -> int:
    """计算目录或文件的大小（字节）。"""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def _format_size(bytes: int) -> str:
    """格式化字节大小为可读字符串。"""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes < 1024.0:
            return f"{bytes:.1f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.1f} TB"


def find_existing_work_dir(library_root: Path) -> Optional[Path]:
    """查找现有的工作目录。"""
    for name in ALL_WORK_DIR_NAMES:
        candidate = library_root / name
        if candidate.is_dir():
            return candidate
    return None


def migrate_workspace(
    library_root: Path,
    workspace_base: Path,
    *,
    dry_run: bool = False,
    verify: bool = True,
) -> bool:
    """将工作目录从照片文件夹迁移到自定义工作空间。

    Args:
        library_root: 照片库根目录
        workspace_base: 目标工作空间基础目录
        dry_run: 仅显示将要执行的操作，不实际移动文件
        verify: 迁移后验证文件完整性

    Returns:
        是否成功迁移
    """
    library_root = Path(library_root).expanduser().resolve()
    workspace_base = Path(workspace_base).expanduser().resolve()

    if not library_root.exists():
        print(f"❌ 错误：照片库不存在: {library_root}")
        return False

    # 查找现有工作目录
    source_dir = find_existing_work_dir(library_root)
    if source_dir is None:
        print(f"ℹ️  在 {library_root} 中未找到现有工作目录")
        print(f"    查找的目录名: {', '.join(ALL_WORK_DIR_NAMES)}")
        return False

    library_name = _generate_library_name(library_root)
    target_dir = workspace_base / library_name / WORK_DIR_NAME

    print("=" * 60)
    print("iPhotron 工作目录迁移工具")
    print("=" * 60)
    print(f"\n照片库: {library_root}")
    print(f"库名称: {library_name}")
    print(f"\n源目录: {source_dir}")
    print(f"目标目录: {target_dir}")

    # 计算大小
    source_size = _calculate_size(source_dir)
    print(f"\n数据大小: {_format_size(source_size)}")

    # 检查目标目录是否已存在
    if target_dir.exists():
        response = input(f"\n⚠️  目标目录已存在: {target_dir}\n是否覆盖？(y/N): ")
        if response.lower() != "y":
            print("❌ 迁移已取消")
            return False
        if dry_run:
            print(f"[DRY RUN] 将删除: {target_dir}")
        else:
            shutil.rmtree(target_dir)

    # 确认迁移
    if dry_run:
        print(f"\n[DRY RUN] 将执行以下操作:")
        print(f"  1. 创建目录: {target_dir.parent}")
        print(f"  2. 移动 {source_dir} -> {target_dir}")
        print(f"  3. 删除源目录")
    else:
        response = input(f"\n是否继续迁移？(y/N): ")
        if response.lower() != "y":
            print("❌ 迁移已取消")
            return False

    # 执行迁移
    print(f"\n📦 开始迁移...")

    try:
        # 创建目标父目录
        target_dir.parent.mkdir(parents=True, exist_ok=True)

        if dry_run:
            print(f"[DRY RUN] 移动: {source_dir} -> {target_dir}")
        else:
            # 移动目录
            shutil.move(str(source_dir), str(target_dir))
            print(f"✅ 已移动: {source_dir} -> {target_dir}")

        # 验证
        if verify and not dry_run:
            print(f"\n🔍 验证迁移结果...")
            if not target_dir.exists():
                print(f"❌ 错误：目标目录不存在")
                return False

            # 检查关键文件
            key_files = [
                target_dir / "global_index.db",
                target_dir / "links.json",
            ]
            missing = [f for f in key_files if not f.exists()]
            if missing:
                print(f"⚠️  警告：以下关键文件缺失:")
                for f in missing:
                    print(f"    - {f.name}")

            print(f"✅ 验证完成")

        # 更新设置
        if not dry_run:
            from iPhoto.settings.manager import SettingsManager, default_settings_path

            settings_path = default_settings_path()
            if settings_path.exists():
                print(f"\n⚙️  更新设置文件...")
                print(f"   请在设置中手动添加以下配置:")
                print(f"   \"workspace_base\": \"{workspace_base}\"")
            else:
                print(f"\n⚙️  设置文件不存在，创建示例配置...")
                settings_path.parent.mkdir(parents=True, exist_ok=True)
                settings_path.write_text(
                    f'{{"workspace_base": "{workspace_base}"}}',
                    encoding="utf-8",
                )
                print(f"✅ 已创建设置文件: {settings_path}")

        print(f"\n✅ 迁移完成!")
        print(f"\n📝 后续步骤:")
        print(f"   1. 确保设置文件中包含:")
        print(f'      "workspace_base": "{workspace_base}"')
        print(f"   2. 重启 iPhotron")
        print(f"   3. 验证照片浏览、缩略图、人脸等功能正常")

        return True

    except Exception as e:
        print(f"\n❌ 迁移失败: {e}")
        import traceback

        traceback.print_exc()
        return False


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) < 2:
        print(__doc__)
        print("\n使用方法:")
        print("  migrate_workspace <照片文件夹> <目标工作目录基础路径> [选项]")
        print("\n选项:")
        print("  --dry-run   仅显示将要执行的操作，不实际移动文件")
        print("  --no-verify 跳过迁移后的验证步骤")
        print("\n示例:")
        print('  migrate_workspace "D:/Photos" "C:/iPhotronWorkspace"')
        print('  migrate_workspace "D:/Photos" "C:/iPhotronWorkspace" --dry-run')
        return 1

    library_root = Path(argv[0])
    workspace_base = Path(argv[1])
    dry_run = "--dry-run" in argv
    verify = "--no-verify" not in argv

    success = migrate_workspace(
        library_root,
        workspace_base,
        dry_run=dry_run,
        verify=verify,
    )

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
