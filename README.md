# brick.it

一个以图片创作为入口的积木模型工作台。已接入官方 BrickWright / BrickLayer：照片 → AI 编写装配程序 → 几何校验 → 渲染与照片对照 → 反馈修正 → 导入三维工作台。生成需要在本地服务端配置模型 API Key 与 LDraw 零件库；未配置时仍可使用苹果示例、编辑和本地作品库，不会把示例伪装成生成结果。

## 已实现

- 图片选择、拖放、预览与移除；手机拍照入口，以及支持设备上的相机拍摄与关闭释放。
- 上传区使用与系统暖白、浅灰绿色调一致的积木信纸底图，以及原生 SVG 积木相机；底图无水印，整个底板都支持拖放图片。
- 115 颗苹果积木逐颗拼装，上一块/下一块、播放暂停、速度、进度定位和阶段跳转。
- 独立的搭建 / 编辑模式；模型旋转、缩放、分层、当前积木高亮。
- 工作台画布右侧工具栏提供 AI 生成图标，可返回图片上传 / 拍照页面，并保留当前模型和生成设置。
- 自由编辑的撤销 / 重做位于画布左上角；选件、颜色、方向、删除、取消放置与模型还原由侧栏提供，画布底部不再保留编辑工具横条。
- 41 种常见示例零件，覆盖标准砖、薄板、光面板、斜坡、圆形件、科技梁、连接件。
- 仓库检索、分类和颜色筛选，直接带入工作台选件。
- 放置预览、网格吸附、基本旋转实体重叠检查、选择、换色、位置调整、旋转、替换、删除和撤销/重做。待放置积木可在侧栏选择朝向，或按 R 旋转 90°，连续放置保留方向。
- 颜色使用立体凸点按钮，选中后显示边框与勾选标记；待放置和已选积木都可用四种积木朝向图选择 0° / 90° / 180° / 270°，并同步实际模型、键盘操作和撤销记录。
- 浏览器本地草稿自动保存、显式版本保存、收藏、作品检索与再次编辑。每份修改稿独立保留原始模型，可在删除、刷新或保存后点击“还原原始模型”；还原本身也可撤销，已保存版本会保留。
- 作品缩略图在桌面横向预览框、手机方形预览框内等比例居中展示完整模型；首页最近作品也使用完整图片适配。
- PNG 模型图片和 CSV 零件清单导出；手机端零件 / 属性底部抽屉。
- 页头将红色积木与立体字标拆为独立透明素材，分别调整大小、间距和手机布局；标签页使用红色六凸点 SVG 标记。
- 搭建进度条使用当前完整模型的透明缩略图作为滑块；支持点击、拖动、触控和键盘调节，修改模型后同步更新。

## 本地运行

需要 [uv](https://docs.astral.sh/uv/)；uv 会安装符合要求的 Python（≥ 3.12）。在本目录运行：

```sh
uv sync --locked
cp .env.example .env
```

在 `.env` 中设置：

- `BRICKWRIGHT_GENERATION_MODEL`：支持图像输入与工具调用的模型，使用 `openai/`、`anthropic/` 或 `gemini/` 前缀。
- 对应服务商的 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 或 `GEMINI_API_KEY`。
- `LDRAW_LIBRARY_PATH`：可留空，默认使用 `~/.cache/brick-it/ldraw`；已有其他安装时可填其 `ldraw` 目录绝对路径。
- `BRICK_IT_AUTO_DOWNLOAD_LDRAW`：默认 `true`，首次启动缺少零件库时自动下载固定版本。离线环境可设为 `false`。

然后启动：

```sh
npm run dev
```

首次启动会在服务就绪前安装缺少的零件库（压缩包约 140 MiB），已有有效安装直接复用。如果网络或校验失败，编辑器仍会启动，并提示手动重试（安装成功后重新启动服务）；未完成的库不会被当作可用安装。

打开 [http://127.0.0.1:4173](http://127.0.0.1:4173)。生成服务和静态页面共用同一地址。点击“生成”后，照片会发送至配置的模型服务商。相机需要浏览器授权，并在 HTTPS 或 localhost 下使用；三维工作台需要 WebGL 2。

只运行原有静态工作台可使用 `npm run dev:static`，此时生成入口会明确显示服务不可用。已有静态托管不会自动获得 Python 后端。

## LDraw 零件仓库

零件库通过独立的 [GitHub Release 附件](https://github.com/youngsYao/brickit/releases/tag/ldraw-library-2026-11-01) 分发，不进入 Git 历史。`ldraw-library.json` 固定附件 URL、SHA-256、压缩与解压大小和文件数；本项目快照日期为 2026-11-01，对应上游 Parts Update 2026-10。原始 ZIP 保持字节不变。

`npm run dev` 会自动准备缺少的库，也可以手动安装或仅检查：

```sh
npm run setup:ldraw
npm run check:ldraw
# 指定新的缓存目录
npm run setup:ldraw -- --destination /path/to/ldraw
# 离线安装：仍然验证同一份 SHA-256 和大小
npm run setup:ldraw -- --destination /path/to/ldraw --archive /path/to/ldraw-complete-2026-11-01.zip
```

脚本也可以在未安装项目依赖时通过 `python3 scripts/download_ldraw.py` 运行（Python ≥ 3.9）。默认读取非空环境变量 `LDRAW_LIBRARY_PATH`，其次读取项目 `.env` 中的同名路径，再使用 `~/.cache/brick-it/ldraw`。使用 `--destination` 时请同步设置 `.env` 的 `LDRAW_LIBRARY_PATH`，以便服务使用该目录。

下载按流处理，完整验证后才解压；解压在同级临时目录完成，通过必需文件、许可文件、路径安全、文件数和大小检查后再原子安装。失败会清理临时数据，保留现有库。并发安装通过目录锁协调；`--check` 不下载、不写入。已有手动安装的有效库原样复用，不会补写标记；脚本管理的库保留 `.brickit-library.json`，版本或校验值不匹配时要求改用新目录，不会自动覆盖旧库。管理标记与结构检查不等于每次启动重新校验全部文件。

首次安装的目录会保留原 ZIP 的许可与归因，并补充附带 mklist 工具所需的 GPL 全文；详见 [LDraw 归因说明](docs/LDRAW-NOTICE.md)。默认缓存目录在本机包含 24,985 个顶层零件文件，其中 21,676 种具有 SDK 元数据和工作台网格，可在仓库浏览。

打开“零件仓库”即可查看真实模型缩略图，无需 AI API Key。仓库使用 Three.js r180 的官方 `LDrawLoader`，直接读取本地 `.dat` 文件及子件、基础图元和 `LDConfig.ldr`。每页 24 件，按可见范围加载；所有缩略图共享一个离屏渲染器，并缓存模型和图片。

支持编号、英文名称与常见中文类别搜索、分类筛选、分页和预览颜色切换。点击“在工作台使用”会读取该零件的 SDK 网格，放置后随作品保存；仓库完整 LDraw 外观与工作台用于编辑的内缩碰撞网格有少许区别。预览颜色不代表该零件颜色组合实际在售。

其他机器使用相同的安装命令即可获取同一快照，无需 GitHub 登录或 AI API Key。脚本不使用会变化的 `latest` 链接，也不会在下载失败后静默切换至其他版本。零件文件仅通过受限的只读接口提供，不能读取库目录之外的文件。

## BrickWright 生成流程

直接依赖 [官方仓库](https://github.com/AvaLovelace1/brickwright)，固定提交 `f8a90d1a416582e945505398311ffa397aa84959`，依赖版本记录于 `uv.lock`。不复制论文指标，也不使用预制模型代替 AI 结果。

1. 浏览器缩小并上传图片，可附加提示词、模型大小和风格。
2. 本地服务保存任务，启动可终止的独立生成进程，使用官方 `run_agent()`。
3. AI 使用真实目录零件编写 `build.py`，根据 `validate()` 返回的错误修正结构。
4. 开启两轮视觉评审：多视角渲染、匹配参考观察角度、对照照片并反馈修改。
5. `finalize()` 后重新读取实际导出的 LDraw 文件，独立强制检查：非空、件数上限、无碰撞、无悬空、单一连接组件。未完成、预算耗尽、费用未知或校验失败均不导入。
6. 真实零件网格和完整 XYZ 姿态转换成工作台数据，保留原图、原始模型、来源信息及检查报告。每种零件网格只保存一份。

小 / 中 / 大模型最多分别为 80 / 250 / 600 件。默认每任务预算 1 美元、最长 15 分钟，可通过 `.env` 调整。费用在模型响应后统计，最后一次调用可能略超预算；取消无法撤销已经产生的模型费用。

生成窗口支持后台继续、任务取消、短暂断线重连和刷新后恢复查看。服务重启会将中断任务标为失败，避免留下假进度。通过校验的任务提供 LDraw、原始装配程序、预览和报告下载；浏览器作品仍保存在本地，原始任务文件由服务端记录目录保留。

### API

| 请求 | 用途 |
| --- | --- |
| `GET /api/generation/capabilities` | 模型、密钥及渲染依赖是否就绪，不返回密钥 |
| `POST /api/generations` | 提交 `{image, prompt, size, style}`，返回任务 ID |
| `GET /api/generations/{id}` | 轮询阶段、消息与完成后的 Project |
| `DELETE /api/generations/{id}` | 取消任务并终止生成进程组 |
| `GET /api/generations/{id}/artifacts/{name}` | 下载已完成任务的指定产物 |
| `GET /api/parts` | 查询真实零件目录，支持 q / category / offset / limit |
| `GET /api/parts/{id}` | 获取可放置、可保存的真实零件网格 |
| `GET /api/ldraw/{path}` | 读取库内零件、基础图元及颜色文件 |
| `GET /health` / `GET /ready` | 服务健康与生成能力状态 |

### 验证与使用范围

- BrickLayer 验证的是几何碰撞、悬空和连接，不是承重、摩擦、倾覆或完整装配顺序。逐块播放仍为层序示意。
- 三维预览使用官方 SDK 随附的内缩碰撞网格，细节与完整 LDraw 外观略有差异；下载的 `.ldr` 保留原始零件引用。
- 编辑后的本地重叠检查仍是包围盒近似，不等于重新执行 BrickLayer。修改后会标记“尚未重新校验”，并禁用原始模型 / 报告下载；还原原始模型后可再次下载。
- 零件清单保留官方编号，但零件颜色组合、库存、价格与采购渠道未验证。
- 这是仅限本机来源的开发服务，官方 agent 会执行其生成的 Python。公开部署前应将生成任务放入独立容器或等效隔离环境，并增加账号、任务归属及费用控制；不要把此本地入口直接暴露到公网。
- `.env`、源照片和任务记录已加入忽略规则。删除 `.brick-it-records/` 会删除服务端任务与下载产物，不会删除浏览器中已经保存的作品。

## 检查

```sh
npm run check
npm run check:ldraw
npm test
npm run test:backend
npm run test:ui
# 先启动 npm run dev，再针对真实 LDraw 库验证
npm run test:catalog
```

浏览器测试需要 Python Playwright 与 Chromium；该测试自行启动临时静态服务，并通过明确的测试响应覆盖上传、进度、导入、保存、取消和失败，不会调用付费模型。后端测试使用真实 BrickLayer 零件与几何验证，并以测试进程验证任务管理。

## 数据与模型

`dist/data.js` 提供示例零件目录、作品数据和存储适配层。作品保存在当前浏览器的 IndexedDB 中；不可用时尝试 localStorage，不进行账号或云端同步。清理浏览器站点数据会删除本地作品。

原有 41 种编辑零件使用内部示例编号和简化几何；AI 生成模型使用真实 LDraw 编号、网格和姿态，并将其零件加入当前浏览器目录。两类零件在清单中保留各自身份。颜色仅表示视觉选择，不代表该零件颜色组合确实在售。搭建播放按从下到上的确定性顺序逐颗展示。

`Project.parts` 保存生成模型引用的网格目录，`Project.generation` 保存任务来源和原始检查信息；这些字段与原图、完整 Piece 姿态、原始 baseline 一起持久化。生成服务没有账号同步功能。

## 主要文件

- `dist/index.html` / `dist/style.css`：响应式页面与工作台布局。
- `dist/app.js` / `dist/generation.js`：页面状态、生成任务、编辑、作品和导出流程。
- `server/`：FastAPI 接口、任务进程、官方 SDK 适配、验收及坐标转换。
- `scripts/download_ldraw.py` / `ldraw-library.json`：零件库自动安装入口与固定快照清单。
- `dist/model.js`：Three.js 模型、拾取、放置、逐颗动画和图片渲染。
- `dist/data.js`：示例目录、模型数据、重叠检查、本地作品存储。
- `dist/ui-art.js`：积木相机、可着色凸点与四种方向的原生 SVG 图形。

## 品牌与进度图

`dist/assets/brand-brick.png` 与 `brand-lettering.png` 使用内置 ImageGen 从提供的品牌稿中分别提取，为独立透明素材；原始组合稿保留在 `brand-wordmark.png`。页头使用两个图片元素组合，桌面间距为 16px，平板为 14px，手机为 12px，窄屏为 10px，尺寸也随屏幕调整。`brand-mark.svg` 与 `favicon.svg` 保留供进度图占位和标签页使用。素材处理提示词见 `outputs/brand-extraction-prompts.txt`。

进度条保留原生 range 输入，通过独立的完整模型透明 PNG 覆盖默认圆点。`model.js` 的 `progressThumbnail()` 输出 160 × 160 缩略图，排除底板、网格和选择状态，生成后恢复原场景；`app.js` 仅在打开或修改模型时刷新并缓存，播放与拖动期间不重复渲染小图。

开源前端依赖的许可见 `dist/assets/licenses/`。BrickWright 代码使用 MIT；BrickLayer 的 LDraw 数据涉及 CC BY 2.0 / 4.0，BrickNet 数据为 MIT，须保留官方 [NOTICE](https://github.com/AvaLovelace1/brickwright/blob/main/packages/bricklayer/NOTICE) 与相应归因。本项目为独立创意数字工具，并非官方 LEGO 产品。

## 本地 UI 素材

`dist/assets/upload-stationery.png` 使用内置 ImageGen 根据提供的信纸参考图重新生成，移除右下角水印，调整为接近页面 `#f7f6f2`、面板 `#fcfbf7` 的暖白底色和浅灰绿边线，外围保留透明。它作为上传框底层背景，真实的文字、按钮、预览与拖放区域继续由 HTML/CSS 实现。生成提示词见 `outputs/ui-stationery-prompt.txt`。

相机模型与颜色、方向控件使用原生 SVG 几何绘制。方向图按同一块示例积木的真实 Y 轴朝向投影，凸点按目录颜色着色，选中状态、颜色名称和角度文字均由当前编辑状态更新。

搜索、作品文件夹与新建创作使用同一套原生 SVG 积木图标：`dist/assets/brick-search.svg`、`brick-folder.svg` 与 `brick-brush.svg`。图形包含凸点、拼缝和浅灰绿立体侧面，并在搜索框、页头、作品导航及动态空状态中复用。

工作台、零件仓库、保存版本与收藏分别使用 `brick-wrench.svg`、`brick-box.svg`、`brick-notebook.svg` 和 `brick-heart.svg`。收藏后切换为砖红色实心的 `brick-heart-filled.svg`，同步用于工作台收藏按钮、作品卡片和收藏标记。

其余 29 类操作图标已统一为同风格的原生 SVG，上传、相机、AI 创作、搭建编辑、模型视图、播放、历史操作、内容导出和状态提示均使用 `dist/icons.js` 管理。静态页面与动态内容共用同一套素材；播放／暂停在原按钮内切换图片，保留文本、焦点和按下状态。

旋转积木、旋转视角、撤销、重做、重新搭建与还原模型分别使用独立造型；逐块搭建与分层拆解也使用不同图标。本次新增 33 个 SVG 文件，包含这些用途变体。所有图标沿用浅灰绿、暖白与砖红色点缀，成功、信息和警告提示分别使用对应的状态配色。
