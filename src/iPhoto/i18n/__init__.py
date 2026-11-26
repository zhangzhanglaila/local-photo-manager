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
    "menu.file": "文件(&F)",
    "menu.view": "视图(&V)",
    "menu.settings": "设置(&S)",
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
    "menu.search_precision": "搜索精度",
    "action.search_precision_strict": "精确",
    "action.search_precision_balanced": "均衡",
    "action.search_precision_loose": "宽松",

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
    "sidebar.search.loading": "搜索：{query}（搜索中…）",
    "sidebar.search.results": "搜索：{query}（{count} 张）",
    "sidebar.search.failed": "搜索：{query}（失败）",
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
    "status.scanning": "正在扫描文件…",
    "status.scan_started": "正在开始扫描文件…",
    "status.scanning_counting": "正在扫描文件…（统计文件数）",
    "status.scanning_no_files": "正在扫描文件…（未找到文件）",
    "status.scanning_progress": "正在扫描文件… {percent}%（{current}/{total}）",
    "status.scan_complete": "文件扫描完成。",
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
    "search.popup.scope": "搜索范围",
    "search.popup.scope_all": "全部照片",
    "search.popup.scope_album": "当前相册",
    "search.popup.history": "最近搜索",
    "search.popup.history_empty": "还没有搜索记录",
    "search.popup.clear": "清空",
    "search.popup.similar": "智能推荐",
    "search.popup.similar_tooltip": "查找相似照片",
    "search.similar_query": "相似照片",

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
    "action.change_workspace": "更改存储位置…",
    "dialog.select_workspace_location": "选择新的存储位置",
    "dialog.workspace_relocate_title": "更改存储位置",
    "dialog.workspace_current_location": "当前存储位置：\n{path}",
    "dialog.workspace_change_prompt": "照片索引、缩略图与向量都保存在这个目录；迁移到新位置后需要重启应用才能生效。",
    "dialog.workspace_relocate_no_workspace": "当前库还没有工作区，无法迁移。",
    "dialog.workspace_relocate_same": "选择的位置与当前存储位置相同。",
    "dialog.workspace_relocate_confirm": "将把工作区复制到：\n{destination}\n\n复制并校验成功后会删除旧目录，且需要重启应用才能生效。继续吗？",
    "dialog.workspace_relocate_copying": "正在迁移工作区…",
    "dialog.workspace_relocate_failed": "迁移失败，原目录已保留：\n{error}",
    "dialog.workspace_relocate_done": "迁移完成：\n{destination}\n\n请重启应用以使用新的存储位置。",
    "setup.title": "初始设置",
    "setup.step_welcome": "欢迎",
    "setup.step_welcome_sub": "准备开始整理你的本地照片",
    "setup.step_library": "照片库位置",
    "setup.step_library_sub": "选择存放照片的文件夹",
    "setup.step_workspace": "数据存储位置",
    "setup.step_workspace_sub": "索引、缩略图与 AI 向量存这里",
    "setup.step_ai": "AI 能力（可选）",
    "setup.step_ai_sub": "语义搜索、人脸、文字识别",
    "setup.step_done": "完成",
    "setup.step_done_sub": "确认设置并开始扫描",
    "setup.welcome_title": "欢迎使用 iPhoto",
    "setup.welcome_sub": "准备开始整理你的本地照片",
    "setup.welcome_body": "接下来几步会带你完成两件事：选定照片文件夹，以及决定索引和缩略图存放在哪里。\n\n所有处理都在本机完成，照片不会被上传。",
    "setup.privacy_note": "数据仅在本机处理，不上传服务器",
    "setup.back": "← 上一步",
    "setup.next": "下一步 →",
    "setup.finish": "开始使用",
    "setup.browse": "浏览…",
    "setup.detect": "自动检测",
    "setup.reset_default": "重置默认",
    "setup.library_placeholder": "例如 D:/APPLE",
    "setup.library_hint": "可以添加多个文件夹，之后也能在设置里继续添加。",
    "setup.workspace_placeholder": "例如 D:/iPhotronWorkspace",
    "setup.workspace_hint": "索引、缩略图、人脸与语义向量都放在这里，可包含多个照片库；换位置后需要重启应用。",
    "setup.ai_clip": "语义搜索（约 {mb} MB）",
    "setup.ai_face": "人脸识别（约 {mb} MB）",
    "setup.ai_ocr": "文字识别（约 {mb} MB）",
    "setup.ai_hint": "可以先跳过，之后在设置里开启；首次使用某个能力时会自动下载模型。",
    "setup.summary_library": "照片库：{path}",
    "setup.summary_workspace": "数据位置：{path}",
    "setup.summary_ai": "已启用：{items}",
    "setup.not_set": "未设置",
    "setup.ai_none": "无（稍后再启用）",
    "action.run_setup_wizard": "运行初始设置向导…",
    "setup.add_folder": "添加文件夹…",
    "setup.remove_folder": "移除所选",
    "dialog.settings_title": "设置",
    "dialog.close_title": "关闭 iPhoto",
    "dialog.close_prompt": "要最小化到任务栏，还是退出程序？",
    "dialog.close_minimise": "最小化",
    "dialog.close_quit": "退出程序",
    "dialog.close_cancel": "取消",
    'settings.page.general': '通用',
    'settings.page.browsing': '浏览',
    'settings.page.export': '导出',
    'settings.page.library': '图库',
    'settings.page.intelligence': '智能',
    'settings.section.interface': '界面',
    'settings.section.window': '窗口',
    'settings.section.display': '显示',
    'settings.section.interaction': '交互',
    'settings.section.defaults': '默认设置',
    'settings.section.actions': '操作',
    'settings.section.basic_library': '基础图库',
    'settings.section.storage': '存储',
    'settings.section.maintenance': '维护',
    'settings.section.semantic': '语义搜索',
    'settings.section.tools': '工具',
    'settings.row.appearance': '外观',
    'settings.row.language': '语言',
    'settings.row.close_action': '关闭窗口时',
    'settings.row.filmstrip': '显示胶片条',
    'settings.row.face_names': '在人脸信息中显示姓名',
    'settings.row.hidden_people': '显示已隐藏的人物',
    'settings.row.wheel': '滚轮行为',
    'settings.row.share': '分享行为',
    'settings.row.export_destination': '导出目标',
    'settings.row.export_format': '导出格式',
    'settings.row.current_library': '当前基础图库',
    'settings.row.bind_library': '设置基础图库…',
    'settings.row.change_workspace': '更改存储位置…',
    'settings.row.run_setup': '运行初始设置向导…',
    'settings.row.open_album_folder': '打开相册文件夹…',
    'settings.row.rebuild_links': '重建实况链接',
    'settings.row.download_map': '下载地图扩展…',
    'settings.row.semantic_search': '启用语义搜索',
    'settings.row.search_precision': '搜索精度',
    'settings.row.export_all_edited': '导出全部已编辑照片',
    'settings.row.export_selected': '导出所选照片',
    'settings.row.find_duplicates': '查找重复照片',
    'settings.row.smart_event': '按事件整理智能相册',
    'settings.row.smart_location': '按地点整理智能相册',
    'settings.row.smart_time': '按时间整理智能相册',
    'settings.row.smart_theme': '按主题整理智能相册',
    'settings.close.ask_every_time': '每次询问',
    'settings.close.minimise': '最小化到托盘',
    'settings.close.quit': '直接退出',
    'settings.not_bound': '未绑定',
    'empty.no_photos': '没有照片',
    'empty.favorites': '没有收藏',
    'empty.videos': '没有视频',
    'empty.live': '没有实况照片',
    'empty.no_library': '请选择照片文件夹',
    'empty.scanning': '正在扫描照片，请稍候…',
    'empty.no_media': '没有找到照片，点击「重新扫描」扫描此图库',
    'dialog.ok': '确定',
    'dialog.cancel': '取消',
    'tray.open': '打开主窗口',
    'tray.settings': '设置…',
    'tray.quit': '退出 iPhoto',
    'close.kicker': '退出应用',
    'close.minimise_title': '最小化到托盘',
    'close.minimise_desc': '留在后台运行，以后可从托盘图标或任务栏恢复窗口。',
    'close.quit_title': '退出程序',
    'close.quit_desc': '关闭 iPhoto 进程，未保存的编辑会提示确认。',
    'close.remember': '下次不再提示，直接按上面选择执行',
    'tray.settings_ellipsis': '设置…',
    'sidebar.home': '首页',
    'sidebar.all_photos': '所有照片',
    'sidebar.videos': '视频',
    'sidebar.live_photos': '实况照片',
    'sidebar.favorites': '个人收藏',
    'sidebar.people': '人物',
    'sidebar.location': '地点',
    'sidebar.recently_deleted': '最近删除',
    'organize.searching_duplicates': '正在查找重复照片…',
    'organize.no_duplicates': '没有发现重复照片',
    'organize.duplicates_found': '发现 {groups} 组重复照片（共 {photos} 张）',
    'organize.creating_albums': '正在按{group_by}创建智能相册…',
    'organize.no_albums': '没有可整理的相册',
    'organize.albums_created': '已创建 {count} 个智能相册',
    'organize.group.event': '事件',
    'organize.group.location': '地点',
    'organize.group.time': '时间',
    'organize.group.theme': '主题',
    'dialog.rescanning_library_folder': '正在重新扫描 {name}…',
    'search.model.title': '选择搜索模型',
    'search.model.subtitle': '模型决定用哪种语言搜索、结果有多准、建索引有多快。之后可在“设置 → 智能”里随时切换。',
    'search.model.use': '使用此模型',
    'search.model.summary': '{language} · {quality} · {mb} MB',
    'search.model.details': '{name}\n语言：{language}\u3000质量：{quality}\u3000索引速度：{speed}\n体积：{mb} MB\u3000状态：{state}',
    'search.model.installed': '已安装',
    'search.model.missing': '需要下载 {mb} MB',
    'search.model.lang.en': '英文',
    'search.model.lang.zh': '中文',
    'search.model.lang.multilingual': '中英等多语言',
    'search.model.quality.1': '基础',
    'search.model.quality.2': '较好',
    'search.model.quality.3': '最佳',
    'search.model.speed.fastest': '最快',
    'search.model.speed.fast': '快',
    'search.model.speed.medium': '中等',
    'search.model.current': '当前：{name}',
    'search.model.switch': '切换搜索模型…',
    'search.model.choose': '选择…',
    'search.model.row': '搜索模型',
    'search.model.device': '运行设备：{device}',
    'search.model.coverage': '已索引 {done}/{total}',
    'search.model.reindexing': '正在为新模型重建索引：{done}/{total}',
    'search.model.download_failed': '模型下载失败',
    'search.model.download_done': '模型已下载：{name}',
    'search.model.rebuild': '重建搜索索引…',
    'search.model.rebuild_confirm': '将用当前模型重新计算所有照片的向量，已有向量会被覆盖。GPU 下通常几分钟，继续吗？',
    'search.model.rebuild_started': '正在重建搜索索引…',
    'settings.row.search_model': '搜索模型',
    'search.model.download': '下载模型',
    'search.model.download_and_use': '下载并使用',
    'search.model.retry': '重试下载',
    'search.model.downloading': '正在下载 {name}（约 {mb} MB）…',
    'search.model.open_folder': '打开文件夹',
    'search.model.download_failed_hint': '下载失败（{error}）。\n可以把模型文件手动放到：{path}\n然后回到这里点「重试下载」旁边的「下载并使用」。',
    'search.model.downloading_percent': '正在下载：{percent}%',
    'search.model.download_failed_reason': '下载失败：{reason}\n目标文件夹：{path}\n可以点下面的链接手动下载，把文件放进这个文件夹后再点「重试下载」。',
    'search.model.manual_links': '手动下载：{links}',
    'search.model.reason.timeout': '连接超时，网络不可达或需要代理',
    'search.model.reason.dns': '域名解析失败，可能没有网络',
    'search.model.reason.network': '网络连接被拒绝或中断',
    'search.model.reason.auth': '没有访问权限',
    'search.model.reason.unknown': '未知错误',
    'search.model.not_installed_suffix': '（未下载）',
    'settings.row.search_index_state': '索引状态',
    'search.model.indexing_now': '正在重建索引：{percent}%（{done}/{total}）',
    'search.model.coverage_unknown': '尚未建立索引',
    'search.model.device_pending': '设备待加载',
    'status.tasks_empty': '没有正在进行的后台任务',
    'status.tasks_done': '完成',
    'status.tasks_failed': '失败',
    'status.task.scan': '正在扫描文件',
    'status.task.index': '正在建立搜索索引',
    'status.task.download': '正在下载搜索模型',
    'status.task.scan_named': '正在扫描 {name}',
    'search.empty.title': '未找到与“{query}”相关的照片',
    'search.empty.hint': '换个更具体的说法试试，比如「海边的猫」「地铁站」「红色的花」',
    'search.empty.back': '返回所有照片',
    'search.no_results': '没有搜索结果',
    'settings.section.local_ai': '人脸与文字',
    'settings.row.face_recognition': '人脸识别（人物聚类）',
    'settings.row.face_index_state': '人脸索引状态',
    'settings.row.face_rebuild': '补全人脸索引…',
    'settings.row.ocr': '文字识别（OCR）',
    'settings.row.ocr_index_state': '文字索引状态',
    'settings.row.ocr_rebuild': '补全文字索引…',
    'settings.ai_state.coverage': '已处理 {done}/{total}',
    'settings.ai_state.people': '{count} 个人物',
    'settings.ai_state.no_library': '未打开照片库',
    'settings.rebuild_confirm.face': '将在后台扫描尚未处理的人脸照片，首次使用会先加载人脸模型（约 300 MB）。继续吗？',
    'settings.rebuild_confirm.ocr': '将在后台识别尚未处理照片中的文字，识别结果会加入搜索。继续吗？',
    'status.task.face': '正在识别人脸',
    'status.task.ocr': '正在识别文字',
    'settings.ai_state.scanning': '{title}：正在扫描待处理照片…',
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
    "menu.search_precision": "Search Precision",
    "action.search_precision_strict": "Precise",
    "action.search_precision_balanced": "Balanced",
    "action.search_precision_loose": "Loose",

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
    "sidebar.search.loading": "Search: {query} (searching…)",
    "sidebar.search.results": "Search: {query} ({count})",
    "sidebar.search.failed": "Search: {query} (failed)",
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
    "status.scanning": "Scanning files…",
    "status.scan_started": "Starting file scan…",
    "status.scanning_counting": "Scanning files… (counting)",
    "status.scanning_no_files": "Scanning files… (none found)",
    "status.scanning_progress": "Scanning files… {percent}% ({current}/{total})",
    "status.scan_complete": "File scan complete.",
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
    "search.popup.scope": "Search in",
    "search.popup.scope_all": "All photos",
    "search.popup.scope_album": "Current album",
    "search.popup.history": "Recent searches",
    "search.popup.history_empty": "No recent searches",
    "search.popup.clear": "Clear",
    "search.popup.similar": "Suggested",
    "search.popup.similar_tooltip": "Find similar photos",
    "search.similar_query": "Similar photos",

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
    "action.change_workspace": "Change Storage Location…",
    "dialog.select_workspace_location": "Select the new storage location",
    "dialog.workspace_relocate_title": "Change Storage Location",
    "dialog.workspace_current_location": "Current storage location:\n{path}",
    "dialog.workspace_change_prompt": "The index, thumbnails and embeddings live in this folder. A restart is required after moving them.",
    "dialog.workspace_relocate_no_workspace": "This library has no workspace to relocate yet.",
    "dialog.workspace_relocate_same": "The selected folder is the current storage location.",
    "dialog.workspace_relocate_confirm": "The workspace will be copied to:\n{destination}\n\nThe old folder is removed after a verified copy, and the app must restart. Continue?",
    "dialog.workspace_relocate_copying": "Moving the workspace…",
    "dialog.workspace_relocate_failed": "Relocation failed; the original folder was kept:\n{error}",
    "dialog.workspace_relocate_done": "Relocation finished:\n{destination}\n\nRestart the app to use the new location.",
    "setup.title": "Setup",
    "setup.step_welcome": "Welcome",
    "setup.step_welcome_sub": "Get your local library ready",
    "setup.step_library": "Photo Library",
    "setup.step_library_sub": "Choose the folder that holds your photos",
    "setup.step_workspace": "Data Location",
    "setup.step_workspace_sub": "Index, thumbnails and AI vectors live here",
    "setup.step_ai": "AI Features (optional)",
    "setup.step_ai_sub": "Semantic search, faces, text recognition",
    "setup.step_done": "Done",
    "setup.step_done_sub": "Review and start scanning",
    "setup.welcome_title": "Welcome to iPhoto",
    "setup.welcome_sub": "Get your local library ready",
    "setup.welcome_body": "The next steps will pick your photo folder and decide where the index and thumbnails are stored.\n\nEverything runs on this machine; your photos are never uploaded.",
    "setup.privacy_note": "Data is processed locally and never uploaded",
    "setup.back": "← Back",
    "setup.next": "Next →",
    "setup.finish": "Start",
    "setup.browse": "Browse…",
    "setup.detect": "Detect",
    "setup.reset_default": "Reset",
    "setup.library_placeholder": "e.g. D:/APPLE",
    "setup.library_hint": "You can add several folders now, and more later in settings.",
    "setup.workspace_placeholder": "e.g. D:/iPhotronWorkspace",
    "setup.workspace_hint": "The index, thumbnails, faces and embeddings live here and may hold several libraries; a restart is needed after changing it.",
    "setup.ai_clip": "Semantic search (~{mb} MB)",
    "setup.ai_face": "Face recognition (~{mb} MB)",
    "setup.ai_ocr": "Text recognition (~{mb} MB)",
    "setup.ai_hint": "You can skip these and enable them later; models download on first use.",
    "setup.summary_library": "Photo library: {path}",
    "setup.summary_workspace": "Data location: {path}",
    "setup.summary_ai": "Enabled: {items}",
    "setup.not_set": "not set",
    "setup.ai_none": "none (enable later)",
    "action.run_setup_wizard": "Run Setup Wizard…",
    "setup.add_folder": "Add Folder…",
    "setup.remove_folder": "Remove Selected",
    "dialog.settings_title": "Settings",
    "dialog.close_title": "Close iPhoto",
    "dialog.close_prompt": "Minimise to the taskbar, or quit the application?",
    "dialog.close_minimise": "Minimise",
    "dialog.close_quit": "Quit",
    "dialog.close_cancel": "Cancel",
    'settings.page.general': 'General',
    'settings.page.browsing': 'Browsing',
    'settings.page.export': 'Export',
    'settings.page.library': 'Library',
    'settings.page.intelligence': 'Intelligence',
    'settings.section.interface': 'Interface',
    'settings.section.window': 'Window',
    'settings.section.display': 'Display',
    'settings.section.interaction': 'Interaction',
    'settings.section.defaults': 'Defaults',
    'settings.section.actions': 'Actions',
    'settings.section.basic_library': 'Basic library',
    'settings.section.storage': 'Storage',
    'settings.section.maintenance': 'Maintenance',
    'settings.section.semantic': 'Semantic search',
    'settings.section.tools': 'Tools',
    'settings.row.appearance': 'Appearance',
    'settings.row.language': 'Language',
    'settings.row.close_action': 'On window close',
    'settings.row.filmstrip': 'Show filmstrip',
    'settings.row.face_names': 'Show names on faces',
    'settings.row.hidden_people': 'Show hidden people',
    'settings.row.wheel': 'Scroll wheel',
    'settings.row.share': 'Share action',
    'settings.row.export_destination': 'Export destination',
    'settings.row.export_format': 'Export format',
    'settings.row.current_library': 'Current basic library',
    'settings.row.bind_library': 'Set basic library…',
    'settings.row.change_workspace': 'Change storage location…',
    'settings.row.run_setup': 'Run setup wizard…',
    'settings.row.open_album_folder': 'Open album folder…',
    'settings.row.rebuild_links': 'Rebuild Live Photo links',
    'settings.row.download_map': 'Download map extension…',
    'settings.row.semantic_search': 'Enable semantic search',
    'settings.row.search_precision': 'Search precision',
    'settings.row.export_all_edited': 'Export all edited photos',
    'settings.row.export_selected': 'Export selected photos',
    'settings.row.find_duplicates': 'Find duplicate photos',
    'settings.row.smart_event': 'Smart album by event',
    'settings.row.smart_location': 'Smart album by location',
    'settings.row.smart_time': 'Smart album by time',
    'settings.row.smart_theme': 'Smart album by theme',
    'settings.close.ask_every_time': 'Ask every time',
    'settings.close.minimise': 'Minimise to tray',
    'settings.close.quit': 'Quit',
    'settings.not_bound': 'Not bound',
    'empty.no_photos': 'No photos',
    'empty.favorites': 'No favorites',
    'empty.videos': 'No videos',
    'empty.live': 'No Live Photos',
    'empty.no_library': 'Choose a photo folder',
    'empty.scanning': 'Scanning photos, please wait…',
    'empty.no_media': 'No media found. Click Rescan to scan this library.',
    'dialog.ok': 'OK',
    'dialog.cancel': 'Cancel',
    'tray.open': 'Open iPhoto',
    'tray.settings': 'Settings…',
    'tray.quit': 'Quit iPhoto',
    'close.kicker': 'QUIT',
    'close.minimise_title': 'Minimise to tray',
    'close.minimise_desc': 'Keep running in the background; reopen it from the tray icon.',
    'close.quit_title': 'Quit iPhoto',
    'close.quit_desc': 'Close iPhoto; unsaved edits will prompt for confirmation.',
    'close.remember': 'Do not ask again; always use the option above',
    'tray.settings_ellipsis': 'Settings…',
    'sidebar.home': 'Home',
    'sidebar.all_photos': 'All Photos',
    'sidebar.videos': 'Videos',
    'sidebar.live_photos': 'Live Photos',
    'sidebar.favorites': 'Favorites',
    'sidebar.people': 'People',
    'sidebar.location': 'Location',
    'sidebar.recently_deleted': 'Recently Deleted',
    'organize.searching_duplicates': 'Searching for duplicates…',
    'organize.no_duplicates': 'No duplicates found',
    'organize.duplicates_found': 'Found {groups} duplicate groups ({photos} photos)',
    'organize.creating_albums': 'Creating smart albums by {group_by}…',
    'organize.no_albums': 'No smart albums to create',
    'organize.albums_created': 'Created {count} smart albums',
    'organize.group.event': 'event',
    'organize.group.location': 'location',
    'organize.group.time': 'time',
    'organize.group.theme': 'theme',
    'dialog.rescanning_library_folder': 'Rescanning {name}…',
    'search.model.title': 'Choose a search model',
    'search.model.subtitle': 'The model decides which languages work, how relevant the results are, and how long indexing takes. You can change it later in Settings → Intelligence.',
    'search.model.use': 'Use this model',
    'search.model.summary': '{language} · {quality} · {mb} MB',
    'search.model.details': '{name}\nLanguages: {language}   Quality: {quality}   Indexing speed: {speed}\nDownload: {mb} MB   Status: {state}',
    'search.model.installed': 'installed',
    'search.model.missing': 'needs a {mb} MB download',
    'search.model.lang.en': 'English',
    'search.model.lang.zh': 'Chinese',
    'search.model.lang.multilingual': 'Multilingual',
    'search.model.quality.1': 'Basic',
    'search.model.quality.2': 'Good',
    'search.model.quality.3': 'Best',
    'search.model.speed.fastest': 'fastest',
    'search.model.speed.fast': 'fast',
    'search.model.speed.medium': 'moderate',
    'search.model.current': 'Current: {name}',
    'search.model.switch': 'Change search model…',
    'search.model.choose': 'Choose…',
    'search.model.row': 'Search model',
    'search.model.device': 'Running on {device}',
    'search.model.coverage': 'Indexed {done}/{total}',
    'search.model.reindexing': 'Rebuilding the index for the new model: {done}/{total}',
    'search.model.download_failed': 'Model download failed',
    'search.model.download_done': 'Model downloaded: {name}',
    'search.model.rebuild': 'Rebuild search index…',
    'search.model.rebuild_confirm': 'Every photo will be re-embedded with the current model and existing vectors replaced. This usually takes a few minutes on GPU. Continue?',
    'search.model.rebuild_started': 'Rebuilding the search index…',
    'settings.row.search_model': 'Search model',
    'search.model.download': 'Download',
    'search.model.download_and_use': 'Download and use',
    'search.model.retry': 'Retry download',
    'search.model.downloading': 'Downloading {name} (~{mb} MB)…',
    'search.model.open_folder': 'Open folder',
    'search.model.download_failed_hint': 'Download failed ({error}).\nYou can place the model files in: {path}\nThen choose “Download and use” again.',
    'search.model.downloading_percent': 'Downloading: {percent}%',
    'search.model.download_failed_reason': 'Download failed: {reason}\nTarget folder: {path}\nUse the links below to download manually, put the files in that folder, then retry.',
    'search.model.manual_links': 'Manual download: {links}',
    'search.model.reason.timeout': 'connection timed out (network unreachable or proxy required)',
    'search.model.reason.dns': 'DNS lookup failed (no network?)',
    'search.model.reason.network': 'connection refused or interrupted',
    'search.model.reason.auth': 'not authorised',
    'search.model.reason.unknown': 'unknown error',
    'search.model.not_installed_suffix': ' (not downloaded)',
    'settings.row.search_index_state': 'Index status',
    'search.model.indexing_now': 'Rebuilding index: {percent}% ({done}/{total})',
    'search.model.coverage_unknown': 'Not indexed yet',
    'search.model.device_pending': 'device pending',
    'status.tasks_empty': 'No background tasks',
    'status.tasks_done': 'done',
    'status.tasks_failed': 'failed',
    'status.task.scan': 'Scanning files',
    'status.task.index': 'Building the search index',
    'status.task.download': 'Downloading the search model',
    'status.task.scan_named': 'Scanning {name}',
    'search.empty.title': 'No photos match “{query}”',
    'search.empty.hint': 'Try a more specific phrase, e.g. “cat on a beach”, “subway platform”, “red flowers”',
    'search.empty.back': 'Back to all photos',
    'search.no_results': 'No search results',
    'settings.section.local_ai': 'Faces and text',
    'settings.row.face_recognition': 'Face recognition (people)',
    'settings.row.face_index_state': 'Face index status',
    'settings.row.face_rebuild': 'Scan pending faces…',
    'settings.row.ocr': 'Text recognition (OCR)',
    'settings.row.ocr_index_state': 'Text index status',
    'settings.row.ocr_rebuild': 'Scan pending text…',
    'settings.ai_state.coverage': '{done}/{total} processed',
    'settings.ai_state.people': '{count} people',
    'settings.ai_state.no_library': 'No library open',
    'settings.rebuild_confirm.face': 'Photos without face data will be scanned in the background. The first run loads the face models (~300 MB). Continue?',
    'settings.rebuild_confirm.ocr': 'Text in unprocessed photos will be recognised in the background and added to search. Continue?',
    'status.task.face': 'Recognising faces',
    'status.task.ocr': 'Recognising text',
    'settings.ai_state.scanning': '{title}: scanning pending photos…',
}


_REVERSE_EN: dict[str, str] = {}


def translate_qt(context: str, source: str, disambiguation: str | None = None) -> str:
    """Translate a Qt-style `(context, source)` pair via the app dictionaries.

    The window calls used `QCoreApplication.translate`, but no `.qm` catalogue ships
    with the app, so those strings stayed English even when the language was Chinese.
    """

    if not _REVERSE_EN:
        _REVERSE_EN.update({value: key for key, value in _EN.items()})
    key = _REVERSE_EN.get(source)
    return tr(key) if key else source


def tr(key: str, **kwargs: object) -> str:
    """Return the translation for *key* in the current language.

    Any keyword arguments are interpolated into the translated string using
    :meth:`str.format_map`.
    """

    fallback = kwargs.pop("default", None)
    table = _ZH if _current == "zh" else _EN
    text = table.get(key)
    if text is None:
        # Fall back to the other language, then to the caller's default, then to
        # the key itself (which used to leak "organize.no_duplicates" into the UI).
        text = (_EN if _current == "zh" else _ZH).get(key)
    if text is None:
        text = fallback if isinstance(fallback, str) else key
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

        # Apply the persisted language on startup. The widgets are usually built
        # after this store, so notify them once the event loop runs: without the
        # deferred emit an app started in English kept the Chinese labels that
        # were baked in during construction.
        saved = self._settings.get("ui.language", "zh")
        set_language(saved)
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, lambda: self.languageChanged.emit(current_language()))

    def _on_settings_changed(self, key: str, value: object) -> None:
        if key == "ui.language" and isinstance(value, str):
            set_language(value)
            self.languageChanged.emit(value)


__all__ = ["LanguageStore", "current_language", "set_language", "tr", "translate_qt"]
