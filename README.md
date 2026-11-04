# instant - Leica M9 "Leica Moment" offline renderer

小米 17 Ultra「徕卡一瞬 · M9」风格的离线渲染器。输入 RAW/DNG，输出 M9 / M3 成片。
**全程本地运算，不联网、不上传。**

## 核心：不再是"猜风格"，而是用 ROM 里的真数据

早期版本靠评测里的形容词手工调参（暖调、浓郁、暗部蓝……），反复漂移成"琥珀滤镜"。
现在色彩直接来自 **Xiaomi 17 Ultra ROM 解出的 LeicaFilter 参数**（由
[OffLineM9](https://github.com/benbaobaoshigemi/OffLineM9) 逆向后公开）：

```
instant/assets/leica_filter_param_m9_snapshot.bin   1.6MB  17³ LUT ×114 + CvStyle shading ×55
instant/assets/leica_filter_param_m9_preview.bin    1.6MB  同上（StyleTrans 关闭时的退化参数）
```

实测真实 LUT 的行为（这解释了"浓而不艳"）：

| 输入 | 输出 | 含义 |
|---|---|---|
| 中性灰 0.50 | (0.518, 0.518, 0.518) | **零色偏**，仅提亮 |
| 肤色 (0.75,0.60,0.50) | (0.811, 0.621, 0.518) | 提亮 + 微暖 |
| 天空 (0.45,0.60,0.80) | (0.432, 0.616, 0.861) | 蓝 +0.06（更浓） |
| 红色 (0.80,0.15,0.12) | (0.882, 0.132, 0.033) | R +0.082 / B −0.087（正红化） |

**结论：M9 的"德味"是选择性色彩增强，不是全局色温偏移。** 中性色完全不动，只有彩色区域被加强——
这就是为什么手工加暖总会变成滤镜，而 LUT 不会。

## 管线（对齐 OffLineM9 逆向结论）

```
RAW 解码(AsShotNeutral) → 双锚点曝光归一 → 线性域高光中性化
  → filmic(M9: contrast 1.42 / shoulder 2.10 / S 1.58 / 黑位 0.022)
  → 饱和补偿 1.35（弥补缺失的 StyleTrans 网络）
  → 轻微高光收色 + 孤立红点抑制（缺陷修复，非风格）
  → 色度降噪 0.6
  → 【真实 LeicaFilter】17³ LUT（lux×CCT 插值，trilinear）+ CvStyle 暗角（仅亮度）
```

关键参数来源：

| 参数 | 取值 | 依据 |
|---|---|---|
| lux_index | `474.9 − 23.45 × EV100` | OffLineM9 `frontend.py::lux_index_from_ev` |
| CCT | 从 AsShotNeutral 反算（`scene_cct()`） | ROM trigger 名为 "aiscene-lux-cct"，CCT 选 LUT 档位；可用 `--cct` 覆盖 |
| LUT 应用域 | 显示域 sRGB | LeicaFilter 在 StyleTrans 之后、YUV 输出之前 |
| CvStyle 暗角 | 8 参数，仅乘亮度、保留极值 | `leicafilter.py::cvstyle_apply` |

## 真实 M9 的 B2Y 层是"做减法"（已查证，未完整实现）

| 环节 | 真实 M9 | 本项目 |
|---|---|---|
| cc15 色彩矩阵 | 对角元 1.07–1.51（普通 1.61–1.86）→ 降饱和 | 未实现（缺少相机矩阵绑定） |
| cv122 色度增益 | 0.49/0.47（普通 0.53/0.50） | 未实现 |
| tdl13 色相/饱和 LUT | 高饱和 −10%、色相旋转 ±2–5° | 未实现 |
| gamma152 | 仅强光档压反差（暗部 +26/1023） | 用 filmic 近似 |
| LTM / TMC | **LTM 0%、GTM 100%**，无局部处理 | 已遵循（无局部算子） |
| StyleTrans | low/high 风格网络 + colorfix 全分辨率 | **未实现（需 QNN 模型与 QAIRT）** |

缺失 StyleTrans 是本项目与官方效果的主要差距；LUT 已是真数据，网络部分用"饱和补偿"近似。

## 安装与用法

```bash
git clone https://github.com/NopantsNobra/leica-instant-pc.git
cd leica-instant-pc
pip install -r requirements.txt
```

`instant/assets/` 下的两个 `.bin` 是运行必需的参数表，已随仓库提供（来源与版权见文末）。

> 仓库**不含样张**：`samples/` 与所有 RAW 文件都在 `.gitignore` 里（既是体积考虑，
> 也避免把私人照片推到公开仓库）。请自备 DNG/ARW/CR2 测试。

```bash
python render.py input.dng -o out.jpg              # M9 彩色（默认）
python render.py input.dng -o out.jpg --mode m3    # M3 黑白
python render.py raws/ -o outs/                    # 批处理
python render.py in.dng -o out.jpg --cct 3200        # 覆盖场景色温（换 LUT 档位）
python render.py in.dng -o out.jpg --strength 1.4     # 更浓（饱和补偿倍率）
python render.py in.dng -o out.jpg --wb camera        # 更冷（默认 AsShotNeutral）
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--mode` | m9 | `m9` 彩色 / `m3` 黑白 |
| `--ev` | 0 | 曝光（档） |
| `--strength` | 1.0 | 饱和补偿强度 0..2 |
| `--cct` | 自动 | 场景色温 Kelvin（自动从 DNG 反算）；决定 LUT 档位 |
| `--scene` | common | **实测对画面无影响**，114 个 LUT 中仅 8 个内容不同，同一 lux/cct 下各场景落到同一份 LUT。保留仅为兼容 |
| `--wb` | camera | `camera` 中性 / `warm` 略暖 / `daylight` 强暖 |
| `--denoise` | 0.6 | 亮度+色度降噪：色度全图、亮度仅暗部且边缘保护（过高会吞色度） |
| `--vignette` | 0 | 额外暗角（LUT 已自带 CvStyle） |
| `--tiff` | 关 | 16bit TIFF |

## 三个已实测证伪的假设（避免重复踩坑）

0. **不要在 `save()` 里再做一次 gamma 编码**。`filmic()` 输出的已经是显示域 sRGB，
   早期版本又调了 `linear_to_srgb()` 一次，等于**双重 gamma**：落盘 JPEG 的皮肤
   L*=38，管线内部其实只有 L*=14。这掩盖了曲线把主体压暗 3 档的事实，也让此前
   所有"亮度刚好"的判断失真。修正后重新校准了曲线（+1.5 EV 基准、shoulder 3.60、
   mid_gray 0.30、s_curve 1.15），p50/p95 与相机直出对齐（0.348/0.853 vs 0.369/0.815）。


1. **`--scene` 不改变画面**：ROM 的 114 个 LUT 索引中只有 **8 份内容互不相同**，
   且 6 个场景（common/protrait/night/plants/food/sunrise_sunset）在同一 `(lux, cct)`
   上落到的 LUT 内容完全一致——场景表只是不同的 lux/cct 阈值边界。想换风格要动
   `lux_index`（由 EXIF 决定的亮度档）或 `cct`（色温档），不是 scene。
2. **CCT 不能写死 5000**：MI 8 样张真实场景色温 4322 K，落到 group 3 的另一份 LUT，
   与 5000 K（group 0）的输出最大差 8.6%。CCT 从 DNG 的 AsShotNeutral 反算，
   精度只够选档位（档位边界 2800/3600/4500），故提供 `--cct` 手动覆盖。

另注：ROM 里 portrait 的拼写是 **`protrait`**，而 `LeicaFilterParams.lut()` 对未知名
静默回退 common——所以传 `portrait` 会「看起来成功、实际是通用档」。入口已做别名归一化。

## 依赖与第三方组件

### Python 运行时依赖

| 包 | 版本 | 许可 | 用途 |
|---|---|---|---|
| [rawpy](https://github.com/letmaik/rawpy) | >=0.28 | MIT | DNG/ARW/CR2 解码，封装 [LibRaw](https://www.libraw.org/)（LGPL-2.1 / CDDL-1.0 双许可） |
| [numpy](https://numpy.org/) | >=2.0 | BSD-3-Clause | 全部数组运算 |
| [Pillow](https://python-pillow.org/) | >=10.0 | MIT-CMU | JPG/PNG 编解码、高斯模糊、YCbCr 转换、Sobel 边缘 |
| [tifffile](https://github.com/cgohlke/tifffile) | >=2024.0 | BSD-3-Clause | 16-bit RGB TIFF 输出（Pillow 不支持该格式） |

无需任何深度学习框架——本工具不运行 StyleTrans 神经网络，见「未实现的部分」。

### 逆向工程与上游项目

| 项目 | 作者 | 许可 | 本项目使用方式 |
|---|---|---|---|
| [OffLineM9](https://github.com/benbaobaoshigemi/OffLineM9) | benbaobaoshigemi | MIT（其自身代码） | ① `instant/m9filter.py` 为 `m9/leicafilter.py` 的**逐字副本**（解析专有二进制布局，刻意不改动）；② 管线结构、lux-index 公式、CCT 触发逻辑来自其 `M9-style-analysis.md` 与 `frontend.py`；③ `instant/assets/*.bin` 的参数表由该项目从 ROM 提取 |
| Xiaomi 17 Ultra ROM | Xiaomi | 专有 | LeicaFilter 参数：114×17³ LUT、CvStyle 暗角、gamma/cc/cv/tdl 调校表 |

### 方法论与事实交叉验证

| 来源 | 用于 |
|---|---|
| [爱范儿：把小米徕卡影调搬到 iPhone](https://www.ifanr.com/1650532) | 「德味四要素」（克制影调／浓郁色彩／一点点偏色／暗角）与「允许死黑与高光溢出」的影调观 |
| [Imaging Resource — Leica M9 评测](https://www.imaging-resource.com/) | M9 的客观特征：高对比、鲜艳色彩，而非全局暖偏 |
| Leica M9 DNG 的 ColorMatrix / AsShotNeutral 实测 | 确认 M9 自身并非「锁死日光白平衡」，纠正早期误判 |
| raw.pixls.us（CC0 样张库） | 小米 Mi 8 官方 DNG，用于 RAW 链路回归 |
| 相机内嵌 JPEG（`raw.extract_thumb()`） | 本机 14 Ultra 样张的全分辨率直出图，作为亮度/色彩调参的**基准锚** |

## 许可

- **本项目代码**：MIT，见 [LICENSE](LICENSE)。
- **`instant/assets/*.bin`**：从 Xiaomi 17 Ultra ROM 提取的专有参数表，**不在 MIT 许可范围内**，
  版权归 Xiaomi / Leica Camera AG。此处仅作互操作与个人研究之用，不再授予任何再分发权利。
  若权利人提出要求，将移除这些文件。
- **`instant/m9filter.py`**：OffLineM9 的 MIT 代码，版权归其作者。

## 免责声明

个人学习与研究项目，与 Xiaomi、Leica Camera AG 无任何关联，未获其授权或背书。
输出不含品牌水印，不声称等同官方「徕卡一瞬」效果——官方 StyleTrans 神经网络未包含在内。
