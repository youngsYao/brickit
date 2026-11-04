# 厦大 · 颂恩楼内置示例

入口位于创作首页右侧的示例区，可切换「颂恩楼」与「一颗苹果」。打开示例不需要生成服务、API 密钥或联网资源；所有模型数据、照片和预览均随 `dist/` 发布。

## 模型与素材

- `dist/examples/songen.js`：确定性的 2,979 颗可独立编辑积木，24 凸点宽的塔身、14 排主体窗格、拱形幕墙、红砖门厅、大拱窗、双侧角楼、红瓦坡屋顶、飞檐、屋脊与天线；附带湖岸、低翼楼、绿植、长椅和两只黑天鹅。
- `dist/assets/songen-reference.jpg`：用户提供的正面照片，用于工作台「原图对照」。
- `dist/assets/songen-study.png`：内置 ImageGen 工具根据原照片生成的正面、前侧面与后侧面建筑研究图。生成图仅用于辅助推定，未直接当作三维模型或预览。
- `dist/assets/songen-preview.png`：由实际积木模型渲染的首页预览。
- `output/songen/`：桌面与手机界面、模型和展开视图的检查截图。

正面以用户照片为依据；照片未覆盖的侧面与背面采用一致的窗格和石材语言推定，并非测绘复原。模型使用系统既有的数字示例砖及微缩装饰尺寸，不标记为官方 LDraw 零件。模型是独立积木数据，可播放、编辑、导出图片和零件清单；示例与修改稿保留各自的原始模型，颂恩楼不会还原为苹果。

## 生成参考图的完整提示词

工具：内置 `image_gen.imagegen`；参考输入：用户提供的颂恩楼照片；不透明白色背景。

```text
Use case: stylized-concept. Asset type: architectural reconstruction reference only, not app UI. Input image 1 is the primary authoritative front elevation reference of Song'en Building (颂恩楼), Xiamen University. Create an architectural study sheet of this SAME central tower, 3 aligned full-building views: frontal elevation, front-right three-quarter, rear-left three-quarter. White background, neutral daylight, realistic architectural miniature, no scenery, no water, no neighboring buildings. Preserve the supplied frontal proportions closely: very tall narrow cream/light stone rectangular tower, turquoise grid-glass front facade inset with large shallow arch at its top, slim central turquoise vertical stripe, narrow vertical slit-window strips on both stone outer piers, rust red brick podium and large arched entrance, stepped upper crown with turquoise grand arched window and two small symmetric roofed turrets, sweeping terracotta Chinese hipped roof with dark upturned eaves, small dark ridge pavilion and thin antenna. Tower is roughly 3 times taller than wide; broad main tower 50% central curtain glass and substantial stone strips at edges; crown occupies top quarter. Side/rear views must be understood as plausible inferred reconstructions, keep coherent depth and window spacing; do not invent extra towers or fanciful pagodas. No text. Intended to guide detailed brick model reconstruction.
```

## 验证

`npm test` 覆盖两个内置示例的独立数据、稳定 ID、零件清单、还原来源和播放排序；`npm run check` 检查全部前端模块。

静态服务下已检查：首页切换两个示例、照片对照、从零播放与单步推进、跳到完整模型、选择和删除积木、还原建筑、保存及刷新后恢复、苹果回归；桌面和 390px 手机视口均无横向溢出或浏览器脚本错误。
