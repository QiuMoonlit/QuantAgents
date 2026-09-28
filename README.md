<div align="center">
  <h1>QuantAgent</h1>
  <p><b>多智能体 LLM 交易研究框架 —— 原生支持 A 股与港股</b></p>
  <p>
    <a href="https://github.com/QiuMoonlit/QuantAgents">GitHub</a>
    &nbsp;·&nbsp; v0.6.0 &nbsp;·&nbsp; Python 3.10+
  </p>
</div>


---

> **QuantAgent 是研究工具，不构成任何投资建议。** 输出不构成买入或卖出任何证券的建议。实际表现取决于所用大模型、采样温度、时间区间、数据质量等多种因素，存在不可消除的随机性。请自行判断并承担风险。

---

## 目录

- [核心能力](#核心能力)
- [多智能体架构](#多智能体架构)
- [安装](#安装)
- [快速开始](#快速开始)
- [网页界面](#网页界面)
- [命令行使用](#命令行使用)
- [Python 调用](#python-调用)
- [配置说明](#配置说明)
- [A 股与港股支持](#a-股与港股支持)
- [情绪数据源](#情绪数据源)
- [已知缺口](#已知缺口)
- [当前持仓](#当前持仓)
- [持久化与恢复](#持久化与恢复)
- [回测](#回测)
- [可复现性](#可复现性)
- [开发](#开发)


---

## 核心能力

| 能力             | 说明                                                         |
| ---------------- | ------------------------------------------------------------ |
| **多智能体协作** | LangGraph 工作流：4 个分析师并行、多空辩论、研究主管裁决、Trader 制定方案、三位风控智能体施压、投资组合经理给出评级 |
| **A 股 / 港股**  | 行情、技术指标、财务三大表（中国企业会计准则）、个股新闻、散户情绪、结算价格全覆盖 |
| **中文报告**     | 分析报告与最终决策中文输出；机器可读的评级词表保持英文，保证信号可解析 |
| **网页界面**     | 深色终端风格 UI，SSE 实时流式展示每个智能体进度，可中途中止  |
| **真实可中止**   | 中止会真正终止服务端工作线程与后续 LLM 调用，不是前端假象    |
| **成本可见**     | 实时显示模型调用次数、Token 用量与预估费用                   |
| **决策日志**     | 每次运行落盘；历史决策在持有窗口走完后结算实际收益与 alpha，生成复盘反思注入下一次分析 |
| **断点续跑**     | 可选 LangGraph checkpoint，崩溃或中断后从最后一步继续        |
| **回测**         | 在「代码 × 日期」网格上跑同一套流程，按评级分组统计实际 alpha |
| **多供应商**     | OpenAI、Anthropic、Google、Azure、AWS Bedrock、xAI、DeepSeek、通义、智谱、MiniMax、OpenRouter、Mistral、Kimi、Groq、NVIDIA、Ollama 本地模型，以及任意 OpenAI 兼容端点 |

---

## 多智能体架构

```
                    ┌─────────────────────────────────────┐
                    │           分析师团队 (并行)            │
                    │                                     │
                    │  技术分析师 ─┐                        │
                    │  新闻分析师 ─┤                        │
                    │  基本面分析师─┼─→ 同时执行 ────────────┤
                    │  情绪分析师 ─┘                        │
                    └─────────────────────────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │          多空辩论 (N 轮)           │
                    │   看多研究员 ⇄ 看空研究员          │
                    └────────────────┬────────────────┘
                                     │
                              研究主管裁决
                                     │
                               Trader 制定
                                     │
                    ┌────────────────┴────────────────┐
                    │          风控辩论 (M 轮)           │
                    │  激进 ⇄ 保守 ⇄ 中立               │
                    └────────────────┬────────────────┘
                                     │
                            投资组合经理裁决
                                     │
                           最终评级 + 写入决策日志
```

**关键设计点**

- **分析师并行执行**，不是串行。四个分析师同时跑，状态面板会如实显示「三个正在运行」，而不是靠名单顺序猜。
- **多空辩论是可选的**。设为 0 轮时，分析师报告直接交给研究主管。
- **研究主管有裁决 ⇒ 多空辩论必然已结束**。即使辩论轮数为 0、history 为空，状态机也能正确推断这一点。
- **评级是结构化传递的**。投资组合经理输出结构化对象，其 `final_rating` 字段单独经 state 传到信号层，不再依赖解析英文散文。解析散文只是兜底路径，不是主路径。
- **分析日期不晚于今天**。`propagate()` 会拒绝未来日期，因为没有可结算的结果。

---

## 安装

### 环境要求

- Python **3.10+**（开发与镜像使用 3.12）
- 至少一个 LLM 供应商的 API Key

### 获取源码

```bash
git clone https://github.com/QiuMoonlit/QuantAgents.git
cd QuantAgents
```

创建虚拟环境（三选一）：

```bash
# conda
conda create -n quantagent python=3.12
conda activate quantagent

# uv
uv venv --python 3.12
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# venv
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 安装依赖

```bash
# 最小安装（终端版 CLI）
pip install .

# 完整安装：网页界面 + A股/港股数据 + 开发工具
pip install -e ".[web,cn,dev]"

# 支持 AWS Bedrock
pip install -e ".[bedrock]"
```

可选依赖组（`pyproject.toml` 中定义）：

| 组        | 内容                                                    |
| --------- | ------------------------------------------------------- |
| *(核心)*  | LangChain / LangGraph、yfinance、pandas、typer、rich 等 |
| `web`     | FastAPI + uvicorn，供 `quantagent-web` 使用             |
| `cn`      | akshare，A 股与港股数据                                 |
| `bedrock` | langchain-aws（AWS SigV4 认证）                         |
| `dev`     | pytest、pytest-subtests、ruff                           |

### Windows：两个必须知道的坑

#### 坑一：项目路径含非 ASCII 字符 → 行情请求全部失败

yfinance 通过 curl_cffi 访问 Yahoo，而 curl_cffi 的原生层用系统 ANSI 代码页解码文件路径。当项目位于含中文的目录下时，`certifi.where()` 返回的路径是乱码的：

```
curl_cffi.requests.exceptions.SSLError: curl: (77) error adding trust anchors
```

**证书文件本身是好的**，curl 只是读不了那个路径。把证书复制到纯 ASCII 路径即可：

```bash
python scripts\fix_ca_bundle.py
```

然后把脚本打印出来的路径写进 `.env`：

```env
CURL_CA_BUNDLE=C:\Users\<你的用户名>\quantagent-cacert.pem
```

> 重建虚拟环境后需要重跑一次这个脚本。或者干脆把项目移到纯 ASCII 路径（如 `C:\src\QuantAgents`），一劳永逸。

#### 坑二：状态目录不可写 → 运行中途崩溃

QuantAgent 默认把缓存、结果、决策日志写在 `~/.quantagent`。在**普通终端**里这是对的；但在**沙箱、容器或受限上下文**中，用户主目录不在可写范围内，运行会在图构建阶段抛出：

```
PermissionError: [WinError 5] 拒绝访问: 'C:\Users\<你>\.quantagent\cache'
```

遇到时把三个状态路径指向项目内部：

```env
QUANTAGENT_RESULTS_DIR=.\state\logs
QUANTAGENT_CACHE_DIR=.\state\cache
QUANTAGENT_MEMORY_LOG_PATH=.\state\memory\trading_memory.md
```

v0.6.0 起，网页界面会在**启动时自检**这两个目录（写入并删除一个探针文件 —— 目录存在不等于可写，Windows 上只读 ACL 才是常见情况），并在 `/api/config` 暴露 `state_dirs_writable`。不可写时 `/api/analyze` 直接返回 **503 并说明是哪个路径、怎么修**，而不是让你等三十秒、花完钱再失败。

### Docker

```bash
cp .env.example .env          # 填入你的 API Key
docker compose run --rm quantagent
```

拉取仓库更新后需重建镜像：`docker compose build`。状态目录挂载在命名卷 `quantagent_data`。

用 Ollama 跑本地模型：

```bash
docker compose --profile ollama run --rm quantagent-ollama
```

---

## 快速开始

```bash
cp .env.example .env
```

最小配置（DeepSeek + 中文输出）：

```env
DEEPSEEK_API_KEY=sk-...
QUANTAGENT_LLM_PROVIDER=deepseek
QUANTAGENT_DEEP_THINK_LLM=deepseek-reasoner
QUANTAGENT_QUICK_THINK_LLM=deepseek-chat
QUANTAGENT_OUTPUT_LANGUAGE=Simplified Chinese
```

```bash
quantagent            # 终端版（交互式）
quantagent-web        # 网页版 → http://127.0.0.1:8420
```

然后分析 `600519.SS`、`0700.HK` 或 `NVDA`。

### 先花两秒验证数据链路

跑一次完整分析要几分钟。启动前想先确认网络通不通：

```bash
python -c "from quantagent.dataflows.vendors.akshare.market import get_cn_stock_data; print(get_cn_stock_data('600519.SS','2026-08-20','2026-09-02')[:400])"
```

能出表格说明行情源可达。**如果报 `ProxyError`，说明本地代理没启动** —— 在部分网络环境下访问国内行情源必须走代理。

---

## 网页界面

```bash
pip install -e ".[web]"
quantagent-web                       # http://127.0.0.1:8420
```

或直接用 uvicorn：

```bash
uvicorn quantagent.web.server:app --port 8420
```

### 界面构成

- **左栏**：股票代码、分析日期、要运行的分析师、辩论轮数。全部预填自 `.env` / `DEFAULT_CONFIG`。
- **中部**：五个智能体团队，每个智能体随图推进在 `等待中 → 分析中 → 已完成` 之间切换，下方报告面板实时刷新。
- **最终决策卡**：解析出的评级，按颜色区分（买入/增持 绿、持有 橙、减持/卖出 红）。
- **第二个页签**：读取写入 `state/memory/trading_memory.md` 的决策日志。
- **右上角**：模型调用次数、Token 用量、预估费用、耗时。

### HTTP 接口

| 方法   | 路径                               | 用途                                          |
| ------ | ---------------------------------- | --------------------------------------------- |
| `GET`  | `/`                                | UI 页面                                       |
| `GET`  | `/api/teams`                       | 智能体团队与状态定义                          |
| `GET`  | `/api/config`                      | 当前配置（含 `state_dirs_writable` 自检结果） |
| `POST` | `/api/analyze`                     | 启动一次分析                                  |
| `POST` | `/api/cancel/{run_id}`             | 中止运行                                      |
| `GET`  | `/api/runs` · `/api/runs/{run_id}` | 查询历史运行与状态                            |
| `GET`  | `/api/stream/{run_id}`             | SSE 进度流                                    |

### 实现原理

`quantagent/graph/propagation.py` 本来就以 `stream_mode="values"` 运行图，因此每个节点完成时都会产出完整状态。`quantagent/web/server.py` 对状态做差分来判断哪个智能体刚刚完成，然后推送一个 SSE 帧。

**图、智能体、数据供应商层都没有为此做过改动** —— 终端 CLI 消费的是同一个流。

SSE 断线会自动重连，事件有缓冲可重放，`/api/runs/{run_id}` 可回查任意运行的状态。

### 并发模型

运行之间由单锁串行化：供应商路由和决策日志持有进程级状态，两次并发运行会互相穿插。**一次一个分析**，这也正好符合 LLM 成本的使用方式。排队时界面会显示「排队中」状态。

### 安全边界

**不包含**：无身份认证、无多用户隔离，且只绑定回环地址。**在绑定到 `127.0.0.1` 以外的任何地址之前，请先放在带认证的反向代理后面。**

---

## 命令行使用

```bash
quantagent                 # 安装后的命令
python -m cli.main         # 从源码直接运行
```

界面会让你依次选择股票代码、分析日期、LLM 供应商、研究深度等。**上一次运行的答案会作为默认值回填**，直接回车即可接受。

`.env` 里的 `QUANTAGENT_*` 变量会**直接跳过对应的提问步骤**（供应商、模型、输出语言、辩论轮数等）。旧前缀 `TRADINGAGENTS_*` 同样被识别，因此老 `.env` 无需修改即可继续使用；两者同时设置时以新写法为准。

### 常用参数

```bash
quantagent --checkpoint            # 启用断点续跑
quantagent --clear-checkpoints     # 运行前重置全部 checkpoint
quantagent --portfolio my_book.json # 传入当前持仓
quantagent backtest NVDA,AAPL --start 2026-06-01 --end 2026-08-01 --every 7
```

### 支持的市场与代码格式

| 市场         | 代码格式      | 示例                                       |
| ------------ | ------------- | ------------------------------------------ |
| A 股（上海） | `.SS`         | `600519.SS` 贵州茅台、`601318.SS` 中国平安 |
| A 股（深圳） | `.SZ`         | `000001.SZ` 平安银行、`300750.SZ` 宁德时代 |
| 港股         | `.HK`         | `0700.HK` 腾讯、`09992.HK` 美团            |
| 美股         | 无后缀        | `NVDA`、`AAPL`                             |
| 日股         | `.T`          | `7203.T`                                   |
| 英股         | `.L`          | `AZN.L`                                    |
| 印股         | `.NS` / `.BO` | `RELIANCE.NS`                              |
| 加密货币     | `-USD`        | `BTC-USD`、`ETH-USD`                       |

> **请带上交易所后缀。** A 股裸代码（如 `600519`）在 AkShare 数据源里能正确解析，但公司身份查询走的是另一条链路，需要后缀才会返回结果。

---

## Python 调用

```python
from quantagent.default_config import DEFAULT_CONFIG
from quantagent.graph.trading_graph import QuantAgentGraph

ta = QuantAgentGraph(debug=True, config=DEFAULT_CONFIG.copy())
_, decision = ta.propagate("600519.SS", "2026-09-01")
print(decision)
```

`propagate()` 返回 `(final_state, signal)`。`signal` 是五档评级之一（`Buy` / `Overweight` / `Hold` / `Underweight` / `Sell`），或在决策没有可解析评级时返回 `"REVIEW"` —— 用 `quantagent.agents.rating.is_review` 判定后再映射为枚举。

自定义配置：

```python
config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "deepseek"
config["deep_think_llm"] = "deepseek-reasoner"   # 复杂推理
config["quick_think_llm"] = "deepseek-chat"      # 快速任务
config["max_debate_rounds"] = 2
config["max_risk_discuss_rounds"] = 2

ta = QuantAgentGraph(debug=True, config=config)
_, decision = ta.propagate("600519.SS", "2026-09-01")
```

全部配置项见 `quantagent/default_config.py`。用 `ta.save_reports(final_state, "600519.SS")` 可把报告树写到 `results_dir/reports/` 下。

### 只选部分智能体

分析师的选择是**构造参数**，不是 `propagate()` 的参数：

```python
ta = QuantAgentGraph(
    selected_analysts=["market", "social"],
    config=DEFAULT_CONFIG.copy(),
)
_, decision = ta.propagate("600519.SS", "2026-09-01")
```

| key            | 智能体       |
| -------------- | ------------ |
| `market`       | 技术分析师   |
| `news`         | 新闻分析师   |
| `fundamentals` | 基本面分析师 |
| `social`       | 情绪分析师   |

> 注意情绪分析师的 key 是 **`social`** 而不是 `sentiment` —— 后者是它在编译后图中的节点名。`run_backtest()` 也接受 `selected_analysts`。
>
> 分析师 key 的**唯一真源**是 `quantagent/graph/analyst_execution.py` 里的 `ANALYST_NODE_SPECS`；网页界面从它派生校验规则，而不是自己抄一份。

### 加密货币

```python
_, decision = ta.propagate("BTC-USD", "2026-09-01", asset_type="crypto")
```

CLI 会根据代码自动判断 `asset_type`，程序化调用时需显式传入。

---

## 配置说明

全部配置通过 `.env` 或 `config` 字典完成，环境变量前缀为 `QUANTAGENT_`。

### LLM 供应商

```bash
OPENAI_API_KEY=...          # OpenAI (GPT)
GOOGLE_API_KEY=...          # Google (Gemini)
ANTHROPIC_API_KEY=...       # Anthropic (Claude)
XAI_API_KEY=...             # xAI (Grok)
DEEPSEEK_API_KEY=...        # DeepSeek
DASHSCOPE_API_KEY=...       # 通义千问 — 国际站
DASHSCOPE_CN_API_KEY=...    # 通义千问 — 国内站
ZHIPU_API_KEY=...           # GLM 国际站
ZHIPU_CN_API_KEY=...        # GLM 国内站
MINIMAX_API_KEY=...         # MiniMax 全球站
MINIMAX_CN_API_KEY=...      # MiniMax 国内站
OPENROUTER_API_KEY=...      # OpenRouter
MISTRAL_API_KEY=...         # Mistral
MOONSHOT_API_KEY=...        # Kimi
GROQ_API_KEY=...            # Groq
NVIDIA_API_KEY=...          # NVIDIA NIM
```

对应的 `llm_provider` 取值：`openai`、`anthropic`、`google`、`xai`、`deepseek`、`qwen`、`qwen-cn`、`glm`、`glm-cn`、`minimax`、`minimax-cn`、`openrouter`、`mistral`、`kimi`、`groq`、`nvidia`。

**双区域供应商**（`qwen` / `glm` / `minimax`）的国际站与国内站是独立账号，密钥不可互换。

其他供应商：

- **Azure OpenAI**：`llm_provider: "azure"`，设置 `AZURE_OPENAI_API_KEY`、`AZURE_OPENAI_ENDPOINT`、`AZURE_OPENAI_DEPLOYMENT_NAME`。
- **AWS Bedrock**：`pip install ".[bedrock]"`，`llm_provider: "bedrock"`，配置 AWS 凭据链或 `AWS_BEARER_TOKEN_BEDROCK`，并设置 `AWS_DEFAULT_REGION`。
- **Ollama 本地模型**：`llm_provider: "ollama"`，默认端点 `http://localhost:11434/v1`（用 `OLLAMA_BASE_URL` 改），先 `ollama pull <name>`。本地模型无需 API Key。
- **任意 OpenAI 兼容服务**（vLLM、LM Studio、llama.cpp、自建中转）：`llm_provider: "openai_compatible"`，通过 `backend_url` 或 `QUANTAGENT_LLM_BACKEND_URL` 设置端点。本地服务不需要 Key，端点要求鉴权时设 `OPENAI_COMPATIBLE_API_KEY`。

任何供应商提供的模型 ID 都可以直接填在 `deep_think_llm` / `quick_think_llm` 里，不必局限于选项列表。

> **供应商与 API Key 的对应表是代码生成的事实来源**：`quantagent/llm_clients/api_key_env.py` 的 `PROVIDER_API_KEY_ENV`，端点与能力差异在 `openai_client.py` 的 `OPENAI_COMPATIBLE_PROVIDERS`。新增供应商时改这两处，CLI 的 Key 提问会自动跟上。

### 通用运行参数

```bash
QUANTAGENT_TEMPERATURE=0.0        # 采样温度（推理模型基本忽略）
QUANTAGENT_LLM_MAX_RETRIES=6      # SDK 重试预算，用于扛住突发 429 限流
QUANTAGENT_MAX_TOKENS=8192        # 输出 token 上限，防止模型长时间空转触发网关超时
QUANTAGENT_CHECKPOINT_ENABLED=false
QUANTAGENT_BENCHMARK_TICKER=SPY   # 覆盖基准；不设则按交易所后缀自动匹配
```

按供应商的推理深度（设置后 CLI 也会跳过对应提问）：

```bash
QUANTAGENT_OPENAI_REASONING_EFFORT=medium
QUANTAGENT_GOOGLE_THINKING_LEVEL=high
QUANTAGENT_ANTHROPIC_EFFORT=high
```

### 数据源

数据源按**类别**配置，可写多个形成降级链（逗号分隔，按顺序尝试）：

```python
config["data_vendors"] = {
    "core_stock_apis":      "akshare,yfinance",   # 行情 OHLCV
    "technical_indicators": "akshare,yfinance",   # 技术指标
    "fundamental_data":     "akshare,yfinance",   # 财务数据
    "news_data":            "akshare,yfinance",   # 新闻
    "sentiment_data":       "akshare,yfinance",   # 散户情绪
    "macro_data":           "fred",               # 宏观指标
    "prediction_markets":   "polymarket",         # 预测市场
}
```

**这正是默认值**，装好即用。`akshare` 排在前面对美股没有额外成本 —— 它在**发出任何网络请求之前**就会根据代码判断是否为中国市场并直接让开。

也可以在 `tool_vendors` 里对单个工具覆盖类别默认：

```python
config["tool_vendors"] = {"get_stock_data": "alpha_vantage"}
```

其他可选数据源：

```bash
SEC_EDGAR_USER_AGENT="Your Name your@email.com"   # SEC EDGAR 财报（需可联系地址）
FRED_API_KEY=...                                    # 宏观数据（免费，可选）
TYPESAFE_API_KEY=...                                # Jev 社交帖筛选（可选）
```

**SEC EDGAR 特别说明**：美国公司财报可取自 EDGAR，它记录了每个数字的**申报日期**。因此一个历史日期的运行会读到那天当时的报表原貌：已结束但尚未申报的财年不会被返回，后来重述过的数字仍以首次申报值为准。

以苹果总资产为例，同一份年报在 `2009-06-30` 的运行中读到 **39,572**（百万美元），在 `2011-01-01` 的运行中读到重述后的 **36,171** —— 不是「修正了旧数据」，而是每次运行各自读到了当时已公开的那一版（`tests/test_sec_edgar.py` 固化了这个行为）。

### 决策日志与状态路径

```bash
QUANTAGENT_MEMORY_LOG_PATH=./state/memory/trading_memory.md
QUANTAGENT_RESULTS_DIR=./state/logs
QUANTAGENT_CACHE_DIR=./state/cache
```

不设置时使用 `~/.quantagent` 下的 `memory/`、`logs/`、`cache/`。

---

## A 股与港股支持

```bash
pip install -e ".[cn]"     # 安装 akshare
```

### 已接入

| 能力           | 说明                                                         |
| -------------- | ------------------------------------------------------------ |
| **日线 OHLCV** | 中文日期/开/收/高/低/成交量字段映射到统一契约；A 股成交量从**手**换算为**股**（×100） |
| **技术指标**   | 复用 stockstats，与美股**同一套计算逻辑**，不存在两套实现漂移的风险 |
| **财务三大表** | 资产负债表 / 利润表 / 现金流量表，**中国企业会计准则**科目映射 |
| **关键指标**   | 基本每股收益、ROE、销售毛利率、销售净利率等                  |
| **个股新闻**   | 东方财富个股新闻，按分析窗口过滤                             |
| **散户情绪**   | 东方财富股吧：关注指数、情绪评分、人气排名（见下文）         |
| **验证快照**   | 市场分析师的确定性价格快照，A 股与美股共用同一套渲染器       |
| **结算价格**   | 决策结算与 alpha 计算的价格序列                              |

### 代码格式

| 输入               | 解析结果         | 说明                                 |
| ------------------ | ---------------- | ------------------------------------ |
| `600519.SS`        | `600519.SS` 上海 | 正确写法                             |
| `600519.SH`        | `600519.SS` 上海 | Yahoo 写法自动转正                   |
| `600519`           | `600519.SS` 上海 | 裸代码按首位数字判断板块             |
| `688981`           | `688981.SS` 上海 | 科创板归上海                         |
| `300750`           | `300750.SZ` 深圳 | 创业板归深圳                         |
| `0700.HK`          | `00700.HK` 港股  | 自动补齐五位                         |
| `AAPL` / `BTC-USD` | 拒绝             | 非中国代码，直接让开交给下一个数据源 |

### 财务科目映射

中国企业会计准则与美股 GAAP 科目**没有一一对应关系**。框架采用映射表而非直接改名，并在每份报告头部标明准则来源。

两处映射**并非精确等价**，报告中会明确标注：

- **Revenue** 优先取 `营业收入`，无此科目时回退到 `营业总收入`。两者对金融机构不同 —— 银行把利息收入与手续费收入单独列报，回退值口径更宽。
- **Net Income Attributable to Parent** 对应 `归母净利润`，口径**窄于**美股同名的 Net Income。

供应商未以任何候选名称提供的科目，会渲染为 `N/A: 未以该名称披露`，**既不丢弃也不臆造**。

---

## 情绪数据源

美股读 **StockTwits**（用户自标 Bullish/Bearish）与 **Reddit**（r/wallstreetbets、r/stocks、r/investing）。A 股读 **东方财富股吧** —— 它在中国承担着同样的制度角色。

### 但股吧不是消息流

**雪球没有被使用。** AkShare 的雪球接口是热门话题榜和持仓榜，**不存在个股讨论流** —— `stock_hot_tweet_xq(symbol="SH600519")` 会抛 `KeyError`，因为它只提供全局热门列表。声称有雪球情绪源会是编造。

因此 A 股情绪是**一组指标**，而不是帖子：

| 信号                                 | 含义                                                         |
| ------------------------------------ | ------------------------------------------------------------ |
| **用户关注指数**（30 个交易日）      | 散户论坛的关注热度。**是热度，不是方向** —— 关注度上升而价格不动，说明大家在看而不是在买 |
| **综合得分 / 机构参与度 / 主力成本** | 个股情绪评分卡。`主力成本`是散户平均持仓成本，**不是目标价** —— 输出中会明确写出这一点，因为它读起来很像 |
| **人气排名 + 新晋粉丝/铁杆粉丝**     | 人气排名随时间的变化，拆分为追高型与持股型粉丝。**新晋粉丝占比上升而价格无反应 = 散户流入，不是持有信心** |

某个源临时不可用时，**保留其余可用源并列出不可用的项**；全部不可用时抛出明确异常，**而不是给分析师一个空块** —— 空块会被读成「没有情绪」，那是与「这个市场的情绪数据缺失」完全不同的结论。

---

## 已知缺口

以下功能对 A 股**仍会降级而非失败**：

| 缺口               | 对 A 股的影响                                                |
| ------------------ | ------------------------------------------------------------ |
| **董监高持股变动** | 美股 Form 4 在 A 股无对应物；董监高持股变动是披露口径与频率都不同的另一种制度，未实现 |
| **宏观指标**       | 新闻分析师的宏观工具是 FRED，仅覆盖美国。中国宏观（PMI、社融、LPR）未接入；宏观**新闻**已回退到百度经济日报 |
| **港股财务报表**   | `.HK` 的行情、指标、新闻、情绪均可用；财务数据上游供应商不提供，会明确报出而非静默返回空 |
| **交易日历**       | 行情过期阈值已提高到 20 天，使春节、国庆不被误判为过期，但**尚无真实交易所日历** —— 日期窗口是普通日历算术，结算的持有窗口估计按西方节假日调校 |

**两个看起来像缺口但不是的**：

- **公司简介**：`agents/context.py` 仍从 Yahoo 读取标的身份（名称、板块、行业、交易所），**对中国代码有效** —— `600519.SS` 解析为「贵州茅台 / 日常消费」，`0700.HK` 为「腾讯控股」。
- **代码后缀**：见上文[代码格式](#a-股与港股支持)一节。

---

## 当前持仓

默认情况下智能体并不知道你持有什么仓，因此给出的建议是写给「读者自行套用到自己的仓位」的。传入持仓后，Trader、风控分析师和投资组合经理会针对你的真实账面工作：

```python
from quantagent.portfolio import PortfolioContext

portfolio = PortfolioContext.model_validate({
    "cash": 25000.0,
    "currency": "CNY",
    "positions": [{"ticker": "600519.SS", "quantity": 100, "average_price": 1680.0}],
})
_, decision = ta.propagate("600519.SS", "2026-09-01", portfolio=portfolio)
```

命令行同样支持，格式为 JSON 文件：`quantagent --portfolio my_book.json`

> `positions` 为空列表表示**空仓**，这与**不传持仓**是不同的事。不传持仓的运行永远不会被当作空仓处理 —— 那会凭空捏造一个关于你账户的事实。

---

## 持久化与恢复

### 决策日志

**始终开启。** 每次完成的运行会把决策追加到 `~/.quantagent/memory/trading_memory.md`。

同一标的的下一次运行，QuantAgent 会：先结算该标的已到期的历史决策 → 取得已实现收益率（绝对值 + 相对该标的所属地区基准的 alpha）→ 生成一段复盘反思 → 把同标的的历史决策与近期跨标的经验注入投资组合经理的提示词。**每一次分析都带着之前的教训往前走。**

历史回测运行会按分析日期做 **point-in-time 过滤**，只使用当日之前就已结算的教训，避免未来信息泄漏。路径可用 `QUANTAGENT_MEMORY_LOG_PATH` 覆盖。

### 断点续跑

默认关闭，通过 `--checkpoint` 开启。开启后 LangGraph 会在每个节点后保存状态，崩溃或中断的运行会**从最后一个成功步骤继续**，而不是从头再来。成功完成后 checkpoint 自动清除。

每个标的对应一个 SQLite 数据库，位于 `<cache_dir>/checkpoints/<代码>.db`。用 `--clear-checkpoints` 在运行前重置。

```bash
quantagent --checkpoint
quantagent --clear-checkpoints
```

checkpoint 的续跑标识包含「分析师选择 + 辩论轮数 + 风控轮数 + 资产类型 + 持仓指纹」。这些图结构参数改变后会**从新运行开始**，而不是静默接续上一次的状态。

---

## 回测

单次运行只给一个决策，无法说明系统决策质量如何。`run_backtest` 在「代码 × 日期」网格上运行同一套流程，写入独立决策日志，并结算那些持有窗口已经走完的决策。

```python
from quantagent.backtest import iter_grid, run_backtest, summarize

dates = iter_grid("2026-06-01", "2026-08-01", every_n_days=7)
result = run_backtest(["600519.SS", "000001.SZ"], dates, config,
                      selected_analysts=["market", "social"])
print(summarize(result).render())
```

命令行：

```bash
quantagent backtest 600519.SS,000001.SZ --start 2026-06-01 --end 2026-08-01 --every 7
```

每个格子按相对地区基准的实际 alpha 打分，并按评级分组统计。**你自己的决策日志永远不会被写入**；用 `run_id=result.run_id` 重跑同一网格会跳过已完成的格子，中断的扫描可以从断点继续。

> **回测评估的是决策质量，不是组合模拟器。** 把评级变成真实成交还需要数量、成交价和资金账本 —— 框架没有这些，也不会为此虚构一个执行模型。给定持仓时，它是**每个格子共用的同一份静态账本**，而不是逐格滚动的持仓。

---

## 可复现性

QuantAgent 由大模型驱动，因此**同一代码同一日期的两次运行可能不同**。这对于一个基于语言模型的研究工具是预期行为，不是缺陷。差异来自几个可区分的来源：

**模型采样本身不确定。** 即使固定温度，供应商也不保证多次调用输出逐字节一致。推理模型（默认的 GPT 系列及任何开启思考模式的模型）波动最大，因为其内部推理过程本身也是采样的。

**实时数据在变。** 新闻、StockTwits、Reddit 会随时间返回不同内容，因此今天的运行看到的是「现在」的舆情 —— 即使分析日期是历史日期。固定分析日期可以锁定价格与指标窗口，但社交与新闻源仍反映当下。

### 如何降低波动

降低采样温度（`temperature`，或 `.env` 中的 `QUANTAGENT_TEMPERATURE`）。**当前主流的推理模型基本忽略温度**，因此若需更强复现性，请在配置中指定**非推理模型**。

```python
config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "deepseek"
config["temperature"] = 0.0
config["deep_think_llm"] = "deepseek-chat"     # 非推理模型
config["quick_think_llm"] = "deepseek-chat"
```

### 哪些已经不会变了

- **标的身份在运行前由代码确定性地解析**，不会因为模型幻觉而认错公司。
- **市场分析师的所有精确价格与指标断言都锚定在验证快照上**。此前「不同运行变成不同公司」或「编造价位」的问题，由这两项机制解决。

> 回测结果不应被期待与任何已发表数字吻合。请把本框架当作**研究多智能体分析的脚手架**，而不是一个有固定可复现收益的策略。

---

## 开发

```bash
pip install -e ".[dev]"

pytest        # 1251 个用例
ruff check .
```

### 代码结构

```
quantagent/
├── agents/                     # 智能体定义
│   ├── analysts/               # market / news / fundamentals / sentiment
│   ├── researchers/            # 看多 / 看空辩论方
│   ├── risk_mgmt/              # aggressive / conservative / neutral
│   ├── managers/               # research manager / portfolio manager
│   ├── trader/                 # Trader
│   ├── tools.py                # 智能体工具（全部经由路由分发）
│   ├── context.py              # 标的身份与语言指令
│   ├── post_screen.py          # Jev 社交帖筛选
│   ├── rating.py               # 评级词表与解析
│   ├── structured.py           # 结构化输出 + 自由文本回退
│   ├── schemas.py              # Pydantic 输出模式与渲染
│   └── state.py                # 图状态定义
├── dataflows/
│   ├── router.py               # 供应商路由与降级链
│   ├── config.py               # 配置解析
│   ├── symbols.py              # 代码规范化
│   ├── date_window.py          # 日期窗口与过期判定
│   ├── errors.py               # 类型化异常
│   └── vendors/
│       ├── akshare/            # A股/港股：行情 / 财务 / 新闻 / 情绪
│       ├── yahoo/              # 全球行情与快照
│       ├── alpha_vantage/
│       ├── sec_edgar.py        # 美国 point-in-time 申报财报
│       ├── fred.py             # 宏观指标
│       ├── polymarket.py       # 预测市场
│       ├── reddit.py           # 社交情绪
│       ├── stocktwits.py       # 社交情绪
│       └── us_sentiment.py     # 美股情绪组合（StockTwits + Reddit）
├── graph/
│   ├── trading_graph.py        # 主图与对外入口
│   ├── setup.py                # 图构建
│   ├── propagation.py          # 状态初始化与流式运行
│   ├── analyst_execution.py    # 分析师注册表（key 的唯一真源）
│   ├── conditional_logic.py    # 条件边
│   ├── checkpointer.py         # 断点续跑
│   ├── reflection.py           # 复盘反思生成
│   └── settlement.py           # 历史决策结算
├── llm_clients/                # 多供应商客户端与注册表
├── backtest.py                 # 网格回测与评分
├── portfolio.py                # 持仓上下文
├── decision_log.py             # 决策日志
├── default_config.py           # DEFAULT_CONFIG 与环境变量覆盖
├── observability.py            # 用量与成本统计
├── reporting.py                # 报告树输出
└── web/                        # FastAPI + SSE 网页界面

cli/                            # Typer 终端界面
scripts/fix_ca_bundle.py        # Windows 非 ASCII 路径的证书修复
state/                          # 本地状态目录（缓存 / 日志 / 记忆）
tests/                          # 1251 个用例
```

### 关键约定

- **新增数据源**：在 `dataflows/vendors/` 下实现，在 `router.py` 的 `VENDOR_METHODS` 注册，在 `default_config.py` 的 `data_vendors` 加入类别。**不要在智能体里直接 import 某个数据源** —— 那会绕过降级链，并且非中国代码会收到本市场没有的数据。
- **抛出错误要用类型化异常**：`NoMarketDataError`（换下一个源）、`VendorRateLimitError`（源限流）、`VendorNotConfiguredError`（未配置）。抛出裸 `Exception` 会被路由记为「该源故障」，在链耗尽时可能顶替真实原因。
- **新增 LLM 供应商**：在 `llm_clients/api_key_env.py` 与 `openai_client.py` 的注册表里各加一行，CLI 的 Key 提问会自动跟上。
- **分析师 key 的唯一真源是 `graph/analyst_execution.py` 的 `ANALYST_NODE_SPECS`**，不要在别处复制这份列表。
- **环境变量覆盖的注册表是 `default_config.py` 的 `_ENV_OVERRIDES`**，新增可被 `.env` 覆盖的配置项时加一行即可，不需要改任何入口脚本。

---



**再次强调：QuantAgent 是研究工具，不构成投资建议。**
