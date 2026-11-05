# 自定义工作目录功能

## 功能说明

此功能允许用户将 iPhotron 的所有工作文件（索引、缓存、人脸数据等）存储在自定义位置，而不是照片文件夹内的 `.iPhoto` 目录。

### 目录结构

使用自定义工作目录后，文件结构如下：

```
<自定义工作目录>/
└── <照片库名称>/
    └── .iPhoto/
        ├── global_index.db          # 索引数据库
        ├── manifest.json            # 专辑清单（标题、id、筛选条件）
        ├── manifest.bak/            # 清单写入前的备份
        ├── links.json               # Live Photo 配对数据
        ├── ocr_index.db             # OCR 索引
        ├── locks/                   # 跨进程锁
        ├── thumbs/                  # 缩略图缓存（JPEG）
        ├── cache/
        │   └── shaders/             # Qt 着色器缓存
        └── faces/
            ├── face_index.db       # 人脸索引
            ├── face_state.db       # 人脸状态
            └── thumbnails/         # 人脸缩略图
```

照片文件夹保持干净，只包含照片和视频：

```
<照片文件夹>/
├── 2024/
│   ├── 01/
│   │   ├── IMG_001.jpg
│   │   └── IMG_002.jpg
│   └── 02/
└── 2023/
    └── ...
```

### 照片文件夹里不会再出现什么

配置工作目录后，下面这些内容全部落在工作目录，**不会**出现在照片文件夹里：

| 内容 | 位置 |
| --- | --- |
| 专辑清单 | `<工作目录>/.iPhoto/manifest.json` |
| 缩略图 | `<工作目录>/.iPhoto/thumbs/` |
| 着色器缓存 | `<工作目录>/.iPhoto/cache/shaders/` |
| 索引、OCR、人脸、Live Photo 配对 | `<工作目录>/.iPhoto/` |

两个曾经写进照片文件夹的位置已修正：清单过去硬编码为
`<照片库>/.iphoto.album.json`，着色器缓存则因为启动早于工作目录设置而回退到
`<照片库>/.iPhoto`。

### 旧清单的迁移

历史版本在照片文件夹里留下过 `.iphoto.album.json`。配置工作目录后首次打开该库
时，它会**一次性迁移**到 `<工作目录>/.iPhoto/manifest.json`：专辑 id、标题与
筛选条件都保留，照片文件夹随之变干净，无需手工搬运。

未配置工作目录（自包含专辑）时，清单仍写在专辑文件夹内的
`.iphoto.album.json`，这样整个专辑目录可以直接拷走。

## 配置方法

### 方法1：编辑设置文件

找到设置文件位置：
- **Windows**: `%APPDATA%\iPhoto\settings.json`
- **macOS**: `~/Library/Application Support/iPhoto/settings.json`
- **Linux**: `~/.config/iPhoto/settings.json`

添加或修改以下配置：

```json
{
  "workspace_base": "C:/iPhotronWorkspace"
}
```

### 方法2：使用迁移工具

```bash
# 查看帮助
python -m iPhoto.tools.migrate_workspace

# 执行迁移（示例）
python -m iPhoto.tools.migrate_workspace "D:/Photos" "C:/iPhotronWorkspace"

# 预览模式（不实际执行）
python -m iPhoto.tools.migrate_workspace "D:/Photos" "C:/iPhotronWorkspace" --dry-run
```

## 功能特性

- ✅ 照片文件夹完全干净，不生成任何 `.iPhoto` 目录或清单文件
- ✅ 所有性能优化功能保留（索引、缓存、人脸数据）
- ✅ 支持多个照片库，每个库有独立工作空间
- ✅ 自动处理特殊字符（使用哈希值作为目录名）
- ✅ 提供数据迁移工具
- ✅ 兼容现有 `.iPhoto` 目录（自动检测）
- ✅ 旧清单自动迁移到工作目录，无需手工搬运

## 性能影响

使用自定义工作目录**不会影响性能**，因为：
- SQLite 数据库操作相同
- 缩略图缓存机制相同
- 文件 I/O 模式相同
- 仅改变文件位置，不改变访问方式

## 验证方式

想确认照片文件夹确实干净，可以只看"照片以外的东西"：

```powershell
# Windows：列出照片文件夹里的非媒体文件与隐藏目录
Get-ChildItem "D:\APPLE" -Force -File | Where-Object { $_.Extension -notmatch '^\.(jpg|jpeg|png|heic|mov|mp4|aae)$' }
Get-ChildItem "D:\APPLE" -Force -Directory | Where-Object { $_.Name -like '.*' }
```

两条命令都应无输出；媒体文件数量与哈希值不应因打开应用而变化。

## 回退方法

如需回退到传统模式（照片文件夹内的 `.iPhoto`）：

1. 关闭 iPhotron
2. 将自定义工作目录中的 `.iPhoto` 文件夹移回照片文件夹
3. 从设置文件中删除 `"workspace_base"` 配置
4. 重启 iPhotron
