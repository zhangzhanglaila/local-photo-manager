# iPhoto 本地照片管理器

一个类似 macOS 照片应用的**文件夹原生**照片管理工具，支持 Windows、macOS 和 Linux。无需上传云端，直接管理本地照片和视频。

> 基于 [iPhotron](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager) 项目开发，感谢原作者 Haibin Zhao。

---

## 核心功能

| 功能 | 说明 |
|------|------|
| 照片浏览 | 网格视图、详情视图、幻灯片播放，支持缩放/平移 |
| 实况照片 | 自动配对 HEIC/MOV，支持播放 Live Photo 视频 |
| 照片地图 | 基于 GPS 数据在地图上标记照片位置，支持聚合和缩放 |
| 照片编辑 | 亮度/对比度/饱和度/曲线/裁剪/旋转/透视校正等 |
| 视频播放 | 内置视频播放器，支持旋转/裁剪/导出 |
| 人物识别 | 基于 InsightFace 的人脸检测和分组（需安装额外依赖） |
| 多图库管理 | 支持同时管理多个文件夹根目录 |
| 相册与子相册 | 库内可创建相册与一级子相册，支持重命名、置顶到侧栏 |
| 侧边栏过滤 | 按文件夹/相册勾选筛选显示的照片 |
| 元数据读取 | 支持 EXIF/GPS/拍摄日期等，基于 ExifTool |
| 跨平台 | Windows / macOS / Linux |
| 自定义工作目录 | 支持将工作文件存放在指定位置，保持照片文件夹干净 |

## 技术栈

- **语言：** Python 3.12+
- **GUI：** PySide6 (Qt 6)
- **数据库：** SQLite (WAL 模式)
- **图像处理：** Pillow, OpenCV, NumPy, rawpy
- **视频处理：** PyAV (FFmpeg)
- **地图渲染：** OsmAnd 矢量地图 + OpenGL
- **人脸检测：** InsightFace + ONNX Runtime
- **元数据：** ExifTool

## 安装

### 前置条件（三种方式都需要）

- **Python 3.12+**
- **ExifTool** — 读取照片 EXIF/GPS 元数据
  - Windows: `choco install exiftool` 或 https://exiftool.org
  - macOS: `brew install exiftool`
  - Linux: `sudo apt install libimage-exiftool-perl`
- **FFmpeg** — 视频封面提取、视频导出
  - Windows: `choco install ffmpeg` 或 https://ffmpeg.org
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`

> **Linux 用户注意：** `rawpy` 依赖 `libraw`：
> ```bash
> sudo apt install libraw-dev    # Debian/Ubuntu
> sudo dnf install LibRaw-devel  # Fedora
> ```

---

### 方式一：完整包（推荐 ⭐）

从 [v6.1.0 Release](https://github.com/zhangzhanglaila/LocalPhotoManager/releases/tag/v6.1.0) 下载 **`iPhotron-v6.1.0-complete.zip`**（~1.4GB）。

源码 + CLIP 模型 + 人脸模型 + 地图扩展全部打包好，一次下载，解压即用：

```bash
# 1. 解压
unzip iPhotron-v6.1.0-complete.zip
cd iPhotron-v6.1.0

# 2. 安装
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -e .

# 3. （可选）如需人物识别
pip install insightface onnxruntime
```

---

### 方式二：源码 + 自动下载模型

Clone 源码，模型在首次使用时自动从网络下载：

```bash
# 1. 克隆
git clone https://github.com/zhangzhanglaila/LocalPhotoManager.git
cd LocalPhotoManager

# 2. 安装
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e .

# 3. （可选）如需人物识别
pip install insightface onnxruntime
```

> 首次启动时：
> - 地图扩展会弹窗提示下载（~975MB，从 GitHub Release）
> - 首次使用语义搜索时弹窗提示下载 CLIP 模型（~580MB，从 HuggingFace 镜像）
> - 人脸模型在首次使用人物识别时自动下载（~300MB，从 InsightFace CDN）

---

### 方式三：源码 + 手动下载模型

Clone 源码，从 [v6.1.0 Release](https://github.com/zhangzhanglaila/LocalPhotoManager/releases/tag/v6.1.0) 逐个下载模型到指定位置：

```bash
# 1. 克隆
git clone https://github.com/zhangzhanglaila/LocalPhotoManager.git
cd LocalPhotoManager

# 2. 安装
python -m venv .venv
.venv\Scripts\activate
pip install -e .
pip install insightface onnxruntime

# 3. 下载模型并解压到对应目录
# CLIP 模型（语义搜索）
curl -L -o clip-vit-base-patch32.zip https://github.com/zhangzhanglaila/LocalPhotoManager/releases/download/v6.1.0/clip-vit-base-patch32.zip
unzip clip-vit-base-patch32.zip -d extension/models/

# 地图扩展（离线地图）
curl -L -o extension.zip https://github.com/zhangzhanglaila/LocalPhotoManager/releases/download/v6.1.0/extension.zip
unzip extension.zip -d src/maps/tiles/

# 人脸模型
curl -L -o buffalo_l.zip https://github.com/zhangzhanglaila/LocalPhotoManager/releases/download/v6.1.0/buffalo_l.zip
unzip buffalo_l.zip -d src/extension/models/
```

最终目录结构：

```
extension/models/clip-vit-base-patch32/   ← CLIP 模型
src/extension/models/buffalo_l/           ← 人脸模型
src/maps/tiles/extension/                 ← 地图扩展
```

**使用方式：**
- 启动应用后，在搜索框输入关键词（支持中文）
- 首次搜索需要加载模型（约 10 秒）
- 之后搜索瞬间出结果

**支持的搜索示例：**
| 中文 | 英文 |
|------|------|
| 树、花、山 | tree, flower, mountain |
| 海边、日落 | beach, sunset |
| 狗、猫 | dog, cat |
| 美食、蛋糕 | food, cake |

---

### 国内用户特别说明

**人脸模型**下载较慢时，可手动下载（任选其一）：

- **本项目镜像（推荐）：** 从 [v6.1.0 Release](https://github.com/zhangzhanglaila/LocalPhotoManager/releases/tag/v6.1.0) 下载 `buffalo_l.zip`
- **官方源：** 访问 https://github.com/deepinsight/insightface/releases/tag/v0.7 下载 `buffalo_l.zip`

下载后解压，把 `buffalo_l` 文件夹放到 `src/extension/models/` 下：

```
src/extension/models/buffalo_l/
├── 1k3d68.onnx
├── 2d106det.onnx
├── det_10g.onnx
├── genderage.onnx
└── w600k_r50.onnx
```

**地图扩展**下载较慢时，可手动下载：

1. 访问 https://github.com/zhangzhanglaila/LocalPhotoManager/releases/tag/v6.1.0
2. Windows 下载 `extension.zip`，Linux 下载 `extension.tar.xz`
3. 解压后把 `extension` 文件夹放到 `src/maps/tiles/` 下
4. 最终目录结构：`src/maps/tiles/extension/World_basemap_2.obf` 等文件

也可跳过地图扩展，自动降级为基础地图。

## 启动

在项目根目录执行启动命令。

```bash
# Windows 推荐方式：不需要先激活虚拟环境
.venv\Scripts\python.exe -m iPhoto.gui.main

# Windows：如果已经激活虚拟环境
.venv\Scripts\activate        # Windows
iphoto-gui

# macOS / Linux
source .venv/bin/activate
iphoto-gui
```

或直接打开指定相册：

```bash
.venv\Scripts\python.exe -m iPhoto.gui.main D:\APPLE\202406_a   # Windows
# iphoto-gui /path/to/photos                                     # macOS/Linux
```

如果 `iphoto-gui` 命令不可用，先确认已经执行过安装：

```bash
pip install -e .
```

## 环境变量

| 变量名 | 说明 |
|--------|------|
| `IPHOTO_EXIFTOOL_PATH` | 指定 ExifTool 可执行文件路径 |
| `IPHOTO_FACE_MODEL_DIR` | 指定人脸模型目录（默认 `src/extension/models/`） |
| `IPHOTO_FACE_THREADS` | 限制人脸/OCR 推理线程数（默认 `min(4, CPU 核数 ÷ 2)`）。调大可加快扫描，但会挤占界面响应 |
| `IPHOTO_SKIP_FACE_SCAN` | 设为 `1` 可关闭启动后的自动人脸扫描 |
| `IPHOTO_PREFER_OSMAND_NATIVE_WIDGET` | 设为 `0` 可禁用 OsmAnd 原生地图组件 |
| `IPHOTO_OSMAND_EXTENSION_ROOT` | 指定 OsmAnd 地图扩展目录 |

---

## 本版本修复与改进

### 一、性能优化

| 问题 | 修复前 | 修复后 | 修复方式 |
|------|--------|--------|----------|
| 实况照片配对卡死 | O(n²)，13068×3544 ≈ 4600 万次迭代，应用无响应 | O(n)，1.7 秒完成 | 预构建 `videos_by_stem` / `videos_by_folder` 字典索引，候选查找从 O(n) 降至 O(1) |
| 扫描期间点击任何按钮 | 主线程阻塞，弹出"Python 未响应" | 全部异步化，UI 始终流畅 | 路径验证移至 `GalleryLoadWorker` 后台线程；新增 `PathExistsCache` LRU 缓存（20,000 条） |
| 缩略图磁盘加载 | 同步 `QPixmap(disk_file)` 阻塞主线程 | `QRunnable` 后台加载，主线程只做 `QPixmap.fromImage()` | 新增 `ThumbnailDiskLoadTask(QRunnable)`，`QImage` 在后台线程创建 |
| 地图聚类 | 主线程遍历 16000 项 | >1000 项自动切换后台 `_ClusterWorker` 线程 | `_rebuild_photo_clusters()` 按数据量自动选择主线程/后台线程 |
| 地图渲染闪烁 | 每个缩略图到达就重绘一次，`clear_pixmaps()` 清空后逐个闪烁 | 双缓冲，一次性 blit 到屏幕 | `_MarkerLayer` 新增 `_back_buffer` + `_buffer_dirty` 标志 |
| 启动时界面卡死 | 地图 OpenGL 初始化 + 哈希遍历全部行 + 图库树级联加载阻塞主线程 | 懒加载 + O(1) 代次计数器 + 启动遮罩进度条 | 地图组件延迟初始化；`_snapshot_hash()` 改为代次计数器；拆分多步加载 |
| 同一图库 rescan 地图闪烁 | rescan 时清空所有缩略图再重新加载 | 保留已有缩略图，只更新变化部分 | 同一图库 rescan 时不发射 `thumbnailsInvalidated` 信号 |

### 二、Bug 修复

| 问题 | 修复前 | 修复后 |
|------|--------|--------|
| 已删除文件仍显示在 All Photos | 本地文件已删除，缩略图仍可见，点击放大提示文件不存在 | All Photos 视图也验证路径存在性，后台线程过滤不存在的文件 |
| 单击照片无法打开详情页 | 单击无反应，必须双击才能放大查看 | 连接 `itemClicked` 信号到 `_on_asset_clicked`，单击/双击均可打开 |
| Escape 键直接关闭应用 | 按 Escape 无条件调用 `self.close()` 关闭整个窗口 | 移除无条件关闭，Escape 由 `AppShortcutManager` 管理，仅退出全屏 |
| 右键移除文件夹无响应 | 点击"移除"后无反应 | 连接 `excludeAlbumRequested` 信号 |
| 实况照片播放画面倒置 | Windows 上 180° 旋转被重复应用 | 修复旋转检测逻辑，Qt6 FFmpeg 后端自动处理旋转 |
| 实况照片只显示一张 | 配对时间差阈值过窄（3 秒），大量实况照片无法配对 | 阈值放宽至 5 秒；新增同文件名匹配逻辑 |
| 地图标记覆盖层不可见 | 覆盖层不透明，地图被完全遮挡 | 修复覆盖层透明度 |
| Ctrl+C 无法退出 | Qt `eventFilter` 内 `KeyboardInterrupt` 死循环 | Windows 后台看门线程正确处理 Ctrl+C 信号 |
| ExifTool 缺失导致崩溃 | 未安装 ExifTool 时提取元数据直接崩溃 | `ExternalToolError` 异常捕获，弹出中文警告对话框；Windows 自动检测路径 |

### 三、新功能

| 功能 | 说明 |
|------|------|
| 多图库共存 | 支持同时管理多个图库根目录，侧边栏显示所有已添加的图库 |
| 侧边栏文件夹过滤 | 可勾选/取消文件夹，按文件夹筛选显示的照片 |
| 详情页全屏按钮 | 详情页新增全屏按钮，支持沉浸式查看照片 |
| 照片地图面板 | 详情页新增地图面板，显示照片拍摄位置 |
| 扫描状态实时反馈 | 进度条 + 已扫描/总数，扫描过程透明可见 |
| 地图标记动态缩放 | 标记数字徽章随缩放级别动态调整大小 |
| 全局异常捕获 | 未处理的异常记录到日志文件，方便排查问题 |
| 全中文化 UI | 涉及 10+ 文件，包括状态栏、右键菜单、导出/分享提示、对话框、人物面板等 |

### 四、本次更新：稳定性与浏览性能

#### 浏览性能

| 问题 | 修复前 | 修复后 |
|------|--------|--------|
| 浏览时应用持续占用 CPU | 图库窗口被反复重载，空闲时也有约 11 次/秒的模型重置，进程占用约 19 个核 | 重载只在真正需要时发生：空闲 0 次/秒，CPU 降到约 0.13 核 |
| 上下滚动卡顿 | 每次只加载可见的 50 行，滚一下就撞到边界并重载 | 加载 200–1000 行的缓存窗口（前视 1 屏、后视 2 屏），滚动在窗口内不触发重载 |
| 跨月拖动发涩 | 绘制时对未缓存的行逐行同步查库 | 绘制只读缓存，缺行异步补载后单独重绘 |
| 滚轮一次跳太远 | 一格滚轮约 2.8 行 | 一格滚轮约 1.4 行 |
| 扫描人脸/OCR 时看照片卡 | 推理默认按 CPU 核数开线程，一条自动扫描即可占满整机 | 推理线程限制为 `min(4, 核数÷2)`；并且**看照片、滚动、播视频时两条扫描自动暂停**，回到网格后继续 |

#### 数据位置

| 问题 | 修复前 | 修复后 |
|------|--------|--------|
| 照片文件夹里出现状态文件 | 专辑清单写死为 `<照片库>/.iphoto.album.json`；着色器缓存也因启动顺序写进库内 `.iPhoto` | 全部写入自定义工作目录；旧清单首次打开时自动迁移（保留专辑 id） |

#### 正确性修复

| 问题 | 修复前 | 修复后 |
|------|--------|--------|
| 扫描后失效照片仍留在索引 | 重扫库根时清理逻辑一行都删不掉（相对路径与绝对前缀永远匹配不上） | 正确区分"库根"与"库外目录"，重扫会清理已删除/移动的行 |
| 老照片导致扫描中断 | Windows 上 `datetime.timestamp()` 对 1970 年前后的值抛 `OSError` | 换算不再依赖系统 `mktime`，纪元附近的值也能安全处理 |
| 数据库损坏后无法自愈 | SQLite 连接未关闭，Windows 上文件被占用，恢复流程删不掉旧文件 | 连接统一显式关闭，损坏索引可正常重建 |
| 无法创建子相册 | 多库改造后层级判断失效，创建必然报错 | 层级为 库(1) → 相册(2) → 子相册(3)，创建与展示均正常 |
| 地图组件初始化时机 | 需要先手动"激活"才能取到地图控件 | 惰性构建，取用时自动初始化 |
| 人脸扫描失败无细节 | 只记"N 个资源处理失败" | 逐个记录资源与失败原因，便于排查 |

#### 测试

| 项目 | 变化 |
|------|------|
| 测试套件 | 65 个失败 → **0 个失败**（1473 通过 / 34 跳过，连续三次结果一致） |
| 崩溃 | 修复了因遗留线程导致的偶发进程中止；整套运行不再中断 |
| 架构检查 | 两处层级越界已修复，`tools/check_architecture.py` 通过 |

---

## 已知限制

- **离线地图扩展需要与所用 Qt 版本匹配**。扩展自带的原生组件若与当前 PySide6 的 Qt 版本不一致，会加载失败并自动降级为在线地图；此时日志中会出现 `WinError 127`。
- **CI 尚未配置**。仓库的 `.github/` 目前只有 issue 模板，架构检查与测试需要本地运行（见 `AGENT.md` 第 9 节）。
- `tests/gui` 目录不在默认测试范围内，其中部分用例仍停留在旧的同步加载模型，尚未维护。

---

## 许可证

MIT License
