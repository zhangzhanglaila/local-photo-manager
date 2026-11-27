# iPhoto 本地照片管理器

一个类似 macOS 照片应用的**文件夹原生**照片管理工具，支持 Windows、macOS 和 Linux。照片和视频留在本地，不需要导入，也不上传云端。

> 基于 [iPhotron](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager) 开发，感谢原作者 Haibin Zhao。

![主界面](docs/picture/mainview.png)

![照片地图](docs/picture/map1.png)

![编辑视图](docs/picture/editview.png)

## 当前状态

- 当前版本：**6.1.0**（见 `pyproject.toml`）。
- 生产代码已完成 vNext 架构收敛：`RuntimeContext -> LibrarySession -> application ports/services`。
- 全量测试（排除原生地图探测用例）保持全绿；精确数量以本地最新一次运行为准。
- GUI 使用 PySide6/Qt 6；图库索引使用 SQLite（WAL）；AI 使用 ONNX Runtime。

## 核心功能

| 功能 | 说明 |
|------|------|
| 文件夹原生图库 | 一个文件夹就是一个相册，无需导入 |
| 多图库 | 同时管理多个图库根目录，侧边栏切换 |
| 照片浏览 | 网格、详情、胶片条、幻灯片、缩放/平移 |
| 实况照片 | 自动配对 HEIC/MOV，支持播放与导出 |
| 照片地图 | GPS 标记、聚合、缩放、离线 OsmAnd 扩展 |
| 照片编辑 | 亮度/对比度/饱和度/曲线/裁剪/旋转/透视校正等 |
| 视频 | 内置播放器，支持旋转、裁剪、导出 |
| 人脸识别 | InsightFace + ONNX Runtime，人物聚类与分组 |
| OCR 文字搜索 | RapidOCR 识别图中文字，加入搜索；设置 → 智能可逐张浏览识别结果 |
| 语义搜索 | CLIP / SigLIP2 向量索引，支持中文关键词 |
| 相册管理 | 相册/子相册、重命名、置顶、封面、排序 |
| 侧边栏过滤 | 按文件夹/相册勾选筛选照片 |
| 元数据 | ExifTool 读取 EXIF/GPS/拍摄时间 |
| 自定义工作区 | 索引、缩略图、人脸数据放到独立目录，保持照片文件夹干净 |

## 技术栈

- **语言：** Python 3.12+
- **GUI：** PySide6 (Qt 6)
- **数据库：** SQLite（WAL 模式，FTS5）
- **图像处理：** Pillow、OpenCV、NumPy、rawpy
- **视频处理：** PyAV (FFmpeg)
- **地图：** OsmAnd 矢量地图 / OpenGL / QRhi 渲染适配
- **AI：** ONNX Runtime、InsightFace、RapidOCR、CLIP/SigLIP2
- **元数据：** ExifTool

## 环境要求

- Python **3.12+**
- **ExifTool**：读取照片 EXIF/GPS
  - Windows：`choco install exiftool` 或 https://exiftool.org
  - macOS：`brew install exiftool`
  - Linux：`sudo apt install libimage-exiftool-perl`
- **FFmpeg**：视频封面、视频导出
  - Windows：`choco install ffmpeg` 或 https://ffmpeg.org
  - macOS：`brew install ffmpeg`
  - Linux：`sudo apt install ffmpeg`

Linux 使用 RAW 解码时还需要 `libraw`：

```bash
sudo apt install libraw-dev       # Debian/Ubuntu
sudo dnf install LibRaw-devel     # Fedora
```

## 安装

### 从源码安装（推荐）

```bash
git clone https://github.com/zhangzhanglaila/local-photo-manager.git
cd local-photo-manager

python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -e .
```

可选 AI 依赖：

```bash
# OCR + ONNX Runtime
pip install -e ".[ai]"

# 语义搜索（CLIP / SigLIP2）
pip install -e ".[agent]"

# 人脸识别
pip install insightface onnxruntime
```

### 从 Release 安装

从 [Releases](https://github.com/zhangzhanglaila/local-photo-manager/releases) 下载完整包，解压后执行：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

完整包通常已包含 CLIP、人脸模型和地图扩展；如果缺少，首次使用时会提示下载。

## AI 模型

| 用途 | 默认位置 | 覆盖变量 |
|------|----------|----------|
| 语义搜索 CLIP/SigLIP2 | `extension/models/` | `IPHOTO_CLIP_MODEL_DIR` |
| 人脸识别 | `src/extension/models/` | `IPHOTO_FACE_MODEL_DIR` |
| OCR（RapidOCR） | `extension/models/rapidocr/` | 首次使用自动下载 |
| 地图扩展 | `src/maps/tiles/extension/` | `IPHOTO_OSMAND_EXTENSION_ROOT` |

首次使用 AI 功能时的下载量约为：

- CLIP/SigLIP2 搜索模型：约 350–580 MB
- 人脸模型：约 300 MB
- OCR 模型：约 21 MB
- 地图扩展：约 975 MB（可选）

也可以直接从 Release 手动下载模型并解压到对应目录。

## 启动

```bash
# Windows 推荐方式
.venv\Scripts\python.exe -m iPhoto.gui.main

# 已激活虚拟环境
iphoto-gui

# macOS / Linux
source .venv/bin/activate
iphoto-gui
```

打开指定相册：

```bash
.venv\Scripts\python.exe -m iPhoto.gui.main D:\APPLE\202406_a
# iphoto-gui /path/to/photos
```

Windows 无控制台启动使用 `run_iphoto.pyw`；排障时使用 `python -m iPhoto.gui.main` 查看控制台日志。

## 项目结构

```text
src/iPhoto/        生产代码（application/domain/infrastructure/gui/bootstrap/...）
src/maps/          可选 OsmAnd 离线地图运行时
tests/             pytest 测试
tools/             架构检查与手工脚本
scripts/           打包、模型下载、发布辅助脚本
packaging/         安装包与图标资源
docs/              当前架构、开发、安全、构建文档
extension/         下载的 CLIP 模型缓存（gitignore）
downloads/         下载的发布包缓存（gitignore）
run_iphoto.pyw     Windows GUI 入口
pyproject.toml     依赖、打包与 pytest 配置
```

更详细的依赖方向、`RuntimeContext`/`LibrarySession` 边界和存储模型见 [docs/architecture.md](docs/architecture.md)。

## 数据位置

默认情况下，图库工作文件位于：

```text
<LibraryRoot>/.iPhoto/
  global_index.db     # 图库索引
  ocr_index.db        # OCR 索引
  manifest.json       # 相册清单
  thumbs/             # 缩略图缓存
  faces/              # 人脸数据
```

配置自定义工作区后改为：

```text
<WorkspaceBase>/<LibraryName>/.iPhoto/
```

照片原文件不会被普通浏览、扫描或编辑覆盖；编辑结果保存在 `.ipo` sidecar 中。

## 开发与测试

```bash
# 架构边界检查
.venv\Scripts\python.exe tools\check_architecture.py

# 架构测试
.venv\Scripts\python.exe -m pytest tests/architecture -q

# 全量测试
.venv\Scripts\python.exe -m pytest tests -q
```

`tests/gui` 与 `tests/ui` 默认不在全量测试范围内；GUI 用例需要显式运行。测试细节和提交约定见 [docs/development.md](docs/development.md) 与 [AGENT.md](AGENT.md)。

## 环境变量

| 变量 | 作用 |
|------|------|
| `IPHOTO_EXIFTOOL_PATH` | 指定 ExifTool 可执行文件 |
| `IPHOTO_FACE_MODEL_DIR` | 指定人脸模型目录 |
| `IPHOTO_CLIP_MODEL_DIR` | 指定 CLIP/SigLIP2 模型目录 |
| `IPHOTO_FACE_THREADS` | 限制人脸/OCR 推理线程数 |
| `IPHOTO_SKIP_FACE_SCAN` | `1` 关闭启动后的自动人脸扫描 |
| `IPHOTO_PREFER_OSMAND_NATIVE_WIDGET` | `0` 禁用 OsmAnd 原生地图组件 |
| `IPHOTO_OSMAND_EXTENSION_ROOT` | 指定地图扩展目录 |
| `IPHOTO_SEARCH_MIN_SIM` | 覆盖语义搜索相似度下限 |
| `IPHOTO_SEARCH_MAX_RESULTS` | 覆盖搜索结果数量上限 |
| `IPHOTO_DIAG` | 输出 `[DIAG]` 诊断日志 |

## 已知限制

- 地图扩展自带的原生组件需要与当前 PySide6/Qt 版本匹配；不匹配时会自动降级为在线地图，并在日志中记录 `WinError 127`。
- CI 尚未配置；架构检查和测试目前需要在本地运行。
- 本机全量测试在原生 OsmAnd 探测用例上可能触发 Qt 退出期崩溃；可用 `--ignore=tests/test_photo_map_view.py` 稳定跑完其余测试。

## 文档

- [架构](docs/architecture.md)
- [开发指南](docs/development.md)
- [安全与数据位置](docs/security.md)
- [自定义工作区](docs/custom_workspace.md)
- [变更记录](docs/CHANGELOG.md)
- [Windows 构建](docs/misc/BUILD_EXE.md)
- [Linux .deb 构建](docs/misc/BUILD_DEB.md)

## 许可证

MIT License
