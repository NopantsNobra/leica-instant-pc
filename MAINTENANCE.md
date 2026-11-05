# 维护说明

面向后续接手本仓库的维护者（包括未来的自己）。这里记录架构约定、已知边界、以及「不要重犯」的坑。

## 目录结构

```
render.py             CLI 入口 + 管线编排
instant/
  develop.py         RAW 解码、白平衡、EXIF/色温读取
  tonemap.py         filmic 影调（M9/M3 两套）+ 曝光归一 + 高光中性化
  color.py           M9 色彩算子（饱和补偿 / 高光收色 / 红点抑制）
  leica_lut.py       真实 LeicaFilter 封装（LUT 选择 + CvStyle 暗角）
  m9filter.py        ★ 第三方代码（OffLineM9 逐字副本），勿改动
  grain.py           M3 银盐颗粒
  export.py          降噪 + 输出（JPG/PNG/TIFF）
  assets/*.bin       ★ ROM 专有参数表，勿改动，勿再分发
webapp/
  server.py          本地渲染服务（只绑 127.0.0.1）
  index.html         拖拽式前端（单文件）
tools/               诊断脚本（开发期工具，非运行时依赖）
```

## 数据域约定（最重要的约定）

**`filmic()` 的输出是显示域 sRGB [0,1]。** `save()` **不再做 gamma 编码**。

历史上 `save()` 对显示域数据又调了一次 `linear_to_srgb()`，等于 gamma 编码两次：
落盘 JPEG 的肤色 L*=38，而管线内部只有 L*=14。这掩盖了曲线把主体压暗 3 档的事实，
也让所有「亮度刚好」的验证失真。**任何改动后，用「相机内嵌 JPEG」做锚重新核对
p50/p95/肤色 L\*，不要只看自己渲染的图。**

对照基准（14 Ultra 黄昏人像样张）：

| 指标 | 相机直出 | 本工具目标 |
|---|---|---|
| 整图 p50 | 0.369 | ≈0.35 |
| 整图 p95 | 0.815 | ≈0.85 |
| 死白 ≥0.95 | 0.16% | <1% |
| 肤色 L\* | 30.2 | ≈34 |
| 肤色 b\* | +4.35 | +5 左右 |

## 参数语义

- `--strength`：**只**驱动饱和度补偿（`1 + 0.08×strength`）。高光收色、红点抑制是
  「缺陷修复」不是「风格」，强度固定，不随 strength 变——否则「风格拉满」会重新
  引入红 catchlight。
- `--cct`：决定选哪一档 LUT（trigger 名 `aiscene-lux-cct`）。默认从 DNG 的
  AsShotNeutral 反算，不是固定 5000。
- `--scene`：**对画面无影响**（见下），保留仅为兼容 CLI。
- `--wb`：`warm`（默认，25% 日光混合）/ `camera` / `daylight` / `auto`。

## 弱光/夜景的五个坑（2026-11 用实拍反复定位）

夜景是这套管线最容易翻车的地方，多个独立缺陷会在同一张图上叠加。

0. **S 曲线的支点必须连同曝光一起投影**。`filmic` 的曲线作用于 `x * 2**exposure`，
   但早期 `pivot_linear` 投影时漏乘了 `2**exposure`，支点被算到远低于实际位置。
   后果是整条 S 曲线几乎都落在支点之上，**加对比反而变亮**（实测 s_curve 1.15→1.70，
   暗部从 L*32 提到 L*44）。修好后 s_curve 才真正开始压缩暗部。
   任何调对比度的实验，先确认 pivot 是否正确。

1. **过饱和 = LUT 前多加了饱和度**。LUT 对中性灰完全无影响（实测 B/G=1.000），
   但对有色区域自带 +20~25% 增益。我原先又叠了 `1.08x`，两者相乘导致低中调
   色度高出相机 31%，视觉上是"整张橙红"。修正：`apply_m9` 的 `sat` 基准降到
   **0.85**（`0.85 + 0.15*(strength-1)`）。实测两张反转色偏的样张同时归位：
   琥珀场景 R/G 1.887→1.603（相机 1.619）、青偏场景 R/G 1.249→1.163（相机 1.182）。

2. **暗部提亮过度 = 中调锚在暗场景里权重过高**。`target_mid/p50` 有个反直觉副作用：
   **场景越暗 gain 越大**。一张暗调人像里本该纯黑的丝袜被抬到中灰（实测 L*14.7 → 38），
   提亮同时放大噪点，看起来就是「泛绿 + 噪点」。收敛办法：`high_weight` 0.65 → **0.90**，
   让高光锚主导、中调只做微调。代价是整体略暗于相机，用 `--ev` 补。
   注意这与「死黑」是一对矛盾需求，同一组全局参数无法同时满足——暗调场景优先保黑位。

3. **死黑 = 高光锚定把中调当成了代价**。`min(gain_geom, gain_high)` 里高光是硬限，
   夜景亮招牌把 p99.5 顶得很高，gain 被压到极小，街道（真正的主体）全黑。
   修正：改成**加权几何** `gh^0.65 * gm^0.35`，让中调锚有话语权，多出来的高光
   由 `filmic` 的 shoulder（3.60）吸收。

4. **噪点清的其实是错的通道，阈值还是死的**。两处叠加：
   - luma 降噪的亮度窗口 `Y<165` 几乎把皮肤排除在外（噪点落在 Y 170-230）；
   - `edge = 1 - g/28` 的固定阈值**低于噪声底**（实测模糊后梯度 p50 就有 26），
     门控把所有像素都判成"边缘"，`strength` 拉到 2.5 也只降 5% 噪声。
   修正：亮度窗口放宽到 `(255-y)/140`（只作偏置，不当门），阈值改自适应
   `max(28, p85(g))`——保护最强 15% 的真边缘。现在 strength 1.2 降噪 11.6%、
   细节保留 90%。

5. **单帧追不平相机的多帧降噪**。同一场景：本工具 noise 0.029，相机直出 0.011
   （约 2.6x）。相机用 MFNR 合成多帧，单帧 RAW 管线做不到。别试图用更强的
   高斯去凑——只会糊掉细节。让「亮度接近相机」比「噪点接近相机」优先。

**验证方法**：每张 DNG 都用 `raw.extract_thumb()` 取相机内嵌 JPEG 做锚，
比较 `p50 / p95 / R/G / B/G / chroma`，不要靠肉眼调参。

## 三个已实测证伪的假设（改代码前先读）

1. **`--scene` 不改变画面**。ROM 的 114 个 LUT 索引只有 8 份内容互不相同；6 个场景
   在同一 `(lux, cct)` 上落到同一份 LUT。想换风格要动 lux 或 cct，不是 scene。
2. **CCT 不能写死 5000**。真机样张场景色温 4322K，落到另一份 LUT，与 5000K 输出
   最大差 8.6%。`scene_cct()` 用 sRGB 白点近似（DNG 的 ColorMatrix 求逆在两个参考
   样张上把白点算到 Planckian locus 之外，McCamy 发散，故弃用）。
3. **ROM 里 portrait 拼作 `protrait`**，而 `LeicaFilterParams.lut()` 对未知名静默
   fallback common。入口已做 `normalize_scene()` 别名归一化。

另有一个**不要重犯**：`_xy_to_cct()` 的分母 `0.1858 - y` 在 y>0.1858 时为负。早年
用 `max(0.1858-y, 1e-4)` 防除零，把负分母钳到 1e-4，CCT 算出千万级。只应在真过零
处保护。

## 降噪顺序（有坑）

降噪必须在 **LUT 之后、红点抑制之前**：
- LUT 会重新拉开对比、放大残留噪声，先降噪会被 LUT 重新带出来；
- 降噪会把红点晕成 halo，孤立性判据就抓不住，所以红点抑制必须在降噪之后。

## 第三方资产边界（法律，勿越界）

- `instant/assets/*.bin`：从 Xiaomi 17 Ultra ROM 提取，版权归 Xiaomi / Leica。
  仅作互操作与个人研究，**不授予再分发权**，权利人要求即移除。
- `instant/m9filter.py`：OffLineM9 的 MIT 代码**逐字副本**，刻意不改（它解析专有
  二进制布局，本地「清理」有静默解析错误的风险）。改动应回到上游。

## 常见维护动作

**换 LUT / 参数**：替换 `instant/assets/*.bin`，无需改代码（`LeicaFilter` 启动时读）。

**接入官方 StyleTrans**：在 `tonemap` 与 `apply_leica_lut` 之间插入网络推理段。
`apply_m9` 里的 `1 + 0.08×strength` 饱和补偿就是对缺失网络的粗糙近似，接入后应
归零。

**加新的白平衡档**：在 `develop.py::load_linear_srgb` 的白名单 + 分支里加，同时更新
`webapp/server.py` 的 `wb` 白名单、`render.py` 的 `choices`。

**调曲线**：改 `m9_tonemap` / `m3_tonemap` 的参数。改完必须跑回归（见下）。

## 回归检查清单

```bash
# 1. 编译
python -m py_compile render.py instant/*.py tools/*.py webapp/server.py

# 2. 三条路径渲染（RAW M9 / RAW M3 / JPG）
python render.py samples/mi8.dng -o /tmp/m9.jpg --quiet
python render.py samples/mi8.dng -o /tmp/m3.jpg --mode m3 --quiet
python render.py samples/user/thumb_camera.jpg -o /tmp/j.jpg --quiet

# 3. 数值核对（p50 应在 0.3-0.4，死白 <1%，M3 chroma≈0）
# 用 numpy/PIL 读回三个 JPG，检查 p50/p95/clip/chroma
```

注意：`samples/` 不在仓库里（含私人照片），回归样张需自备；`tools/diag_skin.py` 保留
了肤色 L\*/b\* 的分层诊断，可作为调色回归工具。

## 已知局限（诚实清单）

- **StyleTrans 神经网络未实现**（需 QNN/QAIRT 运行时，权重段有二次加密）。这是与
  官方效果的主要差距，现以饱和补偿近似。
- cc15/cv122/tdl13 等 B2Y 调校表的相机绑定矩阵未实现（无 17U 传感器标定）。
- 曝光归一对雪地/海滩等大高光占比场景可能失准（有 `--no-normalize` 逃生口）。
- 5000 万像素全尺寸处理内存峰值约 1GB，无 half-size 选项。
