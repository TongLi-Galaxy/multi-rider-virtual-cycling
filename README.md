# 多人骑行台功率考试软件 MVP

这是一个本地桌面端 Python MVP，支持 1~8 台 BLE 智能骑行台读取实时功率、固定时长测试、固定线路比赛、时间加权平均功率计算、惯性模拟速度/心率、赛道坡度、掉线统计和 CSV 导出。没有真实设备时可以使用 mock 模式测试完整流程。

## 安装

建议使用 Python 3.10 或更新版本：

```powershell
cd cycling_exam_app
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 启动桌面程序

真实 BLE 模式：

```powershell
python main.py
```

离线 mock 模式：

```powershell
python main.py --mock
```

mock 模式会模拟 8 台骑行台持续输出功率，并根据赛道坡度改变功率波动，适合先检查多分屏、计时、模拟速度、平均功率、心率和导出。

## 界面布局

新版主界面分为 4 个页面：

- `考试`：投屏用 1~8 人分屏，支持自动、1 列、2 列、3 列和 4 列显示；低至 800x600 可正常查看，空间紧张时会隐藏部分次要数据栏位，速度和功率保持大字显示。空间足够时会在上方显示赛道剖面图，高分辨率/高 DPI 下自动使用 Qt 缩放策略。
- `赛道`：编辑路段距离和坡度，并显示绿色海拔剖面图。
- `设置`：集中管理比赛模式、时长、整车自重、Mock、坡度推送、设备绑定、选手名和体重。
- `日志`：连接、掉线、订阅、坡度推送和导出等调试输出。

每个分屏主要显示：

- 选手名
- 体重
- 模拟速度
- 当前功率
- 模拟心率
- 平均功率
- 平均心率
- 当前坡度
- 已用时间
- 掉线时长
- 模拟距离
- 全程进度条
- 最终成绩

数据源状态显示在功率旁边的彩色圆点中，绿色代表已连接/数据正常，红色代表未连接或异常。

## 比赛模式

在 `设置` 页可以切换：

- `固定时长`：按设定秒数统一结束，适合功率考试。
- `固定线路`：按赛道总距离完赛，类似 ITT/大组赛。每个分屏到达终点后会单独锁定成绩，全体活跃分屏完赛后整场结束。

固定线路模式下，分屏会显示当前赛段编号和赛段进度。

## BLE 扫描

命令行扫描附近设备：

```powershell
python main.py scan --timeout 8
```

桌面程序中点击“扫描/绑定”，扫描后选择设备和分屏编号，再点击“绑定到分屏”。绑定会保存到 `config/devices.json`，下次启动自动加载。

## 单设备读取功率

```powershell
python main.py read --address "设备地址或 MAC" --duration 60
```

程序会优先订阅 FTMS Indoor Bike Data `0x2AD2`，如果没有则尝试 Cycling Power Measurement `0x2A63`。

如果设备暴露 FTMS Control Point `0x2AD9`，考试中可勾选“推送坡度”，软件会尝试发送 FTMS `Set Indoor Bike Simulation Parameters`，把当前赛道坡度推送给骑行台。不同厂商对 FTMS 控制支持不完全一致，失败会写入日志页，不会影响功率读取。

## 多设备命令行测试

```powershell
python main.py multi --addresses "地址1" "地址2" "地址3" ... "地址8" --duration 60
```

每个设备独立 asyncio 任务运行，一个设备失败不会停止其他设备。

## Windows 蓝牙注意事项

- 确认 Windows 蓝牙已打开，电脑蓝牙适配器支持 BLE。
- 第一次扫描或连接时，Windows 可能弹出蓝牙权限或配对提示。
- 某些骑行台被手机 App、码表或其他软件连接后，会拒绝电脑连接。请先断开其他 App。
- Windows 的 BLE 服务 UUID 有时只在连接后才能完整读取，扫描页能显示的是广播包中暴露的服务。

## 开始考试

1. 扫描并绑定最多 8 台骑行台。
2. 点击“连接设备”，等待状态变为“已连接”或“数据正常”。
3. 在“设置”页输入选手名、体重、整车自重，选择固定时长或固定线路。
4. 在“赛道”页设计路段距离和坡度，点击“应用赛道”或“保存赛道”。
5. 固定时长模式下选择考试时长或自定义秒数。
6. 点击“准备考试”，检查设备状态。
7. 点击“开始考试”统一计时。
8. 时间结束后自动锁定成绩；也可以点击“终止考试”，结果会标记为 `aborted`。

考试结束后，平均功率不会继续被后续数据改变。

## 数据导出

点击“导出 CSV”后会在 `exports/<exam_id>/` 生成：

- `summary.csv`：每个分屏的最终成绩。
- `samples.csv`：考试过程中的时序样本。

`summary.csv` 包含平均功率、最大功率、有效采样时长、掉线时长和完成状态。

新版 `summary.csv` 还包含体重、整车自重、比赛模式、线路距离、平均心率、最大心率、模拟距离和平均速度。`samples.csv` 还包含模拟速度、模拟距离、坡度、赛段编号、赛段进度和心率。

## 赛道和模拟速度

赛道配置保存在 `config/route.json`。每个路段包含：

- `distance_m`：路段距离，单位米。
- `grade_percent`：坡度百分比，允许 -20% 到 25%。

模拟速度使用功率、体重、整车自重、坡度、滚阻和空气阻力计算受力，并保留速度状态做积分。因此加速、减速、上坡、下坡都会有动量效果，不再是按功率瞬时拟合稳态速度。它适合考试投屏和相对比较，不等同于经过标定的真实路速。

当前心率是模拟值，不读取真实心率带。后续可以扩展 BLE Heart Rate Service。

## 常见问题

### 扫不到设备怎么办

确认骑行台已通电并处于可连接状态，关闭手机 App/码表连接，靠近电脑后重试。部分设备需要先踩几下才开始广播 BLE。

### 设备被手机 App 占用怎么办

关闭手机 App、码表或训练软件中的连接，必要时关闭手机蓝牙，再重新扫描和连接。

### 设备支持蓝牙但没有功率数据怎么办

第一版只读取 FTMS Indoor Bike Data 和 Cycling Power Measurement。如果设备只提供厂商私有服务，界面会显示“不支持功率读取”。

### 多设备连接不稳定怎么办

尽量使用质量较好的蓝牙 5.x 适配器，把电脑放近骑行台，减少 2.4GHz 干扰，并避免其他软件同时连接同一批设备。

### 坡度推送没有效果怎么办

确认骑行台支持 FTMS Control Point 和 Indoor Bike Simulation Parameters。有些设备只广播 Cycling Power Service，只能读取功率，不能控制阻力。也有设备需要官方 App 断开后才允许第三方获取控制权。

## 项目结构

```text
cycling_exam_app/
  main.py
  requirements.txt
  README.md
  config/devices.json
  config/settings.json
  config/route.json
  app/
    ble/
      scanner.py
      device_client.py
      parsers.py
    core/
      exam_controller.py
      exporter.py
      metrics.py
      route.py
      settings.py
      rider_state.py
      simulation.py
    gui/
      main_window.py
      rider_panel.py
      route_profile_widget.py
      scan_dialog.py
    utils/
      logger.py
```
