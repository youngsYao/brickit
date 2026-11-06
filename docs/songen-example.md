# 厦大 · 颂恩楼内置示例

入口位于创作首页右侧的示例区，目前提供「颂恩楼」示例。打开示例不需要生成服务、API 密钥或联网资源；所有模型数据、照片和预览均随 `dist/` 发布。

## 模型与素材

- `dist/examples/songen.js`：确定性的 5,649 颗可独立编辑积木场景，以颂恩楼为中心，包含左右周边楼群、前景芙蓉湖水面、湖岸步道、树木、长椅和两只黑天鹅。颂恩楼保留 24 凸点宽的塔身、14 排主体窗格、拱形幕墙、红砖门厅、大拱窗、双侧角楼、红瓦坡屋顶、飞檐、屋脊与天线。其中周边楼群占 2,170 颗、湖水占 190 颗。界面总积木数由场景数据动态统计。
- `dist/assets/songen-reference.jpg`：用户提供的正面照片，用于工作台「原图对照」。
- `dist/assets/songen-study.png`：内置 ImageGen 工具根据原照片生成的正面、前侧面与后侧面建筑研究图。生成图仅用于辅助推定，未直接当作三维模型或预览。
- `dist/assets/songen-preview.png`：由实际积木模型渲染的首页预览。
- `output/songen/`：桌面与手机界面、模型和展开视图的检查截图。

颂恩楼正面、左右红白楼群及湖岸关系以用户照片为依据；照片未覆盖的侧面与背面、周边建筑的进深和湖面的范围采用一致的窗格、石材和校园景观语言推定，并非测绘复原。模型使用系统既有的数字示例砖及微缩装饰尺寸，不标记为官方 LDraw 零件。模型是独立积木数据，可播放、编辑、导出图片和零件清单；示例与修改稿保留各自的原始模型，还原时保留整套楼群与湖景。

搭建进度按整场景的实际积木数显示。颂恩楼示例及其修改稿的四个快捷阶段为「起步、半程、收尾、全景」，分别跳至约 10%、50%、85% 与 100%；阶段按总体进度命名，完整湖岸或楼体不与固定百分比绑定。

## 生成参考图的完整提示词

工具：内置 `image_gen.imagegen`；参考输入：用户提供的颂恩楼照片；不透明白色背景。

```text
Use case: stylized-concept. Asset type: architectural reconstruction reference only, not app UI. Input image 1 is the primary authoritative front elevation reference of Song'en Building (颂恩楼), Xiamen University. Create an architectural study sheet of this SAME central tower, 3 aligned full-building views: frontal elevation, front-right three-quarter, rear-left three-quarter. White background, neutral daylight, realistic architectural miniature, no scenery, no water, no neighboring buildings. Preserve the supplied frontal proportions closely: very tall narrow cream/light stone rectangular tower, turquoise grid-glass front facade inset with large shallow arch at its top, slim central turquoise vertical stripe, narrow vertical slit-window strips on both stone outer piers, rust red brick podium and large arched entrance, stepped upper crown with turquoise grand arched window and two small symmetric roofed turrets, sweeping terracotta Chinese hipped roof with dark upturned eaves, small dark ridge pavilion and thin antenna. Tower is roughly 3 times taller than wide; broad main tower 50% central curtain glass and substantial stone strips at edges; crown occupies top quarter. Side/rear views must be understood as plausible inferred reconstructions, keep coherent depth and window spacing; do not invent extra towers or fanciful pagodas. No text. Intended to guide detailed brick model reconstruction.
```

## 验证

`npm test` 覆盖颂恩楼示例的独立数据、稳定 ID、零件清单、还原来源和播放排序，以及湖水的前景范围、左右周边楼群、整场景修改稿保存及恢复；`npm run check` 检查全部前端模块。

静态服务下已检查：首页打开颂恩楼示例、照片对照、从零播放与单步推进、跳到全景、选择和删除积木、还原整场景、保存及刷新后恢复；桌面和 390px 手机视口均无横向溢出或浏览器脚本错误。

湖岸草地、岸线、广场和步道采用不同的表面高度，避免交叠顶面旋转时闪白；花坛基座延伸到下沉草地，顶部高度保持一致。几何回归检查覆盖地景表面高差，另以 15 个相机角度对比了修复前后的湖岸交叠区域：原有 10 个取样点均发生颜色切换，修复后均无切换。
