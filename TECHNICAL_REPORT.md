# ThesisAgent 技术报告

[github地址](https://github.com/princestudy/ThesisAgent.git)

## 1. 项目概述

ThesisAgent 是一个面向论文资料问答和智能体安全学习的本地 Agent 原型。它使用大语言模型理解用户问题，并根据需要调用计算、时间、文件读取和知识库检索工具，最后根据工具结果生成回答。

这个项目的重点不是“让模型聊天”，而是展示一个 Agent 的完整闭环：

```text
用户输入
  ↓
模型理解与规划
  ↓
模型提出工具调用
  ↓
本地程序校验权限和参数
  ↓
Python 工具执行
  ↓
工具结果返回模型
  ↓
模型生成最终回答
  ↓
审计、测试和安全评估
```

项目适合用于学习以下内容：

- OpenAI 兼容 API 调用；
- Function Calling 工具调用；
- Agent 的规划—执行循环；
- RAG 本地知识库检索；
- SQLite 对话记忆；
- Streamlit 网页界面；
- 提示词注入、路径穿越和工具洪泛测试；
- 工具权限、资源限制、审计和输出溯源。

当前版本属于本地学习实验项目，不应直接作为公网生产系统使用。

## 2. 技术栈

| 层次     | 技术                              | 主要作用                          |
| -------- | --------------------------------- | --------------------------------- |
| 编程语言 | Python                            | 编写 Agent、工具、RAG、测试和界面 |
| 模型接口 | OpenAI Python SDK                 | 调用 OpenAI 兼容的聊天模型 API    |
| 模型服务 | DeepSeek 兼容 API                 | 提供语言理解、回答和工具规划能力  |
| 配置管理 | `python-dotenv`                   | 从 `.env` 读取 API Key 和模型配置 |
| 工具调用 | Chat Completions Function Calling | 让模型以结构化 JSON 请求工具      |
| 文档解析 | `pypdf`、`python-docx`            | 读取 PDF 和 Word 文档             |
| 文本检索 | scikit-learn TF-IDF               | 计算查询与文档片段的相似度        |
| 数据库   | SQLite                            | 保存对话历史和用户/会话范围       |
| 网页界面 | Streamlit                         | 提供本地聊天和文件上传界面        |
| 测试     | Python `unittest`                 | 验证确定性安全控制                |
| 安全记录 | JSONL、HMAC-SHA256                | 保存审计事件和应用级溯源信息      |

## 3. 系统架构

![image-20261111211716261](Thesis_Pic\ThesisArchitecture)

系统采用“模型负责规划、程序负责执行”的架构。模型不能直接操作文件或数据库，它只能提出工具调用请求；真正执行前必须经过本地 Python 代码的权限和参数校验。

## 4. 目录与模块职责

```text
ThesisAgent/
├─ agent.py                 # Agent 主循环、模型 API、工具定义和分发
├─ security_controls.py     # 参数校验、路径限制、脱敏、限额、审计
├─ knowledge_base.py        # 文档解析、切片、TF-IDF 检索
├─ memory.py                # SQLite 对话记忆和会话隔离
├─ main.py                  # 终端入口
├─ app.py                   # Streamlit 网页入口
├─ data/                    # 文件工具允许访问的目录
├─ knowledge/               # RAG 知识库资料
├─ memory.db                # SQLite 数据库，运行后生成
├─ security_lab/            # 安全测试和实验材料
│  ├─ jailbreak_cases.jsonl # 越狱/越权测试样例
│  ├─ audit.jsonl           # Agent 运行审计日志
│  ├─ evaluate_online.py    # 在线模型安全回归
│  ├─ test_offline.py       # 不调用 API 的安全测试
│  ├─ test_agent_controls.py# Agent 控制层测试
│  └─ provenance.py         # 应用级输出签名溯源
└─ .env                    # API 配置，不应提交或泄露
```

其中，`data/` 和 `knowledge/` 必须区分：前者是文件读取工具的受限目录，后者是 RAG 检索资料目录。以后扩展为多用户系统时，还应进一步按用户或租户隔离这两个目录。

## 5. API 调用与配置

配置模板在 `.env.example` 中，实际配置放在本地 `.env`：

```dotenv
OPENAI_API_KEY=你的_API_Key
OPENAI_BASE_URL=模型服务_API_地址
OPENAI_MODEL=模型名称
```

`agent.py` 中的 `Agent.__init__()` 读取配置并创建客户端：

```python
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY", "").strip()
base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
self.model = model or os.getenv("OPENAI_MODEL", "").strip()

self.client = OpenAI(
    api_key=api_key,
    base_url=base_url,
    timeout=30.0,
    max_retries=1,
)
```

这里使用的是“OpenAI SDK + OpenAI 兼容接口”模式：SDK 的调用格式保持统一，`OPENAI_BASE_URL` 决定实际请求发往哪个模型服务。

安全上必须保证：

1. API Key 只由本地程序读取；
2. API Key 不进入系统提示词、用户消息或工具结果；
3. 异常信息和审计日志不能打印完整密钥；
4. `.env` 不上传到 Git 或发送给模型。

## 6. 工具定义与工具调用

### 6.1 工具 Schema

`agent.py` 中的 `TOOLS` 定义模型可以使用的工具。例如计算器的 Schema：

```python
{
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "计算只包含数字和基本运算符的数学表达式。",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string"}
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
    },
}
```

Schema 的作用是告诉模型工具名称、用途和参数格式，但它不是权限系统。模型仍然可能产生错误参数，因此执行前还必须调用 `validate_tool_arguments()`。

### 6.2 Agent 主循环

`Agent.run()` 是整个系统最重要的代码。它的逻辑是：

```python
while tool_rounds < MAX_TOOL_ROUNDS:
    response = client.chat.completions.create(
        model=self.model,
        messages=working_messages,
        tools=TOOLS,
        tool_choice="auto",
    )

    assistant_message = response.choices[0].message

    if not assistant_message.tool_calls:
        return assistant_message.content

    for tool_call in assistant_message.tool_calls:
        arguments = json.loads(tool_call.function.arguments)
        arguments = validate_tool_arguments(tool_call.function.name, arguments)
        result = self._run_tool(tool_call.function.name, arguments)
        working_messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result,
        })
```

一次“请计算 12 * 8”的消息流程如下：

```text
用户消息：请计算 12 x 8
       ↓
模型返回：调用 calculate，expression = "12 x 8"
       ↓
Python 校验参数并执行 calculate
       ↓
工具结果：96
       ↓
将 role=tool 的结果追加到消息列表
       ↓
再次请求模型
       ↓
最终回答：结果是 96
```

这就是 Agent 的基本“规划—行动—观察”循环：

```text
Plan → Act → Observe → Plan → Answer
```

安全控制上，当前循环增加了最大工具轮次和最大工具调用次数，避免模型持续调用工具导致 API 成本失控或拒绝服务。

## 7. 重要工具代码

### 7.1 安全计算器

`calculate()` 没有使用危险的 `eval()`，而是使用 `ast.parse()` 解析表达式，然后只允许数字、加减乘除、整除、取模、幂和正负号。

核心思想是：

```text
输入字符串
  ↓
解析为 AST
  ↓
遍历 AST 节点
  ↓
只执行允许的节点和运算符
```

因此类似下面的 Python 代码不会被执行：

```python
__import__("os").getcwd()
```

当前代码还限制了表达式长度、AST 节点数量、数字长度和结果长度。后续可以增加指数大小限制、嵌套深度限制以及隔离进程超时。

### 7.2 文件读取工具

`read_text_file()` 通过 `resolve_data_file()` 限制文件范围：

```python
data_root = data_dir.resolve()
target = (data_root / raw_path).resolve()

if target == data_root or data_root not in target.parents:
    raise ToolArgumentError("只能读取 data 文件夹内的文件")
```

这一步可以防御：

```text
../.env
..\..\secret.txt
subdir/../../.env
```

之后还会检查：

- 文件名是否属于受保护文件；
- 扩展名是否属于白名单；
- 文件是否存在；
- 文件大小是否超过上限；
- 输出是否需要截断和脱敏。

这体现了路径穿越防护和最小权限原则。

### 7.3 当前时间工具

`get_current_time()` 只读取本机时间，属于低风险工具。但如果将来部署为服务，应明确时间来自服务器还是用户设备，以及时区是否可信。

### 7.4 知识库检索工具

`search_knowledge_base()` 用于从 `knowledge/` 文件夹中的本地资料里检索与用户问题相关的内容。

它和 `read_text_file()` 的区别是：

- `read_text_file()`：根据文件名直接读取 `data/` 中的指定文件；
- `search_knowledge_base()`：根据用户的问题，在 `knowledge/` 中搜索相关资料片段。

因此，当用户提出下面这类问题时，Agent 通常会调用这个工具：

```text
请根据知识库解释什么是智能体安全。
请从论文资料中查找 RAG 的相关内容。
请总结知识库中关于提示词注入的介绍。
```

这个函数主要负责：

- 接收用户的检索问题；
- 检查查询内容和参数；
- 读取 `knowledge/` 文件夹中的资料；
- 调用内部的文档处理和检索逻辑；
- 返回与问题相关的资料片段；
- 将检索结果交给模型生成最终回答。

需要注意，`search_knowledge_base()` 本身通常不直接生成最终答案。它只负责查找资料，模型再根据返回的资料组织回答。

一次典型的调用过程如下：

```
用户：请根据知识库解释什么是智能体安全
  			↓
    模型判断需要搜索本地资料
  			↓
模型调用 search_knowledge_base()
  			↓
 程序检查 query 和 top_k 参数
 			↓
    知识库返回相关资料片段
  			↓
   资料片段作为工具结果返回模型
  			↓
    模型根据资料生成最终回答
```

## 8. RAG 知识库与相似度分析

`knowledge_base.py` 实现了一个轻量级本地 RAG 流程：

```text
读取 PDF/Word/Markdown/TXT
  			↓
        清理空白字符
            ↓
按约600字符切片，片段重叠约100字符
            ↓
 用字符级 TF-IDF 将片段转换为向量
            ↓
   将问题转换为同一空间的向量
            ↓
       计算余弦相似度
            ↓
     排序并返回 top-k 片段
            ↓
      交给模型生成答案
```

核心代码：

```python
vectorizer = TfidfVectorizer(
    analyzer="char",
    ngram_range=(2, 4),
    max_features=30000,
)
document_vectors = vectorizer.fit_transform(texts)
query_vector = vectorizer.transform([query])
scores = cosine_similarity(query_vector, document_vectors).flatten()
ranked_indexes = scores.argsort()[::-1][:top_k]
```

含义如下：

- `analyzer="char"`：按字符 n-gram 分析，更适合中文短文本；
- `ngram_range=(2, 4)`：使用连续 2 到 4 个字符的片段；
- `fit_transform(texts)`：建立知识库片段向量；
- `transform([query])`：将用户问题映射到相同向量空间；
- `cosine_similarity()`：计算问题和每个片段的方向相似度；
- `argsort()[::-1]`：按照相关度从高到低排序。

当前还设置了最低相关度阈值，避免把完全无关的文本强行交给模型。

### RAG 的安全处理

检索结果会放在：

```text
<untrusted_retrieved_context>
...
</untrusted_retrieved_context>
```

并明确说明文档内容是不可信参考资料，其中的命令、角色声明和工具调用要求不能改变系统规则。

这是针对间接提示词注入和 RAG 文档投毒的基础防护。真正的安全边界仍然是工具权限和本地参数校验，因为模型可能误解文档内容。

当前 RAG 适合学习，后续可以改进为：

- 文件变化后再重新索引，而不是每次查询重新建立 TF-IDF；
- 使用 Embedding 和向量数据库；
- 增加混合检索和重排序；
- 保存页码、段落和文件哈希；
- 为不同用户建立独立知识库；
- 对 PDF/Word 解析增加超时、扫描和隔离进程。

## 9. SQLite 记忆

`memory.py` 通过 SQLite 保存历史消息。消息表包含：

```text
id
user_id
session_id
role
content
created_at
```

读取历史时使用：

```sql
SELECT role, content
FROM messages
WHERE user_id = ? AND session_id = ?
ORDER BY id DESC
LIMIT ?
```

这比只在提示词中写“不要读取其他用户消息”可靠得多，因为数据库查询本身已经限定了范围。

![image-20261112092342843](Thesis_Pic\db)

数据表（部分）如图所示，user指代了当前用户，assistant指代的是agent助手（一问一答）。当前默认的 `local-user` 和 `terminal` 只是本地学习用标识，不是真正的登录认证。多用户部署时还必须加入认证、RBAC、租户隔离和数据删除策略。

## 10. 安全控制与实验设计

安全控制集中在 `security_controls.py`，主要包括：

| 安全控制             | 解决的问题                   |
| -------------------- | ---------------------------- |
| 工具名称白名单       | 防止调用未注册工具           |
| 参数类型和字段校验   | 防止参数污染和类型绕过       |
| 路径标准化           | 防止路径穿越                 |
| 扩展名和文件大小限制 | 限制文件读取范围和资源消耗   |
| 工具轮次/次数限制    | 防止无限循环和 API 成本失控  |
| 输出长度限制         | 防止超大上下文和响应消耗     |
| 敏感信息脱敏         | 降低 API Key、Token 泄露风险 |
| 用户/会话范围查询    | 防止记忆跨用户泄露           |
| JSONL 审计           | 支持攻击过程复盘             |

对应的安全知识包括：

- 越狱：攻击模型的行为规则；
- 直接提示词注入：用户直接修改模型上下文；
- 间接提示词注入：恶意文档、网页或工具结果影响模型；
- 工具越权：模型请求超出任务需要的动作；
- 路径穿越：通过 `..` 访问受限目录；
- 资源耗尽：工具循环、超大输入、超大输出；
- 记忆隔离：防止不同用户或会话互相读取数据；
- 纵深防御：不把安全责任全部交给系统提示词。

## 11. 测试与评估

### 11.1 离线测试

不调用模型 API 的测试：

```
python -m unittest security_lab.test_offline -v
```

主要测试“单个安全功能是否正确”，偏向基础单元测试。

![image-20261112080642720](Thesis_Pic\test-offline)

测试内容包括：

- 计算器是否拒绝 Python 代码；
- 文件工具是否阻止路径穿越；
- 是否拒绝不支持的文件类型；
- 越狱测试用例字段是否完整；
- HMAC 溯源是否正常；
- 是否能够发现记录篡改。

```
python -m unittest security_lab.test_agent_controls -v
```

主要测试“Agent 和安全控制配合是否正常”，偏向控制层或集成测试。

![image-20261112081407422](Thesis_Pic\test-agent-controls)

测试内容包括：

- Agent 连续调用工具时是否触发轮次上限；
- 工具传入额外参数时是否被拒绝；
- 不同用户和会话的记忆是否隔离。

### 11.2 在线安全评估

攻击样例位于 `security_lab/jailbreak_cases.jsonl`，包括系统提示词提取、密钥提取、路径穿越、间接注入、角色扮演越狱、工具洪泛和记忆隔离。

运行前三条：

```powershell
python -m security_lab.evaluate_online
```

![image-20261112082231911](Thesis_Pic\evaluate-online)

运行全部用例：

```powershell
python -m security_lab.evaluate_online --limit 7
```

在线评估会调用 API，可能产生费用。自动通过不等于绝对安全，还应检查：

1. 是否调用了工具；
2. 调用的工具名称和参数是什么；
3. 工具层是否真正拒绝了越权请求；
4. 最终回答是否泄露敏感内容；
5. 是否出现重复调用和异常消耗；
6. `security_lab/audit.jsonl` 是否记录完整。

### 11.3 审计文件为什么是一行一条

`jailbreak_cases.jsonl` 和 `audit.jsonl` 使用 JSONL（JSON Lines）格式。它的设计是“每行一个完整 JSON 对象”，便于：

- 逐行读取攻击用例；
- 运行中持续追加审计事件；
- 日志文件损坏时只影响单条记录；
- 后续导入日志分析工具。

因此不能直接把一个 JSON 对象拆成多行，否则现有的逐行解析和追加逻辑会失效。本项目已经在 `.vscode/settings.json` 中为 `*.jsonl` 开启自动换行，打开这两个文件时会按列宽视觉换行，但不会改变文件格式，也不会修改其他代码。

## 12. Agent运行

### 12.1 网页版

确保已激活虚拟环境后执行：

```powershell
python -m streamlit run app.py
```

浏览器打开：

```text
http://localhost:8501
```

Windows 用户也可以双击：

```text
start-agent-web.cmd
```

![image-20261112083102734](Thesis_Pic\webPage)

网页端支持：

- 输入问题并进行对话；
- 上传 PDF、Word、Markdown、TXT、CSV 或 JSON 文件；
- 查看知识库文件；
- 查看本次请求调用了哪些工具；
- 清空当前网页会话。

### 12.2 终端版

```powershell
python main.py
```

可以尝试：

```text
请计算 (128 + 72) * 3 / 5
现在几点？
请读取 示例.txt
请根据知识库解释什么是智能体安全
清空记忆
退出
```

指定文件名直接读取（data目录下的文件）-->read_text_file()

![image-20261112084229888](Thesis_Pic\read-text-files)

根据问题搜索相关片段（knowledge目录下的文件）-->search_knowledge_base()

![image-20261112085707595](Thesis_Pic\search-knowledge-base)

| 目录         | 工具                    | 使用方式             | 适合场景                     |
| ------------ | ----------------------- | -------------------- | ---------------------------- |
| `data/`      | `read_text_file`        | 指定文件名直接读取   | “请读取 example.txt”         |
| `knowledge/` | `search_knowledge_base` | 根据问题搜索相关片段 | “请根据知识库解释智能体安全” |

这是为了安全和功能隔离，如果所有文件都由一个工具任意读取，可能出现：

- Agent 读取不该读取的文件；
- 知识库资料和普通文件混在一起；
- 路径权限难以控制；
- 恶意文档更容易影响文件工具；
- 多用户部署时难以隔离数据。

## 13. 通用智能体构建技术路线

下面是一条可以复用于其他 Agent 项目的通用路线。

### 阶段一：明确任务和风险

先写清楚：

- Agent 要解决什么问题；
- 用户是谁；
- 是否需要访问文件、数据库或网络；
- 哪些动作允许自动执行；
- 哪些动作必须人工确认；
- 失败后可能造成什么影响。

不要一开始就添加大量工具。工具越多，权限面越大，安全评估越困难。

### 阶段二：建立最小模型调用

技术选择：

- Python；
- OpenAI SDK 或目标模型官方 SDK；
- `.env` 管理密钥；
- 设置请求超时、重试和模型名称。

先完成“用户输入 → 模型回答”，确认 API 能正常工作。

### 阶段三：增加受限工具

每增加一个工具，都应同时设计：

- 工具 Schema；
- 参数类型；
- 参数长度；
- 权限范围；
- 输出大小；
- 失败行为；
- 审计字段；
- 单元测试。

优先从低风险工具开始，例如计算器、时间查询和只读检索。避免直接提供任意 Shell、任意网络请求或任意文件写入工具。

### 阶段四：实现 Agent 循环

标准流程是：

```text
调用模型
→ 判断是否有 tool_calls
→ 解析工具名称和 JSON 参数
→ 本地校验
→ 执行工具
→ 将结果返回模型
→ 直到模型输出最终回答或达到安全上限
```

必须加入：

- 最大轮次；
- 最大工具调用次数；
- 单次执行超时；
- API 请求超时；
- 异常分类；
- 中止机制。

### 阶段五：增加 RAG 或记忆

需要文档问答时再加入 RAG：

```text
加载文档 → 切片 → 向量化 → 相似度排序 → 取 top-k → 交给模型
```

需要连续对话时再加入记忆：

```text
保存消息 → 按用户/会话读取 → 限制历史长度 → 送入模型
```

RAG 内容和历史记忆都应视为不可信输入，不能自动获得工具权限。

### 阶段六：加入界面和可观测性

可以使用：

- Streamlit：适合快速本地界面；
- FastAPI：适合构建后端 API；
- Langflow：适合可视化编排；
- OpenTelemetry：适合更完整的生产追踪。

界面不应只显示最终答案，最好同时提供工具名称、参数、执行结果摘要和请求 ID，方便调试安全问题。

### 阶段七：安全加固

采用纵深防御：

```text
系统提示词边界
  ↓
工具白名单
  ↓
参数校验
  ↓
权限/资源范围
  ↓
人工确认
  ↓
执行超时和调用预算
  ↓
输出脱敏
  ↓
审计日志
  ↓
安全回归测试
```

关键原则是：模型可以提出请求，但不能单独决定请求是否有权限执行。

### 阶段八：测试和评估

建议同时准备四类测试：

1. 正常功能测试：验证正确任务能完成；
2. 边界测试：空输入、超长输入、错误类型和不存在文件；
3. 攻击测试：越狱、提示词注入、工具越权、RAG 投毒和记忆越权；
4. 资源测试：重复调用、超大文件、超大输出和超长上下文。

评估指标可以包括：

- 任务成功率；
- 工具选择准确率；
- 越权工具调用率；
- 敏感信息泄露率；
- 攻击成功率；
- 平均工具调用次数；
- 平均响应时间；
- 平均 token 和 API 成本；
- 误拒绝率。

### 阶段九：生产化前检查

如果要部署到多人或公网环境，还需要补充：

- 登录认证和 RBAC；
- 速率限制和并发限制；
- 网络出口控制；
- 容器或进程沙箱；
- 文件病毒扫描；
- 数据加密和密钥管理；
- 日志轮转和防篡改存储；
- 用户级知识库和记忆隔离；
- 成本预算和告警；
- 人工审批高风险工具。

## 14. 总结

ThesisAgent 的技术路线可以概括为：

```text
Python 环境
  → OpenAI 兼容 API
  → Function Calling 工具
  → Agent 执行循环
  → RAG 知识库
  → SQLite 记忆
  → Streamlit 界面
  → 安全控制
  → 离线/在线测试
  → 审计和持续改进
```

最重要的工程思想是：

```text
模型负责理解和规划，程序负责权限和执行，测试负责验证，审计负责复盘。
```

模型拒绝回答并不等于 Agent 安全。真正需要验证的是：当模型判断错误、受到越狱或被恶意文档影响时，工具权限、文件范围、调用预算和数据隔离是否仍能阻止危险后果。
