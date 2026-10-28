# Fincard AI Playtester

让大模型通过游戏接口试玩 **Fincard**，你在真实游戏窗口中全程观看。

这不是快速跑数的贪心机器人：AI会读取局面、解释理由、连续出牌，选择事件和奖励，最后给出有证据的试玩建议。

## 特点

- **真实可见**：正常Godot窗口运行，手牌出牌使用原本的点击与动画流程。
- **可接管**：开始／暂停、AI单步；暂停后可直接在游戏窗口人工操作。
- **可追溯**：本地保存每步局面、动作、选择理由和关键截图。
- **独立隔离**：接口适配器也放在本仓库；不修改游戏的代码、规则、数值或默认启动方式。
- **可配置模型**：支持OpenAI兼容的`/chat/completions`接口，可选发送截图给视觉模型。
- **不偷看**：不向模型暴露牌库顺序、隐藏种子、随机数状态或未来随机结果。
- **默认不收费**：启动游戏不请求模型；明确点击开始或单步并确认后才调用，不自动重试。

## 准备

1. Windows上安装 **Python 3.10+**（含Tkinter）和与游戏兼容的 **Godot 4.7.1**。
2. 下载[Fincard游戏源码](https://github.com/w12z/Fincard)，不是Release里的EXE。两个仓库建议并排放：

   ```text
   Documents/
     Fincard/                 # 游戏，包含project.godot
     Fincard-AI-Playtester/    # 本仓库
   ```

3. 准备一个OpenAI兼容接口的地址、模型名称和你自己的API Key。

没有第三方Python依赖，不需要`pip install`。截图默认仅在本地保存；模型评审不等同于真人试玩或统计平衡结论。

## 快速启动

双击 **`start.bat`**，或：

```powershell
python playtester.py
```

在控制台中填写：

| 设置 | 含义 |
| --- | --- |
| 游戏源码路径 | Fincard的`project.godot`所在目录 |
| Godot路径 | `godot_console`，或Godot可执行文件的完整路径 |
| API Base URL | 例如`https://api.openai.com/v1`，不包含`/chat/completions` |
| 模型名 | 服务商提供的准确模型ID，本项目不预设付费模型 |
| API Key | 仅在内存中使用，不保存；也可通过环境变量提供 |
| 国家 | `example_country`新港，`arcadia`阿卡迪亚 |
| 每步展示秒数 | AI解释后等待的时间，默认2秒 |
| 本局AI调用上限 | 包括决策和额外报告，默认250次；不是金额预算 |

### 操作顺序

1. 点击 **启动游戏**。打开真实游戏窗口，进入所选国家，AI默认暂停。
2. 将游戏与控制台并排摆放。可正常缩放游戏窗口，或在本地配置修改`window_size`。
3. 点击 **AI单步**体验一次，或 **开始／继续AI**自动游玩。
4. 控制台先显示AI动作和理由，等待展示时间，再在游戏窗口执行。
5. 点击 **暂停／人工接管**。模型请求若尚未完成，返回决策会被丢弃；已提交的动作会先完成动画。等状态显示已暂停后，可直接在游戏里操作。
6. 人工操作后点击 **刷新局面**查看当前状态；继续AI时会重新观察，旧决策不会套用到新局面。
7. 点击 **生成建议**额外请求一次模型，输出`ai-report.md`；不想继续付费，可直接看已有`summary.md`。

自动模式中不要同时手动点击游戏。若检测到局面变化，AI会暂停，而不是冒险执行旧动作。
更换模型或接口时，请重新打开控制台；启动后不会静默切换已创建的模型客户端。

## 配置与密钥

“保存设置”生成`config.local.json`，不会保存API Key；此文件和`runs/`均已加入Git忽略。
也可以复制`config.example.json`后编辑。

仅当前PowerShell窗口设置密钥：

```powershell
$env:FINCARD_AI_API_KEY = "你的密钥"
python playtester.py
```

不必在游戏项目中配置任何模型密钥。不要将密钥写入源码、示例配置、截图或GitHub Issue。

### 兼容性设置

- 默认发送`max_tokens`限制输出。若模型要求`max_completion_tokens`，在本地JSON中设置：

  ```json
  "token_parameter": "max_completion_tokens"
  ```

- `max_completion_tokens`配置项表示输出token数量上限，不论选择哪个参数名称。
- 使用本地OpenAI兼容模型时，可配置`http://127.0.0.1:端口/v1`；只有回环地址允许HTTP，远端地址必须HTTPS。
- 只支持Chat Completions协议，不宣称支持仅提供Responses或Anthropic Messages的服务。
- 模型必须能返回包含`action`、`reason`、`feedback`的JSON。非法动作、无效JSON、超时或限流均会暂停，不自动重复收费请求。
- 勾选“发送游戏截图”需要模型支持`image_url`。默认关闭，纯文本模型即可体验规则与玩法；未看截图时UI评价只能作为推测。
- `seed: 0`为正常随机开局；非零值可固定开局种子。种子仅给启动器，不发送给模型。

## 对局记录

`runs/日期时间-随机后缀/`：

| 文件 | 内容 |
| --- | --- |
| `session.json` | 模型名称、游戏提交、工作区是否有未提交改动、启动配置摘要 |
| `actions.jsonl` | 每步操作前后局面、动作、理由、反馈、来源 |
| `step-*.png` | 开局及各AI操作完成后的关键截图，不是逐帧视频 |
| `summary.md` | 无需额外模型调用的过程发现汇总 |
| `usage.json` | 服务商实际返回的token使用量，不推算或保证金额 |
| `ai-report.md` | 手动点击“生成建议”后得到的评审 |
| `godot.log` | 游戏日志，用于排查接口或启动问题 |

截图默认保存，可通过`save_screenshots: false`关闭。本工具只记录经它执行的动作；人工接管期间的鼠标操作不录成逐步回放，继续后记录新的实际局面。日志与截图不会自动上传GitHub。

## 接口与安全边界

`game_bridge.gd`是外置可选适配器：

```text
Godot --path 游戏源码目录 --script 本仓库/game_bridge.gd
```

应通过启动器运行，它会自动提供一次性随机token和随机本机端口。
直接运行上面命令但未提供授权环境会拒绝启动。

- TCP JSON行协议，仅监听`127.0.0.1`，每个请求必须包含token。
- 只提供`observe`、`act`、`screenshot`，没有任意代码执行、任意文件读写、改数值或偷看牌库接口。
- `act`必须携带最新`revision`和当前`legal_actions`中的完整动作，否则拒绝。
- 出牌通过实际手牌按钮，其他动作通过原游戏`Command`和`GameUI`处理，并等待动画完成。
- 不使用自动跳过年度会议的批量捷径；奖励逐组领取或放弃，保留原版删牌规则。
- 常规游戏启动完全不加载适配器，无端口、无模型调用。
- 首次运行可能让Godot生成正常的`.godot/`导入缓存，不修改游戏源码。
- API Key不传给游戏子进程。网络请求错误不输出Authorization、原始服务商响应或密钥。

详见[PROTOCOL.md](PROTOCOL.md)。

## 检查（不调用付费模型）

```powershell
python -m unittest -v test_engine.py
python smoke_test.py --headless --steps 40
python smoke_test.py --steps 15
```

单元测试使用本机假模型服务；接口检查会启动真实游戏，按简单固定策略操作，不改规则、不做平衡模拟。
正常试玩启动器始终打开可见游戏窗口，`--headless`仅供接口检查。

## 限制

- 模型质量和单局建议取决于模型能力；此工具不保证获胜，也不把随机输赢当作平衡结论。
- 不额外提供游戏存档或恢复能力。对局日志不是可直接加载的存档。
- 适配器使用当前Fincard类名与UI接口；未来游戏结构变更可能需要更新本仓库中的适配器。
- 新版自动出牌动画取决于你使用的游戏源码版本，本试玩器不会修补或替换游戏逻辑。
