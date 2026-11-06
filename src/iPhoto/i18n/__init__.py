"""Internationalisation support for the iPhoto GUI."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from ..settings.manager import SettingsManager

_logger = logging.getLogger(__name__)

_current: str = "zh"

# ---------------------------------------------------------------------------
# Translation tables
# ---------------------------------------------------------------------------

_ZH: dict[str, str] = {
    # -- Menu: File --
    "menu.file": "&File",
    "menu.view": "&View",
    "menu.settings": "&Settings",
    "action.open_album": "打开相册文件夹…",
    "action.rescan": "重新扫描",
    "action.rebuild_links": "重建实况链接",
    "action.set_basic_library": "设置基础图库…",
    "action.download_map_ext": "下载地图扩展…",
    "action.export_all_edited": "导出所有已编辑",
    "action.export_selected": "导出所选",

    # -- Menu: View --
    "action.show_filmstrip": "显示胶片条",
    "action.show_face_names": "显示人脸名称",
    "action.show_hidden_people": "显示隐藏人物",

    # -- Menu: Settings --
    "menu.appearance": "外观",
    "menu.export_destination": "导出目标",
    "menu.export_format": "导出格式",
    "menu.wheel_action": "滚轮操作",
    "menu.share_action": "分享操作",
    "menu.language": "语言",

    # -- Theme --
    "action.system_default": "跟随系统",
    "action.light_mode": "浅色模式",
    "action.dark_mode": "深色模式",

    # -- Share actions --
    "action.copy_file": "复制文件",
    "action.copy_path": "复制路径",
    "action.reveal_file": "在文件管理器中显示",

    # -- Wheel actions --
    "action.navigate": "导航",
    "action.zoom": "缩放",

    # -- Export destination --
    "action.export_to_library": "基础图库",
    "action.export_ask": "每次询问",

    # -- Language --
    "action.lang_zh": "中文",
    "action.lang_en": "English",

    # -- Sidebar --
    "sidebar.basic_library": "基础图库",
    "sidebar.basic_library_unbound": "基础图库 — 未绑定",
    "sidebar.albums": "相册",
    "sidebar.pinned": "置顶",
    # -- Timeline rail --
    "timeline.day_format": "{year}年{month:02d}月{day:02d}日",
    "timeline.month_format": "{year}年{month:02d}月",

    # -- Startup --
    "startup.starting": "正在启动…",
    "startup.init_components": "正在初始化组件…",
    "startup.starting_services": "正在启动服务…",
    "startup.opening_library": "正在打开图库…",
    "startup.checking_scan": "正在检查扫描状态…",
    "startup.loading_photos": "正在加载照片…",

    # -- Status bar: Scan --
    "status.scanning": "扫描中…",
    "status.scan_started": "开始扫描…",
    "status.scanning_counting": "扫描中…（正在统计文件）",
    "status.scanning_no_files": "扫描中…（未找到文件）",
    "status.scanning_progress": "扫描中… {percent}%（{current}/{total}）",
    "status.scan_complete": "扫描完成。",
    "status.scan_failed": "扫描失败。",
    "status.scan_batch_failed": "有 {count} 个项目保存到数据库失败",

    # -- Status bar: Load --
    "status.loading_items": "正在加载项目…",
    "status.loading_progress": "正在加载项目…（{current}/{total}）",
    "status.load_complete": "相册加载完成。",
    "status.load_failed": "相册加载失败。",

    # -- Status bar: Import --
    "status.import_started": "开始导入…",
    "status.import_progress": "导入中…（{current}/{total}）",
    "status.import_rescanning": "正在重新扫描以完成导入…",

    # -- Status bar: Move/Delete/Restore --
    "status.delete_started": "开始删除…",
    "status.restore_started": "开始还原…",
    "status.move_started": "开始移动…",
    "status.delete_progress": "删除中…（{current}/{total}）",
    "status.restore_progress": "还原中…（{current}/{total}）",
    "status.move_progress": "移动中…（{current}/{total}）",
    "status.delete_rescanning": "正在重新扫描以完成删除…",
    "status.restore_rescanning": "正在重新扫描以完成还原…",
    "status.move_rescanning": "正在重新扫描以完成移动…",

    # -- Status bar: Export --
    "status.export_started": "开始导出…",
    "status.export_progress": "导出中…（{current}/{total}）",
    "status.export_finished": "{success} 个媒体已导出",
    "status.export_finished_with_fail": "{success} 个媒体已导出，{fail} 个失败",
    "status.export_scanning": "正在扫描已编辑的图片…",
    "status.export_exporting": "正在导出 {total} 张已编辑的图片…",

    # -- Context menu --
    "msg.cannot_delete_recently_deleted": "已删除项目中的文件无法再次删除。",
    "msg.select_items_to_delete": "请先选择要删除的项目。",
    "msg.deleted": "已删除",
    "msg.select_items_to_restore": "请先选择要还原的项目。",
    "msg.restoring": "正在还原…",
    "msg.select_items_to_copy": "请先选择要复制的项目。",
    "msg.files_not_available": "所选文件在磁盘上不可用。",
    "msg.copied_to_clipboard": "已复制到剪贴板",
    "msg.select_items_to_reveal": "请先选择要定位的项目。",
    "msg.file_not_found": "文件未找到：{name}",
    "msg.revealed_in_manager": "已在文件管理器中定位 {name}。",
    "msg.no_pasteable_files": "剪贴板中没有可粘贴的文件。",
    "msg.open_album_to_paste": "请先打开一个相册再粘贴文件。",
    "msg.pasting_files": "正在粘贴文件…",
    "msg.no_album_open": "当前没有打开的相册。",
    "msg.folder_not_found": "文件夹未找到：{path}",
    "msg.select_items_to_move": "请先选择要移动的项目。",
    "msg.cover_updated": "封面已更新",
    "msg.unable_to_set_cover": "无法为所选项目设置封面。",

    # -- Export --
    "msg.library_not_bound": "图库未绑定。",
    "msg.cannot_create_export_folder": "无法创建导出文件夹：{exc}",
    "msg.select_export_destination": "选择导出目标位置",
    "msg.no_items_selected": "未选择任何项目。",

    # -- Share --
    "msg.no_item_to_share": "未选择要分享的项目。",
    "msg.preparing_image": "正在准备图片…",
    "msg.copied_original_file": "已复制原始文件",
    "msg.preparing_video": "正在准备视频…",

    # -- Dialog --
    "dialog.select_album": "选择相册",
    "dialog.select_basic_library": "选择基础图库",
    "dialog.scanning_new_folder": "正在扫描新文件夹 {name}…",
    "dialog.already_library_folder": "{root} 已经是图库文件夹",
    "dialog.added_library_folder": "已添加图库文件夹 {name}",
    "dialog.basic_library_bound": "基础图库已绑定到 {root}",
    "dialog.prompt_bind_library": "请选择一个文件夹作为基础图库。",
    "dialog.bind_basic_library": "绑定基础图库",
    "dialog.restore_failed": "还原失败",
    "dialog.restore_to_root_msg": "无法找到 '{name}' 的原始相册或确定其原始位置。是否要将此文件还原到主「基础图库」文件夹？",
    "dialog.yes": "是",
    "dialog.no": "否",
    "dialog.remove_library": "移除图库",
    "dialog.remove_library_msg": "确定要移除图库 {root} 吗？\n移除后将不再扫描该文件夹。",
    "dialog.remove": "移除",

    # -- Sidebar menu --
    "menu.new_album": "新建相册…",
    "menu.new_sub_album": "新建子相册…",
    "menu.rename_album": "重命名相册…",
    "menu.show_in_file_manager": "在文件管理器中显示",
    "menu.exclude_from_scan": "从扫描中排除",
    "menu.pin_album": "置顶相册",
    "menu.unpin_album": "取消置顶",
    "menu.rename": "重命名…",
    "menu.unpin": "取消置顶",
    "dialog.rename_pinned_item": "重命名置顶项目",
    "dialog.new_pinned_label": "新置顶标签：",
    "msg.pinned_label_empty": "置顶标签不能为空。",
    "dialog.new_album": "新建相册",
    "dialog.album_name": "相册名称：",
    "msg.album_name_empty": "相册名称不能为空。",
    "dialog.rename_album": "重命名相册",
    "dialog.new_album_name": "新相册名称：",

    # -- ExifTool --
    "dialog.exiftool_not_found": "未找到 ExifTool",
    "dialog.exiftool_warning": (
        "未找到 ExifTool，无法提取照片元数据（GPS、尺寸、拍摄日期等）。\n\n"
        "解决方法（任选其一）：\n"
        "1. 下载 ExifTool 并将 exiftool.exe 放入程序目录\n"
        "2. 安装 ExifTool 并确保其在系统 PATH 中\n\n"
        "详细信息：{message}"
    ),
    "msg.file_unreadable": "文件无法找到或读取：{name}\n\n{message}",

    # -- People --
    "people.photos": "张照片",
    "people.no_people": "未检测到人物",
    "people.hidden": "已隐藏",
    "people.unpin_group_first": "置顶的群组需要先取消置顶才能解散。",
    "people.cannot_merge_hidden": "隐藏和显示状态的人物无法合并，请先将两个人物卡片都设为隐藏或都设为显示。",
    "people.cannot_merge": "无法合并人物",
    "people.merge_person": "合并人物",
    "people.merge_to": "合并到",
    "people.select": "选择",
    "people.unnamed": "此人",
    "people.hide_person_title": "隐藏此人物？",
    "people.hide_person_body": "隐藏 {name} 后，将从人物视图中移除，直到你选择「显示隐藏的人物」或取消隐藏。",
    "people.hide_person_confirm": "隐藏人物",
    "people.unnamed_group": "此群组",
    "people.dissolve_group_title": "解散此群组？",
    "people.dissolve_group_body": "解散 {label} 后将移除群组，但保留其中的所有人物和照片。",
    "people.dissolve_group_confirm": "解散群组",
    "people.pin_requires_name": "置顶此人物前需要先填写名称。",
    "people.select_other": "选择其他人",
    "people.assign_face_to": "将此人脸分配给",

    # -- Map --
    "map.no_location": "无位置信息",
    "map.loading": "正在加载地图…",
    "map.load_failed": "地图加载失败，请重启应用重试",
    "map.source_label": "地图",
    "map.source_local": "本地地图",
    "map.source_gaode": "高德地图",
    "map.source_esri": "Esri 街道图",
    "map.source_carto": "CARTO Voyager",
    "map.source_osm": "OpenStreetMap",
    "map.source_apple": "Apple Maps",
    "map.feature_limited": "功能受限",
    "map.location_saved_no_exiftool": (
        "地点已保存到本机图库数据库。\n\n"
        "应用当前环境未找到或无法访问 ExifTool，暂时无法把 GPS 信息写入原始照片/视频文件。"
        "请确认 ExifTool 已安装并可被应用访问。"
    ),
    "map.file_write_failed": "原文件写入失败",
    "map.location_saved_file_write_failed": (
        "地点已保存到本机图库数据库。\n\n"
        "但写入原始文件时出错，GPS 信息可能未写入照片/视频文件。"
    ),
    "map.location_saved_file_write_failed_reason": (
        "地点已保存到本机图库数据库。\n\n"
        "GPS 信息未能写入原始照片/视频文件：{reason}"
    ),
    "map.install_extension_prompt": "安装地图扩展以使用「分配位置」功能。",

    # -- Preview --
    "preview.loading": "正在加载…",

    # -- Generic --
    "generic.ok": "确定",
    "generic.cancel": "取消",
    "generic.close": "关闭",

    # -- Gallery context menu --
    "menu.copy": "复制",
    "menu.reveal": "在文件管理器中显示",
    "menu.export": "导出",
    "menu.set_as_cover": "设为封面",
    "menu.move_to": "移动到",
    "menu.delete": "删除",
    "menu.restore": "还原",
    "menu.paste": "粘贴",
    "menu.open_folder": "打开文件夹位置",

    # -- Search --
    "search.placeholder": "搜索照片...",
    "search.no_session": "无图库会话",
    "search.not_available": "语义搜索不可用，请安装 agent 依赖",
    "search.searching": "正在搜索 '{query}'...",
    "search.no_results": "未找到结果",
    "search.found": "找到 {count} 张照片",

    # -- Agent --
    "action.enable_semantic_search": "启用语义搜索",
    "agent.enabled": "语义搜索已启用",
    "agent.disabled": "语义搜索已禁用",
    "menu.agent_features": "智能整理",
    "action.find_duplicates": "查找重复照片",
    "action.smart_album_event": "按事件创建相册",
    "action.smart_album_location": "按地点创建相册",
    "action.smart_album_time": "按时间创建相册",
    "action.smart_album_theme": "按主题创建相册",
    "search.tips_title": "搜索技巧",
    "search.tips": "搜索提示：\n\n1. 使用英文关键词效果更好（如 'yellow crane tower' 而非 '黄鹤楼'）\n2. 使用描述性词语（如 'Chinese tower', 'landmark', 'building'）\n3. 可以组合多个关键词（如 'sunset beach summer'）\n4. 支持场景、物体、活动等描述",
}

_EN: dict[str, str] = {
    # -- Menu: File --
    "menu.file": "&File",
    "menu.view": "&View",
    "menu.settings": "&Settings",
    "action.open_album": "Open Album Folder…",
    "action.rescan": "Rescan",
    "action.rebuild_links": "Rebuild Live Links",
    "action.set_basic_library": "Set Basic Library…",
    "action.download_map_ext": "Download Map Extension…",
    "action.export_all_edited": "Export All Edited",
    "action.export_selected": "Export Selected",

    # -- Menu: View --
    "action.show_filmstrip": "Show Filmstrip",
    "action.show_face_names": "Show Face Names",
    "action.show_hidden_people": "Show Hidden People",

    # -- Menu: Settings --
    "menu.appearance": "Appearance",
    "menu.export_destination": "Export Destination",
    "menu.export_format": "Export Format",
    "menu.wheel_action": "Wheel Action",
    "menu.share_action": "Share Action",
    "menu.language": "Language",

    # -- Theme --
    "action.system_default": "System Default",
    "action.light_mode": "Light Mode",
    "action.dark_mode": "Dark Mode",

    # -- Share actions --
    "action.copy_file": "Copy File",
    "action.copy_path": "Copy Path",
    "action.reveal_file": "Reveal in File Manager",

    # -- Wheel actions --
    "action.navigate": "Navigate",
    "action.zoom": "Zoom",

    # -- Export destination --
    "action.export_to_library": "Basic Library",
    "action.export_ask": "Ask Every Time",

    # -- Language --
    "action.lang_zh": "中文",
    "action.lang_en": "English",

    # -- Sidebar --
    "sidebar.basic_library": "Basic Library",
    "sidebar.basic_library_unbound": "Basic Library — not bound",
    "sidebar.albums": "Albums",
    "sidebar.pinned": "Pinned",
    # -- Timeline rail --
    "timeline.day_format": "{month:02d}/{day:02d}/{year}",
    "timeline.month_format": "{month:02d}/{year}",

    # -- Startup --
    "startup.starting": "Starting…",
    "startup.init_components": "Initializing components…",
    "startup.starting_services": "Starting services…",
    "startup.opening_library": "Opening library…",
    "startup.checking_scan": "Checking scan status…",
    "startup.loading_photos": "Loading photos…",

    # -- Status bar: Scan --
    "status.scanning": "Scanning…",
    "status.scan_started": "Starting scan…",
    "status.scanning_counting": "Scanning… (counting files)",
    "status.scanning_no_files": "Scanning… (no files found)",
    "status.scanning_progress": "Scanning… {percent}% ({current}/{total})",
    "status.scan_complete": "Scan complete.",
    "status.scan_failed": "Scan failed.",
    "status.scan_batch_failed": "{count} items failed to save to database",

    # -- Status bar: Load --
    "status.loading_items": "Loading items…",
    "status.loading_progress": "Loading items… ({current}/{total})",
    "status.load_complete": "Album loaded.",
    "status.load_failed": "Album load failed.",

    # -- Status bar: Import --
    "status.import_started": "Starting import…",
    "status.import_progress": "Importing… ({current}/{total})",
    "status.import_rescanning": "Rescanning to complete import…",

    # -- Status bar: Move/Delete/Restore --
    "status.delete_started": "Starting delete…",
    "status.restore_started": "Starting restore…",
    "status.move_started": "Starting move…",
    "status.delete_progress": "Deleting… ({current}/{total})",
    "status.restore_progress": "Restoring… ({current}/{total})",
    "status.move_progress": "Moving… ({current}/{total})",
    "status.delete_rescanning": "Rescanning to complete delete…",
    "status.restore_rescanning": "Rescanning to complete restore…",
    "status.move_rescanning": "Rescanning to complete move…",

    # -- Status bar: Export --
    "status.export_started": "Starting export…",
    "status.export_progress": "Exporting… ({current}/{total})",
    "status.export_finished": "{success} media exported",
    "status.export_finished_with_fail": "{success} media exported, {fail} failed",
    "status.export_scanning": "Scanning library for edited images…",
    "status.export_exporting": "Exporting {total} edited images…",

    # -- Context menu --
    "msg.cannot_delete_recently_deleted": "Items inside Recently Deleted cannot be deleted again.",
    "msg.select_items_to_delete": "Please select items to delete first.",
    "msg.deleted": "Deleted",
    "msg.select_items_to_restore": "Please select items to restore first.",
    "msg.restoring": "Restoring…",
    "msg.select_items_to_copy": "Please select items to copy first.",
    "msg.files_not_available": "Selected files are not available on disk.",
    "msg.copied_to_clipboard": "Copied to clipboard",
    "msg.select_items_to_reveal": "Please select items to locate first.",
    "msg.file_not_found": "File not found: {name}",
    "msg.revealed_in_manager": "Revealed {name} in file manager.",
    "msg.no_pasteable_files": "No pasteable files in clipboard.",
    "msg.open_album_to_paste": "Please open an album before pasting files.",
    "msg.pasting_files": "Pasting files…",
    "msg.no_album_open": "No album is currently open.",
    "msg.folder_not_found": "Folder not found: {path}",
    "msg.select_items_to_move": "Please select items to move first.",
    "msg.cover_updated": "Cover Updated",
    "msg.unable_to_set_cover": "Unable to set cover for the selected item.",

    # -- Export --
    "msg.library_not_bound": "Library not bound.",
    "msg.cannot_create_export_folder": "Cannot create export folder: {exc}",
    "msg.select_export_destination": "Select export destination",
    "msg.no_items_selected": "No items selected.",

    # -- Share --
    "msg.no_item_to_share": "No item selected to share.",
    "msg.preparing_image": "Preparing image…",
    "msg.copied_original_file": "Copied original file",
    "msg.preparing_video": "Preparing video…",

    # -- Dialog --
    "dialog.select_album": "Select Album",
    "dialog.select_basic_library": "Select Basic Library",
    "dialog.scanning_new_folder": "Scanning new folder {name}…",
    "dialog.already_library_folder": "{root} is already a library folder",
    "dialog.added_library_folder": "Added library folder {name}",
    "dialog.basic_library_bound": "Basic Library bound to {root}",
    "dialog.prompt_bind_library": "Please select a folder as the Basic Library.",
    "dialog.bind_basic_library": "Bind Basic Library",
    "dialog.restore_failed": "Restore Failed",
    "dialog.restore_to_root_msg": "The original album for '{name}' could not be found or its original location could not be determined. Do you want to restore this file to the main 'Basic Library' folder instead?",
    "dialog.yes": "Yes",
    "dialog.no": "No",
    "dialog.remove_library": "Remove Library",
    "dialog.remove_library_msg": "Are you sure you want to remove library {root}?\nThe folder will no longer be scanned.",
    "dialog.remove": "Remove",

    # -- Sidebar menu --
    "menu.new_album": "New Album…",
    "menu.new_sub_album": "New Sub-Album…",
    "menu.rename_album": "Rename Album…",
    "menu.show_in_file_manager": "Show in File Manager",
    "menu.exclude_from_scan": "Exclude from Scan",
    "menu.pin_album": "Pin Album",
    "menu.unpin_album": "Unpin Album",
    "menu.rename": "Rename…",
    "menu.unpin": "Unpin",
    "dialog.rename_pinned_item": "Rename Pinned Item",
    "dialog.new_pinned_label": "New pinned label:",
    "msg.pinned_label_empty": "Pinned label cannot be empty.",
    "dialog.new_album": "New Album",
    "dialog.album_name": "Album name:",
    "msg.album_name_empty": "Album name cannot be empty.",
    "dialog.rename_album": "Rename Album",
    "dialog.new_album_name": "New album name:",

    # -- ExifTool --
    "dialog.exiftool_not_found": "ExifTool Not Found",
    "dialog.exiftool_warning": (
        "ExifTool was not found. Photo metadata (GPS, dimensions, date taken, etc.) cannot be extracted.\n\n"
        "Solutions (choose one):\n"
        "1. Download ExifTool and place exiftool.exe in the program directory\n"
        "2. Install ExifTool and ensure it is in the system PATH\n\n"
        "Details: {message}"
    ),
    "msg.file_unreadable": "File cannot be found or read: {name}\n\n{message}",

    # -- People --
    "people.photos": "photos",
    "people.no_people": "No people detected",
    "people.hidden": "Hidden",
    "people.unpin_group_first": "Pinned groups must be unpinned before they can be dissolved.",
    "people.cannot_merge_hidden": "Hidden and visible people cannot be merged. Please set both cards to the same visibility state first.",
    "people.cannot_merge": "Cannot Merge",
    "people.merge_person": "Merge Person",
    "people.merge_to": "Merge to",
    "people.select": "Select",
    "people.unnamed": "This person",
    "people.hide_person_title": "Hide this person?",
    "people.hide_person_body": "Hiding {name} will remove them from the People view until you show hidden people or unhide them.",
    "people.hide_person_confirm": "Hide Person",
    "people.unnamed_group": "This group",
    "people.dissolve_group_title": "Dissolve this group?",
    "people.dissolve_group_body": "Dissolving {label} will remove the group but keep all people and photos inside it.",
    "people.dissolve_group_confirm": "Dissolve Group",
    "people.pin_requires_name": "Please enter a name before pinning this person.",
    "people.select_other": "Select Another Person",
    "people.assign_face_to": "Assign this face to",

    # -- Map --
    "map.no_location": "No location data",
    "map.loading": "Loading map…",
    "map.load_failed": "Map loading failed. Please restart the application.",
    "map.source_label": "Map",
    "map.source_local": "Local Map",
    "map.source_gaode": "Gaode Map",
    "map.source_esri": "Esri Streets",
    "map.source_carto": "CARTO Voyager",
    "map.source_osm": "OpenStreetMap",
    "map.source_apple": "Apple Maps",
    "map.feature_limited": "Feature Limited",
    "map.location_saved_no_exiftool": (
        "Location has been saved to the local library database.\n\n"
        "ExifTool was not found or is inaccessible, so GPS data cannot be written to the original photo/video files. "
        "Please ensure ExifTool is installed and accessible."
    ),
    "map.file_write_failed": "File Write Failed",
    "map.location_saved_file_write_failed": (
        "Location has been saved to the local library database.\n\n"
        "However, an error occurred while writing to the original file. GPS data may not have been written to the photo/video."
    ),
    "map.location_saved_file_write_failed_reason": (
        "Location has been saved to the local library database.\n\n"
        "GPS data could not be written to the original photo/video file: {reason}"
    ),
    "map.install_extension_prompt": "Install the map extension to use Assign a Location.",

    # -- Preview --
    "preview.loading": "Loading…",

    # -- Generic --
    "generic.ok": "OK",
    "generic.cancel": "Cancel",
    "generic.close": "Close",

    # -- Gallery context menu --
    "menu.copy": "Copy",
    "menu.reveal": "Reveal in File Manager",
    "menu.export": "Export",
    "menu.set_as_cover": "Set as Cover",
    "menu.move_to": "Move to",
    "menu.delete": "Delete",
    "menu.restore": "Restore",
    "menu.paste": "Paste",
    "menu.open_folder": "Open Folder Location",

    # -- Search --
    "search.placeholder": "Search photos...",
    "search.no_session": "No library session",
    "search.not_available": "Semantic search not available. Install agent dependencies.",
    "search.searching": "Searching for '{query}'...",
    "search.no_results": "No results found",
    "search.found": "Found {count} photos",

    # -- Agent --
    "action.enable_semantic_search": "Enable Semantic Search",
    "agent.enabled": "Semantic search enabled",
    "agent.disabled": "Semantic search disabled",
    "menu.agent_features": "Smart Organize",
    "action.find_duplicates": "Find Duplicate Photos",
    "action.smart_album_event": "Create Album by Event",
    "action.smart_album_location": "Create Album by Location",
    "action.smart_album_time": "Create Album by Time",
    "action.smart_album_theme": "Create Album by Theme",
    "search.tips_title": "Search Tips",
    "search.tips": "Search Tips:\n\n1. English keywords work better (e.g., 'yellow crane tower' instead of '黄鹤楼')\n2. Use descriptive words (e.g., 'Chinese tower', 'landmark', 'building')\n3. Combine multiple keywords (e.g., 'sunset beach summer')\n4. Supports scenes, objects, activities descriptions",
}


def tr(key: str, **kwargs: object) -> str:
    """Return the translation for *key* in the current language.

    Any keyword arguments are interpolated into the translated string using
    :meth:`str.format_map`.
    """

    table = _ZH if _current == "zh" else _EN
    text = table.get(key)
    if text is None:
        # Fall back to the other language, then to the key itself.
        text = (_EN if _current == "zh" else _ZH).get(key, key)
    if kwargs:
        try:
            return text.format_map(kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def set_language(lang: str) -> None:
    """Switch the global language to *lang* (``"zh"`` or ``"en"``)."""

    global _current
    if lang not in {"zh", "en"}:
        _logger.warning("set_language: unsupported language %r, defaulting to 'zh'", lang)
        lang = "zh"
    if _current == lang:
        return
    _current = lang
    _logger.info("Language switched to %s", lang)


def current_language() -> str:
    """Return the active language code."""

    return _current


class LanguageStore(QObject):
    """Manages application-wide language state and reacts to settings changes."""

    languageChanged = Signal(str)
    """Emitted with the new language code when the language changes."""

    def __init__(self, settings: "SettingsManager") -> None:
        super().__init__()
        self._settings = settings
        self._settings.settingsChanged.connect(self._on_settings_changed)

        # Apply the persisted language on startup.
        saved = self._settings.get("ui.language", "zh")
        set_language(saved)

    def _on_settings_changed(self, key: str, value: object) -> None:
        if key == "ui.language" and isinstance(value, str):
            set_language(value)
            self.languageChanged.emit(value)


__all__ = ["LanguageStore", "current_language", "set_language", "tr"]
