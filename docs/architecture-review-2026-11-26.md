# 架构审查报告：缝补式实现与技术债盘点

> 审查日期：2026-11-26
> 审查范围：`src/iPhoto` 全部 22 个包（约 46,000 行 Python），重点 6 个切片：agent/ai、gui/coordinators、gui/ui（widgets/controllers）、gui 核心（main/viewmodels/services）、bootstrap/library/cache/infrastructure/io/utils、people/core/application/domain
> 审查方式：分切片只读审计 + 对高危结论做代码复核（下文标「已验证」的均有实际代码位置）
> 参照架构：`docs/finished/referactor/planning/03-target-architecture.md`（MVVM + Clean Architecture：domain / application / infrastructure / bootstrap=组合根 / gui）

---

## 0. 结论摘要

**整体判断：骨架是对的，边缘在烂。** `domain/` 与 `application/` 基本不依赖 Qt，端口也有类型；真正的问题集中在三处：

1. **跨层靠"全局可变对象"通信**——`search_status()`、`background_tasks()`、`ai_features.set_settings_provider()`、`theme_mode._current_mode`、`_ACTIVE_DOWNLOAD`、`_COORDINATORS`、`_global_instance`。它们不是设计出来的接缝，而是"拿不到依赖时先放个全局"。
2. **组合根同时在最上层和最下层**——`bootstrap/` 被 `library/`（5 处）和 `gui/ui/widgets/` 反向依赖，而 `bootstrap/library_session.py` 又反过来 import `gui` 并 new `QThread`/`QDialog`。
3. **功能长在响应它的那个 slot 里**——搜索/查重/智能相册的用例、原始 SQL、位置反解都写在 `main_coordinator.py`（2947 行）内部；同一件事因此存在 2 套实现（两套下载器、两个搜索管线、两个 preview 窗口类、两个组合根）。

**最紧急的 5 项（都在 A 节，全部已复核）：**

| # | 位置 | 后果 |
|---|---|---|
| A1 | `gui/coordinators/navigation_coordinator.py:430` | 移除多个库根之一时 `NameError`（`library_root`/`all_roots`/`path` 都不在该作用域） |
| A2 | `gui/coordinators/main_coordinator.py:2374,2467` | `OrganizeService(...)` 传了它没有的两个参数 → 查重/智能相册必然 `TypeError` |
| A3 | `library/scan_coordinator.py:123,171` | 读 `_scan_service`/`_people_service`，而宿主只存 `_services` → 永远 `None`，worker 各自 new 服务 |
| A4 | `infrastructure/services/library_asset_runtime.py:56` | 用 `Path.home()` 表示"未绑定库" → 每次启动都在用户目录建 `~/.iPhoto/global_index.db` 与缩略图缓存 |
| A5 | `agent/services/face_models.py:70`、`ocr_models.py:63` vs `search_models.py:131` | 模型根解析用了不同的 `parents[N]`：人脸/OCR 落在 `src/extension/models`，搜索落在 `extension/models`（这条是本次审查期间新引入的债，见 B2） |

**分层健康度：**

| 层 | 依赖方向 | 主要违规 | 评分 |
|---|---|---|---|
| domain | 干净（无 Qt、无 IO） | `pinned_state_service` 把中英文标签当身份逻辑 | ★★★★☆ |
| application | 基本干净（有端口） | 少数服务直接开别人的 SQLite（`ai_coverage`） | ★★★★☆ |
| infrastructure | 大致向下 | `library_asset_runtime` 的 home 目录、`exiftool` 硬编码开发机路径 | ★★★☆☆ |
| bootstrap | **双向** | import gui、内嵌 QDialog/QThread；同时被 `library/` 反向依赖 | ★★☆☆☆ |
| gui | **不是叶子** | 直接 `sqlite3` 读索引库、import bootstrap/people/infrastructure 具体类、`app.allWidgets()` 探测 | ★★☆☆☆ |
| library | **向上依赖 bootstrap** | 5 处 import bootstrap + 自建 `LibraryScanService` 回退 | ★★☆☆☆ |

---

## 1. 判定标准：什么算"缝补式"

本次审查只标记满足下列任一条的代码，并在每条给出"为什么是补丁"与"怎么长回架构里"：

- **全局可变状态代替依赖注入**：模块级 `_instance` / `_provider` / `_current_mode`，谁都能改，生命周期无人管；
- **显示文本当协议**：用 `if "完成" in message`、`if chosen.text() == "Delete"` 之类的字符串判断驱动行为；
- **硬编码代替配置**：机器专属路径、魔法阈值、文件名、schema 名、用户可见文案写死；
- **特例分支代替数据**：每加一种情况就加一个 `elif`，本可以是注册表/策略表；
- **越层直达**：gui 直接开库/读别人表/拿私有属性；下层反向 import 组合根；
- **同一概念两套实现**：两套下载器、两个搜索、两个空状态机、两个组合根；
- **为让测试过上而在生产代码里加兼容**：`hasattr` 补状态、`_is_unconfigured_mock`、`__new__` 构造；
- **超出单文件容量的"什么都会"对象**（>800 行且职责跨 3 个以上领域）。

---

## 2. A 类：已确认的真实缺陷（先修，与架构无关也该修）

### A1. 移除库根时的 `NameError`（已验证）

`gui/coordinators/navigation_coordinator.py`

- `_handle_exclude_album()`（~380 行）只处理"路径是库根"的分支就 `return`；
- 处理"相册"的那一半被错误追加到了 `_update_checked_album_paths_after_removal()` 里，从 430 行开始用 `library_root`、`all_roots`、`path`——**这三个名字在该方法里都没有定义**（方法参数只有 `removed_root`）。

后果：多库场景下移除其中一个库根 → `NameError`；而"从库里排除某个相册"实际什么都不做。

**修法**：把这段搬回 `_handle_exclude_album(album_path)`，并且不要留在 GUI——引入 `application/albums/exclude_album.py::ExcludeAlbumUseCase.run(root, rel)`（负责 manifest 读写 + 触发重扫），coordinator 只调用一次。

### A2. `OrganizeService` 构造签名不匹配（已验证）

`agent/services/organize_service.py:51` 的签名是 `__init__(asset_repository, library_root)`；
`gui/coordinators/main_coordinator.py:2374` 与 `:2467` 却传 `embedding_service=` / `embedding_repository=`。

后果：`查找重复照片` 与一键智能相册在拿到会话后必然 `TypeError`，只会被上层 `except` 吞掉。

**修法**：这两处改为 `OrganizeService(asset_repository=..., library_root=...)`；同时把 `QThreadPool` 那段从 coordinator 移到 `agent/workers/organize_worker.py`，让"分组策略"变成可测的纯函数（见 B9）。

### A3. 扫描器读的是过期的属性名（已验证）

- `library/scan_coordinator.py:123` → `getattr(self, "_scan_service", None)`
- `library/scan_coordinator.py:171` → `getattr(self, "_people_service", None)`
- 宿主 `LibraryRuntimeController` 只保存 `self._services`（`runtime_controller.py:131`、`:409`）

后果：两个 `getattr` 恒为 `None`，于是 `ScannerWorker` / `FaceScanWorker` 各自 new 一个服务（`workers/scanner_worker.py:89`、`filesystem_watcher.py:104`），会话绑定的服务从未真正传给它们——这是"人脸索引一直是 0 行"这类现象的同类根因之一。

**修法**：`ScanCoordinatorMixin` 通过 `self._services.scan_service` / `self._services.people_service` 取；更彻底见 P2（`ScanCommands` 端口注入）。

### A4. 用 `~` 表示"没有库"（已验证）

`infrastructure/services/library_asset_runtime.py:56`：未绑定库时返回 `Path.home()`，随后 `:61` 在其下建缩略图缓存，`get_global_repository` 又经 `ensure_work_dir` 建 `~/.iPhoto/global_index.db`。

后果：每次冷启动都往用户目录写 schema 与缓存；任何拿不到库的调用方静默拿到"根目录是家目录"的仓储。

**修法**：`LibraryAssetRuntime` 持有 `Optional[repository/cache]`，`assets`/`thumbnails` 在未绑定时抛 `LibraryUnavailableError`；路径只从 `WorkspaceLocator(library_root)` 推导。删掉 `Path.home()` 分支。

### A5. 三套模型根解析不一致（本次审查期间新增的债）

| 注册表 | 根目录算式 | 实际落到 |
|---|---|---|
| `search_models.py:131` | `parents[4]` | `extension/models` |
| `face_models.py:70` | `parents[3]` | `src/extension/models` |
| `ocr_models.py:63` | `parents[3]` | `src/extension/models` |

后果：同一台机器上存在两份 `extension/models`；用户按提示把 OCR 模型放进 `extension/models/ocr-*/`，程序却在 `src/extension/models` 里找 → 显示"未下载"；反之亦然。

**修法**：见 B2，统一到一个 `ModelCatalog` + 一个 `install_root()`。

### A6. 覆盖率服务直接读别人的库表（已验证）

`agent/services/ai_coverage.py:82`（`ocr_index.db`）、`:99`（`faces/face_index.db`）、`:87`（`ocr_processed`）、`:104`（`persons`），且 `_photo_total` 与 `search_coverage.count_photos` 是同一段 SQL 的两份拷贝。

后果：表名/工作目录一变，设置页静默显示 `0/0`；两份照片计数可能对 live photo 的过滤口径不一致。

**修法**：`CoverageQuery` 用例，组合三个窄端口：`OCRRepository.count_processed()`、`PeopleService.person_count()`、`IndexStore.photo_count()`。删掉 `ai_coverage` 里的所有裸 SQL。

### A7. GUI 线程里的阻塞与子进程

- `gui/coordinators/edit_coordinator.py:989`：在控件信号槽里同步 `ffprobe`（`utils/ffmpeg.py:80`，`timeout=30`），只为拿一个旋转角度；同一份元数据在 `_start_video_edit_load` 里已经取过。
- `core/export.py:544`：视频导出私建一个 `Popen(..., stderr=PIPE)`，**边写 stdin 边不读 stderr**，最后才读（`:563`）：编码器一"话多"就会管道写满死锁 UI；二进制名 `'ffmpeg'` 写死（`:496`），Windows 启动参数块与 `utils/ffmpeg.py:91` 重复。
- `utils/exiftool.py:54-55`：候选路径硬编码 `D:/programs/miniconda3`、`D:/programs/anaconda3`（开发机专属）。

**修法**：`FfmpegFrameEncoder`（一次性解析二进制、stderr 走读线程、带超时与清理）放 `utils/ffmpeg.py`；`TrimThumbnailPlan.for_meta()` 成为纯函数、只吃已取到的 meta；exiftool 发现逻辑收敛到 `infrastructure/tools/tool_locator.py`（env + 配置 + PATH，不写死盘符）。

---

## 3. B 类：结构性补丁（按主题）

### B1. 全局单例当跨层回信道（7 处）

| 位置 | 形态 | 被谁写 / 被谁读 |
|---|---|---|
| `agent/services/search_status.py:86` | `_status` 懒加载单例 + `search_status()` | 编码器（infra）写设备、coordinator 写进度、settings 对话框写覆盖率并读 |
| `gui/services/background_tasks.py:112` | `_registry` 单例 | coordinator 写任务、状态栏控件与设置页读 |
| `agent/services/ai_features.py:28` | `set_settings_provider(lambda: settings)` | bootstrap 注册，扫描器读取；`_settings()` 吞异常后回落到常量默认值 |
| `gui/ui/theme_mode.py:19` | `_current_mode` | ThemeManager 写、所有对话框读，还要 `reset_current_mode()` 给测试擦屁股 |
| `gui/ui/controllers/map_extension_download_controller.py:42` | `_ACTIVE_DOWNLOAD` + `global` | 两个入口控制器互相读对方私有属性来"去重" |
| `people/index_coordinator.py:468` | `_COORDINATORS: dict[Path, ...]` | 任何调用方都能把已关闭的 coordinator 复活 |
| `cache/index_store/repository.py:52` | `_global_instance`（按路径替换） | 6 个适配器直接 import 取用；`utils/pathutils.py:24` 的 `_KNOWN_LIBRARY_ROOTS` 由 GUI 写入 |

**共性**：它们都出现在"某个依赖拿不到"的时刻，选择了"先放个全局"，于是：生命周期无人拥有、测试必须手工重置、同一进程里无法有两个上下文（两个库、两个窗口、headless 用例）。

**重构方案（统一形制，逐个替换，不搞大爆炸）**：

1. 在 `application/contracts/` 定义**窄端口**：`AiFeatureSwitches`（face/ocr 开关）、`IndexStatusSink`（set_device/set_indexing）、`BackgroundTaskSink`（start/update/finish）、`ThemeService`（is_dark/themeChanged）、`ModelSelection`（active/set）。
2. 实现放 `infrastructure/`（读写 `SettingsManager`）或 `gui/viewmodels/`（`SearchIndexViewModel`、`TaskListViewModel`、`ThemeViewModel`）。
3. **只由 `bootstrap/runtime_context.py` 构造一次**，挂到 `RuntimeEntryContract`（如 `context.search`、`context.tasks`、`context.theme`），构造函数注入到 coordinator / settings 对话框 / 编码器。
4. 删除 `search_status()`、`background_tasks()`、`set_settings_provider()`、`_current_mode`、`_ACTIVE_DOWNLOAD`、`_COORDINATORS`、`get_global_repository()` 这些**取用函数**，保留类本身。

> 收益：设置页不再能"伪造"索引覆盖率（现在它能 `set_coverage` 全局对象）；编码器不再 import `agent.services`；测试不再需要 `monkeypatch.setattr(module, "_registry", ...)`。

### B2. 三套模型注册表 + 两套下载器（本次新增债，优先偿还）

**三套注册表**：`search_models.py` / `face_models.py` / `ocr_models.py` 各自实现 `ModelSpec` 数据类、`available_models()`、`resolve_spec()`、`is_installed()`、`_active_key` 全局、`models_root()`。三份代码已开始漂移：

- 根目录算式不同（A5）；
- "当前模型"存在两份真相：settings 里的键 **和** 模块全局 `_active_key`，GUI 同时写两处（`settings_dialog.py`），worker 读全局（`face_scan_worker.py:139`、`ocr_engine.py:41`）；
- 体积字段语义不一（`download_mb` / `total_mb`），导致我刚修过一轮"一个带 MB 一个不带"。

**两套下载器**：`agent/infrastructure/clip_downloader.py`（HF→ModelScope，`progress_callback`，且 `os.environ["HF_ENDPOINT"]` 全局副作用、硬编码 `127.0.0.1:7890` 代理提示）与 `agent/infrastructure/search_model_download.py`（同样的双源回退，但按字节报进度）。应用里两条路径都还在用。

**重构方案**：

```
agent/services/model_catalog.py     # 唯一 ModelSpec + 三个 catalogue（search/face/ocr）
infrastructure/model_store.py       # 唯一 install_root()/is_installed()/dir_for()
infrastructure/model_downloader.py  # 唯一双源下载（search_model_download 升级而来）
application/contracts/model_selection.py  # 读写的只有 settings 键，无全局
```

- `clip-b32` 直接表达成 `Catalogue("search")` 里的一项，删 `clip_downloader.py`；
- 代理/镜像作为**值**从设置传入下载调用（不再改 `os.environ`）；
- 三个 `_active_key` 全局删除，改由 `ModelSelection`（bootstrap 注入）持有；
- 顺手统一 `models_root` 为**一个**目录（建议仓库根 `extension/models`，与文档/用户预期一致），并迁移 `src/extension/models` 下的 buffalo/rapidocr。

### B3. GUI 越层与私有访问

- **直接开 SQLite**：`gui/ui/controllers/dialog_controller.py:180,196,225`（`SELECT COUNT(*) FROM assets` 判空库，重复三遍，硬编码 `"global_index.db"`）、`gui/coordinators/main_coordinator.py:2916`（`SELECT * FROM assets` 给位置索引）。
- **越过 store 私有**：`gui/viewmodels/gallery_viewmodel.py:807` 调 `store._reset_window_state()` 再手工 emit 它的信号——清空语义现在有两个 owner。
- **按私有属性扫描全应用**：`gui/viewmodels/gallery_list_model_adapter.py:379` 遍历 `app.allWidgets()`，用 `hasattr('dismiss_loading_state')` + `hasattr('_loading_label')` 猜控件；`gui/main.py:823` 再干一遍。
- **import 具体实现**：`gui/ui/widgets/people_dashboard_widget.py:23` import `bootstrap.library_people_service` 并自己 `PeopleService()`；`gui/services/*` import bootstrap + `people.service`；`playback_coordinator.py:1275` 自建 `SessionMapRuntimeService()` 兜底；`gui/services/people_service_resolver.py:62` 甚至在**生产代码里 `_is_unconfigured_mock` 嗅探 `unittest.mock`**。
- **边界检查被写成白名单**：`tools/check_layer_boundaries.py:77` 只禁了 `gui/coordinators|services|controllers|models|viewmodels`，`gui/ui/widgets/` 不在名单里——所以上面那处 import 能通过"架构测试"。

**重构方案**：把端口补齐（`ScanPort`/`AssetOperationPort`/`PeoplePort`/`ThumbnailPort`/`AssetQueryPort`/`IndexProbe`），由 bootstrap 注入；`MenuContext` 移出 `gui.ui`；`NullPeopleService` 取代 mock 嗅探；把 `gui/ui/widgets/` 加进边界检查。

### B4. God Object 与"混入式"拆分

| 对象 | 规模 | 症状 |
|---|---|---|
| `gui/coordinators/main_coordinator.py` | 2947 行、~140 方法 | 既是窗口协调器，又是搜索管线、查重、智能相册、地图导航、偏好持久化、原始 SQL 所在地；`__init__` 手搭 ~30 个协作者 |
| `gui/viewmodels/gallery_collection_store.py` | 1594 行、87 方法、14 处 `⭐ 关键修复` | 分页数学、异步池、扫描合并、时间线桶、乐观移动全在一类 |
| `people/state_repository.py` | 1922 行、57 方法 | 9 类表 + 两个 DB 文件；`face_repository.py:930` 又把 group/cover/order 重新声明一遍 |
| `library/runtime_controller.py` | 5 个 mixin ~1.8k 行 | 靠 `getattr(self, '_xxx')` 隐式接口，命名已过期（A3） |
| `gui/coordinators/playback_coordinator.py` | 26 个构造参数 | 用 `hasattr` 补状态以便 `__new__` 测试 |
| `gui/coordinators/edit_coordinator.py` | 18 组 preview/commit 方法 ~390 行 | 只因侧栏每组一个信号而复制粘贴 |

**重构方案（按"一次搬一块、每块可独立验收"）**：

- `main_coordinator` → 组合对象 + 兄弟协调器：`search_coordinator`、`map_navigation_coordinator`、`agent_coordinator`（查重/智能相册）、`window_chrome`。
- `gallery_collection_store` → `GalleryWindowPager` + `ScanRefreshCoalescer` + `TimelineBucketIndex` + 注入的 `LoadScheduler`；store 只留行缓存与选择。
- `state_repository` → `PersonProfileRepository` / `ManualFaceRepository` / `PeopleGroupRepository` / `PersonPreferenceRepository`，由 `PeopleStateStore` 门面聚合；`FaceRepository` 依赖门面而非重复声明。
- `runtime_controller` → 组合：`LibraryTreeIndex`、`ScanScheduler`、`MediaWatchDebouncer`、`LibrarySessionBinder`。
- `playback_coordinator` → 一个 `PlaybackServices` 冻结数据类 + 一个 `PlaybackView` 控件包。
- `edit_coordinator` → `AdjustmentGroup` 描述符表 + 一个通用 preview/commit handler。

### B5. 用显示文本做控制流 + 硬编码文案

- **控制流**：`main_coordinator.py:1357` `if "完成" in message:` 决定消息是否自动过期；`:1904-1907` `if "模型" in message ... elif "索引"` 决定搜索进度阶段；`info_panel.py:155-162` 按菜单项文本分发（明明 `:174` 已经生成了 `action_id`）；`navigation_coordinator.py` 里 `"all photos"`/`"favorites"`/`name == tr("sidebar.albums")` 七分支字符串梯子。
- **文案**：`organize_service.py:153` `f"{year_month} 的照片"`、`ai/workers/embedding_worker.py:114` `"开始生成嵌入向量..."`、`library/scan_coordinator.py:476` `"正在识别图片文字..."`、`clip_downloader.py` 整段说明/错误文本、`info_panel.py` 中文占位符与英文标签混排（`:239` `title="New Person"` + `placeholder="姓名"`）。仓库里明明有 `i18n` 与 `tr()`。

**重构方案**：

- 信号携带**结构化载荷**：`ScanStatus(kind: ScanStatusKind, key: str, **params)`，由视图 `tr(f"status.{key}")` 渲染；删掉字符串嗅探。
- 菜单动作 `action.setData(action_id)`，按 id 分发。
- 侧栏路由表：`{node_id: (UseCase, EmptyState)}`，`_handle_static_node` 变成一次查表。
- 服务产出**草稿数据**（`SmartAlbumDraft(group_type, month|place|range, asset_ids)`），文案在视图层用 `tr()` 拼；下载器/worker 的提示改 key。

### B6. 空状态/加载状态是"四布尔 + 三个重叠 Label"机

`gallery_grid_view.py`：`_scan_completed` / `_is_scanning` / `_query_loading` / `_empty_mode` 驱动 `_empty_label` / `_loading_label` / `_scanning_label`；同一段"强制隐藏所有遮罩"被复制在 830、866、978、982 四处，`_do_update_empty_state` 里还有一支死分支（830 已 return）。

**重构方案**：`GalleryEmptyStateViewModel` 单一枚举 `SCANNING | LOADING | EMPTY(mode) | READY`，视图只渲染一个占位标签；删 `set_scan_started/set_scan_completed/set_empty_mode/dismiss_loading_state` 的四处入口。

### B7. 同一件事的两套实现 / 死脚手架

| 重复 | 说明 |
|---|---|
| 两套下载器 | 见 B2 |
| 两套搜索 | `agent/services/search_service.py` 实现了另一套阈值与 OCR 合并，**唯一构造点 `LibrarySession.get_search_service()` 没有调用者**；真实搜索在 `main_coordinator.py:1565-1670` |
| 两个编码器 | `CLIPFastEmbeddingService`（ONNX，自己的线程预算）只被一个脚本注释提到 |
| 两个 preview 窗口类 | `preview_window.py` 内按平台/资产嗅探二选一 |
| 两个组合根 | `iPhoto/di/bootstrap.py:13` 无调用者；`runtime_context.py:270` 返回"空 DI 容器"以安抚旧调用方 |
| 两处 badge 几何 | `badge_renderer.py:146` 绘制、`gallery_grid_view.py:1076` 注释里抄一遍做命中测试 → 改样式就会点不中 |

**重构方案**：把 `search_service` 提升为唯一用例并让 GUI 调它（顺带删掉 coordinator 内联的排名逻辑）；`CLIPFastEmbeddingService` 若日后要 ONNX 后端，做成 `EmbeddingEncoder` 协议的第二个实现；preview 收敛为"一个窗口 + 两种 renderer"；`iPhoto/di` 要么删、要么成为唯一组合根；badge 矩形抽成 `BadgeRenderer.favorite_badge_rect(rect)` 供绘制与命中测试共用。

### B8. 为让测试过而在生产代码里加兼容

- `playback_coordinator.py:1704` 用 `hasattr` 重建 `__init__` 已赋值的三个属性，因为测试用 `__new__` 构造（`tests/gui/coordinators/test_playback_coordinator.py` 有 100+ 处）。
- `people_service_resolver.py:62` 嗅探 `unittest.mock`。
- 测试大量 patch 私有/全局：`tests/test_ai_features.py:306` 手动"restore for other tests"；`tests/test_search_models.py:14` 用 autouse fixture 重置全局；`tests/test_background_tasks.py:35` patch 模块级 `_registry`。

**重构方案**：注入端口后，测试改用真接口 + 临时目录（`ModelCatalog(install_root=tmp)`、`FakeSettings`、`FakeOCRRepository`）；禁止 `gui/` 内 `PeopleService()` 构造（加测试断言）；`hasattr` 兼容块随 `PlaybackServices` 一起删除。

---

## 4. 参考分层（目标态）

```
gui/views ──▶ gui/viewmodels ──▶ application/ports ◀── infrastructure/adapters
    │                │                    ▲                    ▲
    └── gui/coordinators ────────────────┘                    │
                          bootstrap/runtime_context ───────────┘
                          （唯一组合根：构造、注入、生命周期）
domain/  ◀── application/ ◀── infrastructure/    （只允许这个方向）
```

约束建议写进 `tools/check_layer_boundaries.py`（现在是白名单，改成禁止方向的黑名单）：

1. `domain/`、`application/` 不得 import `PySide6`；
2. `library/`、`cache/`、`infrastructure/`、`utils/` 不得 import `bootstrap/`、`gui/`；
3. `gui/` 不得 import `cache.index_store`、`bootstrap.*` 具体实现、`people.service` 具体构造；
4. `agent/services`、`agent/infrastructure` 不得 import Qt（`search_status` 目前违反）；
5. 禁止新增模块级 `_instance`/`_registry`/`_provider`（可用 grep 规则加进 CI）。

---

## 5. 重构路线（按"每批可独立交付、可回滚"）

### P0 · 止血（半天，纯缺陷，不涉架构）

1. A1 修复 `navigation_coordinator` 的错误放置块（或先改成显式 `NotImplementedError` 以免误删库根）。
2. A2 修正 `OrganizeService` 两处构造。
3. A3 改用 `self._services.*`。
4. A5 统一 `models_root()`（先加一条断言日志，提示两个目录并存）。
5. A7 的 `export.py` stderr 死锁（最小改动：stderr 起读线程）。

**验收**：新增 4 个回归测试（移根、查重、扫描器拿到会话服务、导出不阻塞）。

### P1 · 单例收口（1–2 天）

- 新建 `SearchIndexViewModel`、`TaskListViewModel`、`AiFeatureSwitches`、`ThemeService`，bootstrap 构造一次并注入；
- 删 `search_status()`、`background_tasks()`、`set_settings_provider()`、`theme_mode._current_mode`；
- `SearchIndexStatus`/`BackgroundTaskRegistry` 从"全局取用"降级为"被注入的对象"。

**验收**：`grep -rn "search_status()\|background_tasks()\|set_settings_provider" src/` 为空；设置页不再能写覆盖率；agent 层无 Qt import。

### P2 · 模型层统一（2–3 天，配合 P1）

- `model_catalog.py` + `model_store.py` + `model_downloader.py` + `ModelSelection`；
- 删 `clip_downloader.py`，迁移 `src/extension/models` 到统一根；
- 三套注册表的 GUI 读写路径改为走 `ModelSelection`。

**验收**：只有一处 `parents[N]`；切换模型只写 settings；离线单测可枚举"未安装"。

### P3 · GUI 越层收口（3–5 天）

- `LibraryContext`（root + repository + workspace locator）替代 `get_global_repository`/`pathutils` 全局；
- 端口 `ScanCommands`/`AssetQueryPort`/`PeoplePort`/`ThumbnailPort`/`IndexProbe`，bootstrap 注入；
- 删 GUI 里的 3 处裸 `sqlite3`、`app.allWidgets()` 探测、`_reset_window_state` 召唤；
- 边界检查改黑名单，加入 `gui/ui/widgets/`。

**验收**：`grep -rn "sqlite3.connect" src/iPhoto/gui` 为空；`grep -rn "from iPhoto.bootstrap" src/iPhoto/{library,people,cache,infrastructure,utils}` 为空。

### P4 · 巨类拆分（1–2 周，逐块）

顺序建议（每步独立 PR）：`gallery_collection_store` → `main_coordinator` 的搜索/查重/地图三块 → `state_repository` → `runtime_controller` 组合化 → `playback/edit` 的服务包与描述符表。

**验收**：无文件 >800 行；`main_coordinator` <500 行且只做组装；`test_playback_coordinator` 不再需要 `__new__`。

### P5 · 文案与控制流清理（与 P4 并行，2–3 天）

- `ScanStatus` 结构化；菜单按 `action_id`；侧栏路由表；`organize`/worker/下载器文案进 i18n；
- 空状态机合为单一枚举。

**验收**：`grep -rn '"完成" in\|"索引" in message\|chosen.text() ==' src/` 为空；`i18n` 覆盖测试通过；切英文后扫描提示仍正常。

---

## 6. 建议"不要做"的事

- **不要为了统一而重写 main_coordinator**：它是历史上功能落地的位置，整体重写风险远大于按块搬迁；按 P4 顺序逐块搬，每块保留现有测试。
- **不要引入重量级 DI 框架**：现有 `RuntimeEntryContract` + `LibraryServiceRegistry` 已足够做构造注入，`iPhoto/di` 先删掉比先扩展它更划算。
- **不要先把 Qt 从 people 层拔掉**（`index_coordinator` 是 QObject）：先注入 `SnapshotPublisher` 端口，让 GUI 依赖端口，再考虑换实现。

---

## 7. 附：本次审查的确认情况

| 结论 | 状态 |
|---|---|
| A1 `navigation_coordinator` 未定义变量 | **已验证**（grep 该方法作用域无赋值） |
| A2 `OrganizeService` 签名不匹配 | **已验证**（签名 2 参 vs 调用 4 参） |
| A3 `_scan_service`/`_people_service` 恒 None | **已验证**（宿主只有 `_services`） |
| A4 `Path.home()` 当未绑定库 | **已验证**（代码路径 + 启动时创建 `~/.iPhoto`） |
| A5 模型根 `parents[3]` vs `parents[4]` | **已验证**（两目录同时存在） |
| 空状态四布尔机 | **已验证**（4 处重复隐藏块 + 死分支） |
| 「`_on_load_error` 引用未定义 `row_cache`」 | **未复现**：该处已重构，`_on_load_error` 当前干净（审计报告此项判为过期） |
| preview 双窗口类、`_ACTIVE_DOWNLOAD` 细节 | 抽样复核存在，未逐行验证全部分支 |

## 8. 实施进度（2026-11-27 更新）

| 阶段 | 状态 | 说明 |
|---|---|---|
| P0 · A1 移除库根 `NameError` | 已完成 | 相册分支搬回 `_handle_exclude_album`，移根/排除相册两条路径各自独立 |
| P0 · A2 `OrganizeService` 构造 | 已完成 | `gui/services/organize_workers.py` 统一按 `(asset_repository, library_root)` 构造（`50093d2`） |
| P0 · A3 扫描器过期属性名 | 已完成 | `scan_coordinator` 改用 `self._services.scan_service` / `.people_service` |
| P0 · A4 `Path.home()` 兜底 | 已完成 | 未绑定时抛 `LibraryUnavailableError` |
| P0 · A5 模型根统一 | 已完成 | `agent/services/model_paths.py` 是唯一答案：规范根 + 兼容旧根 + 一次性迁移 |
| P0 · A6 覆盖率服务裸 SQL | 已完成 | `ai_coverage` 只调 `face/ocr/people` 三个窄口 |
| P0 · A7 导出子进程 | 已完成 | stderr 读线程早已就位；补上 PySide6 帧转换（`memoryview` 无 `setsize`，视频导出必崩）与 `ffmpeg/ffprobe` 统一解析（`3b7038a`） |
| P1 · 单例收口 | 已完成 | `search_status()` / `background_tasks()` / `set_settings_provider` 在 `src/` 中已无引用 |
| P2 · 模型层统一 | 已完成 | 三套注册表共用 `model_paths`；`ModelSelection` 已由 bootstrap 构造并注入（`f650cd4`），注册表只剩目录职责 |
| P3 · GUI 越层收口 | 基本完成 | GUI 内 `sqlite3.connect` 为 0；`library/runtime_controller.py` 仅剩一处 `TYPE_CHECKING` 的 `LibrarySession` 类型引用（运行期无依赖）。端口 `ScanCommands`/`AssetQueryPort` 等仍未正式化 |
| P4 · 巨类拆分 | 进行中 | `main_coordinator` 2924 → **1215** 行（搜索线程、嵌入 worker、AI 索引生命周期、位置视图/轨迹、查重/智能相册展示、窗口偏好装配、开库/工作区切换、画廊交互/前台让路已搬出：`search_coordinator.py` 499 行、`organize_coordinator.py` 104 行、`window_preferences.py` 163 行、`library_open_coordinator.py` 125 行、`gallery_interaction_coordinator.py` 167 行、`ai_index_coordinator.py` 239 行、`location_coordinator.py` 298 行）；`gui/viewmodels/gallery_collection_store.py` 1612 → **747** 行（扫描刷新策略/路径映射拆到 `gallery_scan_refresh.py` 315 行，窗口加载/预取/节流拆到 `gallery_load_window.py` 464 行，收藏/元数据/删除/乐观移动拆到 `gallery_mutations.py` 166 行）；`people/state_repository.py` 1922 → **362** 行，拆成 state_groups/state_faces/state_profiles/state_ordering 四个 mixin；此外 `people/face_repository.py` 1398 → 208 行（face_queries/face_mutations/face_merges）、`people/pipeline.py` 954 → 710 行（insightface_compat/clustering_math）、`cache/index_store/repository.py` 1129 → 267 行（asset_writes/asset_queries/sql_helpers）；`playback_coordinator.py` 1890 → 668 行（playback_location/render/faces/info + constants）；`gui/coordinators/edit_coordinator.py` 1626 → 756 行（edit_video/edit_adjustments/edit_support）；仍有 **19** 个文件 >800 行 |
| P5 · 文案与控制流 | 基本完成 | 文本驱动控制流清零并有守卫（`tests/architecture/test_no_text_based_control_flow.py`）；AI 索引/搜索/人脸扫描文案进 i18n（`08130d7`、`8c139eb`、`ff4a2ac`）；地图轨迹、人物视图提示也已进 i18n（`8b3be4b`、`ff4a2ac`）；空状态机尚未合为单一枚举 |

### 已知缺口（有测试记录，未修）

- 扫描结果落在可视窗口之外时，画廊总数不刷新（`tests/gui/viewmodels/test_gallery_collection_store.py` 两条 `xfail(strict=True)`）。窗口不重载是刻意设计，缺的是「只刷计数」的路径。
- `tests/gui/coordinators/test_playback_coordinator.py` 仍有 15 条用例用 `__new__` 手工桩属性，跟当前装配对不上；路线图里「不再需要 `__new__`」那一项还没做。
- `playback_coordinator.py`（668 行）、`info_panel.py`（1690 行）、`edit_coordinator.py`（756 行）、`gallery_collection_store.py`（747 行）等 19 个文件仍在 800 行以上；`state_repository.py` 已拆完，不再是其中之一。
