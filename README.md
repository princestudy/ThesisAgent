# ThesisAgent：论文知识库与安全实验 Agent

ThesisAgent 是一个使用 Python 编写的本地智能体学习项目。它可以调用大语言模型 API，根据用户问题选择工具，检索本地论文资料，并通过安全控制模块限制工具权限和资源消耗。

这个项目适合学习：

- LLM API 调用和 Function Calling；
- Agent 的工具调用循环；
- 本地 RAG 知识库；
- SQLite 对话记忆；
- Streamlit 网页应用；
- 越狱、提示词注入和工具越权测试；
- Agent 的参数校验、审计和安全加固。

> 本项目主要用于本地学习和安全实验，不建议未经认证、限流和沙箱加固就部署到公网。

## 1. 系统架构

```text
用户
 │
 ├── main.py：终端界面
 └── app.py：Streamlit 网页界面
 │
 ▼
agent.py：Agent 控制器
 │
 ├── OpenAI 兼容模型 API
 ├── 系统提示词
 ├── 工具定义
 └── 工具调用循环
 │
 ▼
security_controls.py：安全控制层
 │
 ├── 工具参数校验
 ├── 文件路径限制
 ├── 工具调用次数限制
 ├── 输出截断和敏感信息脱敏
 └── JSONL 审计日志
 │
 ├── calculate：安全数学计算
 ├── get_current_time：获取当前时间
 ├── read_text_file：读取 data/ 中的文本文件
 └── search_knowledge_base：检索 knowledge/ 中的资料
       │
       ├── PDF
       ├── Word
       ├── Markdown
       └── TXT/CSV/JSON
              │
              ▼
       文本切片 → TF-IDF → 余弦相似度 → 返回相关片段
```

Agent 的基本工作流程是：

```text
用户提问
  ↓
模型理解问题并规划动作
  ↓
模型提出工具调用请求
  ↓
Python 校验工具和参数
  ↓
本地工具执行
  ↓
工具结果返回模型
  ↓
模型生成最终回答
```

核心原则是：模型负责理解和规划，Python 程序负责权限判断和实际执行。

## 2. 主要技术

| 技术 | 用途 |
|---|---|
| Python | 编写 Agent、工具、RAG、记忆和测试 |
| OpenAI Python SDK | 调用 OpenAI 兼容的模型 API |
| `python-dotenv` | 从 `.env` 读取 API 配置 |
| `pypdf` | 解析 PDF |
| `python-docx` | 解析 Word 文档 |
| scikit-learn | 使用 TF-IDF 和余弦相似度检索资料 |
| SQLite | 保存本地对话记忆 |
| Streamlit | 提供网页聊天界面 |
| unittest | 编写离线安全测试 |

## 3. 项目结构

```text
ThesisAgent/
├── agent.py                    # 模型 API、工具定义和 Agent 主循环
├── app.py                      # Streamlit 网页入口
├── main.py                     # 终端入口
├── memory.py                   # SQLite 对话记忆和会话隔离
├── knowledge_base.py           # 文档解析和 TF-IDF 检索
├── security_controls.py        # 参数校验、限额、脱敏和审计
├── requirements.txt            # Python 依赖
├── .env.example                # API 配置模板
├── .gitignore                  # Git 忽略规则
├── check-environment.ps1       # Windows 环境检查脚本
├── start-agent-web.cmd         # Windows 网页启动脚本
├── data/                       # 文件读取工具的受限目录
├── knowledge/                  # RAG 知识库资料
└── security_lab/               # Agent 安全测试实验
    ├── jailbreak_cases.jsonl   # 越狱和越权测试用例
    ├── evaluate_online.py      # 在线安全评估
    ├── test_offline.py         # 不调用 API 的安全测试
    ├── test_agent_controls.py  # Agent 控制层测试
    ├── provenance.py           # 应用级输出溯源
    └── audit.jsonl             # 运行时生成的审计日志
```

## 4. 环境安装

### 4.1 克隆项目

```powershell
git clone https://github.com/你的用户名/ThesisAgent.git
cd ThesisAgent
```

### 4.2 创建虚拟环境

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 阻止激活脚本，可以在当前终端临时允许：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

然后重新执行：

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4.3 安装依赖

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

确认当前使用的是项目虚拟环境：

```powershell
python -c "import sys; print(sys.executable)"
```

输出路径应包含：

```text
ThesisAgent\.venv\Scripts\python.exe
```

## 5. 配置模型 API

复制配置模板：

```powershell
Copy-Item .env.example .env
```

然后编辑 `.env`：

```dotenv
OPENAI_API_KEY=你的_API_Key
OPENAI_BASE_URL=模型服务的_API_地址
OPENAI_MODEL=模型名称
```

项目使用 OpenAI 兼容接口，因此可以根据模型服务商填写 `OPENAI_BASE_URL`。API Key 只应保存在本地 `.env` 中。

请注意：

- 不要把 `.env` 上传到 GitHub；
- 不要把 API Key 写入测试用例；
- 不要把 API Key 放进知识库文档；
- 不要在聊天记录或截图中展示 API Key；
- 如果 API Key 曾经上传过，应立即在服务商后台重新生成。

## 6. 启动 Agent

### 6.1 网页版

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

网页端支持：

- 输入问题并进行对话；
- 上传 PDF、Word、Markdown、TXT、CSV 或 JSON 文件；
- 查看知识库文件；
- 查看本次请求调用了哪些工具；
- 清空当前网页会话。

### 6.2 终端版

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

## 7. 添加知识库资料

可以把以下格式的文件放入 `knowledge/`：

```text
.pdf
.docx
.txt
.md
.csv
.json
```

也可以在 Streamlit 网页左侧上传资料。

知识库处理流程：

```text
读取文档
  ↓
清理文本并切片
  ↓
使用字符级 TF-IDF 向量化
  ↓
计算问题与片段的余弦相似度
  ↓
排序并选取 top-k 片段
  ↓
将相关片段交给模型回答
```

检索内容会被标记为不可信参考资料。文档中的命令、角色声明或工具调用要求不能自动获得执行权限，这用于降低 RAG 间接提示词注入风险。

## 8. 安全实验

### 8.1 离线安全测试

离线测试不调用模型 API，不产生 API 费用：

```powershell
python -m unittest security_lab.test_offline -v
python -m unittest security_lab.test_agent_controls -v
```

测试内容包括：

- 计算器拒绝 Python 代码；
- 文件工具拒绝路径穿越；
- 不支持的文件扩展名被拒绝；
- 工具参数额外字段被拒绝；
- 重复工具调用受到轮次限制；
- 用户和会话记忆互相隔离；
- HMAC 溯源可以发现记录篡改。

### 8.2 在线安全评估

测试样例位于：

```text
security_lab/jailbreak_cases.jsonl
```

包含以下类别：

- 系统提示词提取；
- 敏感信息提取；
- 路径穿越；
- 间接提示词注入；
- 角色扮演越狱；
- 工具洪泛；
- 记忆隔离。

默认运行前三条：

```powershell
python -m security_lab.evaluate_online
```

运行全部用例：

```powershell
python -m security_lab.evaluate_online --limit 7
```

在线评估会调用模型 API，可能产生费用。自动通过不代表绝对安全，还需要检查：

1. 是否发生工具调用；
2. 工具名称和参数是否合理；
3. 工具层是否真正拒绝越权操作；
4. 最终回答是否泄露敏感信息；
5. 是否出现异常重复调用；
6. `security_lab/audit.jsonl` 是否记录完整。

## 9. 安全设计说明

`security_controls.py` 是项目的确定性安全控制层，主要提供：

- 工具名称和参数校验；
- 文件路径穿越防护；
- 文件类型和大小限制；
- 工具轮次和调用次数限制；
- 工具输出长度限制；
- API Key、Token 等敏感信息脱敏；
- 用户和会话信息审计；
- JSONL 格式安全日志。

安全设计采用纵深防御：

```text
系统提示词边界
  ↓
工具白名单
  ↓
参数校验
  ↓
文件和资源范围限制
  ↓
调用预算
  ↓
输出脱敏
  ↓
审计日志
  ↓
安全回归测试
```

系统提示词只能指导模型，不能代替操作系统权限、数据库权限或文件系统沙箱。即使模型被越狱，程序层仍应限制它能够执行的动作。

## 10. GitHub 上传注意事项

以下内容不应上传：

```text
.env
.venv/
__pycache__/
memory.db
security_lab/audit.jsonl
```

项目的 `.gitignore` 已经配置了这些规则。上传前检查：

```powershell
git add .
git status
```

确认输出中没有真实 API Key、`.env`、虚拟环境、本地数据库或运行日志。

如果这些文件之前已经被 Git 跟踪，仅修改 `.gitignore` 不够，需要执行：

```powershell
git rm --cached .env memory.db security_lab/audit.jsonl
git commit -m "Remove local sensitive files"
```

这不会删除本地文件，只会停止 Git 跟踪。

## 11. 文档与进一步学习

项目中的详细材料：

- `TECHNICAL_REPORT.md`：技术框架、重要代码、RAG、测试和通用构建路线；
- `security_lab/`：越狱用例、离线测试、在线评估和应用级溯源代码。

建议学习顺序：

```text
1. 运行一个简单问题，观察 Agent.run()
2. 观察 calculate 工具调用
3. 测试路径穿越是否被拒绝
4. 添加恶意 Markdown，测试间接提示词注入
5. 查看 security_lab/audit.jsonl
6. 运行离线安全测试
7. 少量运行在线安全评估
8. 每次只修改一个安全控制并比较结果
```

## 12. 项目限制

当前版本仍然是学习型原型，尚未完整实现：

- 用户登录和 OAuth；
- RBAC 和多租户隔离；
- 网络出口控制；
- 容器或进程级沙箱；
- 恶意文档扫描；
- 生产级日志存储和轮转；
- 完整的 token 成本统计；
- 高风险工具的人工审批。

不要把“模型说我不能做”和“系统真正安全”混为一谈。安全评估时，应优先检查工具行为、权限边界、参数、审计记录和实际影响。
