# INTERVIEW.md · jobpulse 读懂指南（面试版）

按「教学顺序」一个文件一个文件读：先读领域模型，再读接口（ports），再读适配器，最后读流水线、AWS 和测试。
每个文件分三层：**(a) 骨架**：它是干什么的、核心数据流；**(b) 为什么**：设计取舍，面试最常追问的就是这一层；**(c) 语法**：读代码时会卡住的 Python/TS/CDK 写法。
力气主要花在前两层，语法看懂就够了。

> 所有数字都来自真实运行，出处见 README 的 Key metrics 表。面试时只说那些数字，别自己估。
>
> **背下来的 6 个数**：
> - 抽取 F1 0.946±0.004（关键词基线 0.896，30 份盲标 JD，跑 3 次）；
> - 真实性属性测试 2,000 个变体 0 违规；
> - 真实 demo 生成 91 份简历，91 份全部通过校验；
> - 30 天回填共 30 次运行全部成功：256 个岗位，其中 127 个有完整 JD；
> - 覆盖率从 40.1% 提到 44.2%（提升幅度不大，要如实说）；
> - 开发期总共 312 次 LLM 调用，预算 1,200 次。

---

## 第一部分：核心领域（先建立词汇表）

### 1. `src/jobpulse/models.py`：领域模型
**(a) 骨架**
- `Posting`：一个岗位，含 JD 全文、首次看到的日期 `first_seen` 和规则抽出的硬约束 `Constraints`。
- `Extraction`：LLM（或关键词）从 JD 抽出的结构化要求：`required_skills` / `nice_to_have` / `domain` / `method`。
- `TrendSignal`、`ProjectIdea`、`RunRecord`：趋势信号、项目点子、每天一条的运行记录。
- `content_hash(text)`：把 JD 规整（合并空白、转小写）后取 sha256，当 LLM 缓存的 key。

**(b) 为什么**
- 全部用 `frozen=True` 的 dataclass：不可变，可以安全地在线程间共享（extract 步骤用了线程池），也不会被意外改掉。
- 每个模型都手写 `to_dict` / `from_dict`，不用 ORM。存储层只认 JSON 文档，所以 SQLite 和 DynamoDB 可以共用同一套模型。
- 缓存 key 用「内容哈希」而不是岗位 id：同一份 JD 被不同公司或不同链接重复发布时，可以直接复用抽取结果；JD 内容一改，哈希就变，自然失效。

**(c) 语法**
- `from __future__ import annotations`：类型注解延迟求值，所以可以在类里写 `-> Posting`（引用类自己）。
- `field(default_factory=Constraints)`：可变或复杂对象的默认值必须用工厂函数，不能直接写 `= Constraints()`。
- `tuple[str, ...]` 表示不定长元组。用元组而不用 list，是为了配合 frozen，保证真正不可变。

### 2. `src/jobpulse/taxonomy.py` + `data/taxonomy.toml`：技能词表
**(a) 骨架**
- TOML 里每个技能一张表：`aliases` 不区分大小写（例如 `k8s` → Kubernetes），`exact` 区分大小写（例如 `Go`、`R`、`C`）。
- `canonical(name)`：把 LLM 吐出来的任意写法映射到标准名，映射不了就返回 None。
- `find(text)`：在一段文本里找出所有提到的技能，也就是关键词抽取。

**(b) 为什么**
- 词表是整个系统的「共同语言」：LLM 输出要归一到它，需求统计按它计数，趋势映射到它，简历要点也按它匹配。没有统一词表，"k8s" 和 "Kubernetes" 会被算成两个技能。
- 短词误报是真实踩过的坑：`Spring 2027` 被算成 Spring Boot，`networking events` 被算成计算机网络，`rest of the team` 被算成 REST。解决办法有两条：
  1. 删掉歧义别名；
  2. 像 `Go` 这样的大小写敏感短词，必须出现在列表语境里（前后有 `,` `/` `(` `and` `or`）才算数。
  `tests/test_taxonomy.py::test_ambiguous_words_do_not_false_match` 锁住了这个行为。
- 别名按长度倒序拼进一个大正则，这样长的优先匹配："React Native" 不会被拆成 React。

**(c) 语法**
- `(?<![\w+#.])` 和 `(?![\w+#]|\.\w)` 是负向后顾和负向前瞻，自己定义「词边界」。`\b` 处理不了 `C++`、`.NET`、`C#` 这类带符号的技能名。
- `@lru_cache(maxsize=1)`：词表只加载一次，相当于进程内单例。
- `importlib.resources.files("jobpulse.data")`：从安装好的包里读数据文件。打进 Lambda 包以后照样能用，不依赖当前工作目录。

### 3. `src/jobpulse/ports.py`：端口（接口）
**(a) 骨架**：四个 `Protocol`：`DocStore`（pk/sk 文档库）、`BlobStore`（文件）、`LLMProvider`、`HttpClient`。业务代码只依赖这四个接口。
**(b) 为什么**
- 这是端口与适配器（六边形架构）。流水线代码里没有 `import boto3`，也没有 `import sqlite3`，所以本地和 Lambda 跑的是同一份业务逻辑，测试时把这四个接口换成假的实现就行。
- `DocStore` 故意做得很窄（put / put_if_absent / get / query / delete），因为它和 DynamoDB 单表设计一一对应：SQLite 用一张 `(pk, sk)` 复合主键的表就能实现。这样 repository 只写一遍、测一遍。

**(c) 语法**：`typing.Protocol` 是结构化子类型（鸭子类型）：一个类不用显式继承，只要方法签名对得上，mypy 就认它是这个接口的实现。

### 4. `src/jobpulse/repositories.py`：仓储（单表 key 设计）
**(a) 骨架**：文件顶部那张表就是 key 设计：
- `POSTING#<id>/META`：岗位本体；
- `DAY#<date>/POSTING#<id>`：「某天新看到的岗位」索引；
- `EXTRACT#<hash>/<version>`：LLM 抽取缓存；
- `DEMAND/<date>`：每日需求时间序列；
- `RUN/<date>`：运行记录。

**(b) 为什么**
- **幂等靠构造**：所有写入的 key 要么是日期，要么是内容哈希，所以重跑同一天只会覆盖同一批 item，不会产生重复。
- `record_posting` 的三种情况：
  - 第一次见：用条件写入 `put_if_absent`，同时写一条当天的索引；
  - 同一天重跑：覆盖原来的 item；
  - 以前某天见过：返回 False，不算今天的新岗位。
- 缓存 key 里带 `provider:prompt_version`：一旦改了 prompt，旧缓存自动不命中，不会拿旧 prompt 的结果冒充新的。

**(c) 语法**：f-string 拼 key。`dict(self.store.query("DEMAND"))` 把 `(sk, doc)` 列表直接转成 `{日期: 文档}` 字典。

### 5. `src/jobpulse/adapters/local.py`：本地适配器
**(a) 骨架**：`SqliteDocStore`（一张表）、`FileBlobStore`（写到 `./out/site`）、`MemoryDocStore`（单元测试用）。
**(b) 为什么**：`put_if_absent` 用 `INSERT OR IGNORE` 再看 `rowcount`，语义和 DynamoDB 的 `attribute_not_exists` 条件写一致。`FileBlobStore._path` 会检查 key 有没有越出根目录，防止路径穿越。
**(c) 语法**：`substr(sk, 1, ?) = ?` 实现前缀查询，对应 DynamoDB 的 `begins_with`。

### 6. `src/jobpulse/adapters/http.py`：礼貌爬取
**(a) 骨架**：`UrllibHttpClient` 依次做四件事：
1. 用通用 User-Agent（不带任何个人信息）；
2. 先查 robots.txt，被禁止就抛 `HttpError`；
3. 按 host 限速，取「我们自己的最小间隔」和 robots 里 `Crawl-delay` 中较大的那个（Lever 写的是 1 秒）；
4. 遇到 429 / 5xx 线性退避重试。

另外还有 `FakeHttpClient`（测试用）和 `MemoizingHttpClient`（backfill 时 13 MB 的 listings 只下一次）。

**(b) 为什么**
- 合规和伦理：只用公开 API，遵守 robots。代价是 Workday 的部分租户和 SmartRecruiters 的 API 在 robots 里禁止爬虫，这些 JD 就拿不到，而且我们如实计数，不偷偷绕过。
- 只用 stdlib 的 urllib：Lambda 包更小，冷启动更快。

**(c) 语法**：`urllib.robotparser.RobotFileParser`，`can_fetch(UA, url)` 判断能不能抓，`crawl_delay(UA)` 读延迟。`time.monotonic()` 做间隔计时，不受系统改时间的影响。

### 7. `src/jobpulse/adapters/llm.py`：LLM 提供方
**(a) 骨架**
- `QwenProvider`：用 urllib 直接 POST DashScope 的 OpenAI 兼容接口，请求里带 `response_format=json_object` 和 `enable_thinking=False`。
- `CallLedger`：每次真实调用往 JSONL 里追加一行（用途、耗时、token 数，**不记 key，也不记 prompt**），到 1,150 次直接抛 `BudgetExceeded` 硬停。
- `FakeProvider`：确定性的假实现，给测试和 CI 用。
- `CountingProvider`：包一层，统计这一步调了多少次。

**(b) 为什么**
- 开发期预算上限是 1,200 次，所以在代码里设硬上限，而不是靠自觉。
- 防 key 泄漏有三处：`__repr__` 里不打印 key；异常用 `from None` 切断异常链（原始异常里带着 request 对象，request 里有 key）；ledger 不记 prompt。
- 有了 fake provider，CI 里不需要任何密钥。

**(c) 语法**：`raise ... from None` 会去掉 `__cause__` 和 `__context__`。`threading.Lock` 保证计数在多线程下正确。`try/except/finally`：不管成功失败，都在 finally 里写 ledger。

### 8. `src/jobpulse/context.py`：依赖装配
**(a) 骨架**：`Context` 把 repos、site、llm、http、taxonomy、bank、log、settings 打包成一个对象，显式传进每一步。`local_context()` 负责本地装配，AWS 端由 `lambdas.aws_context()` 装配。
**(b) 为什么**：显式依赖注入，不用全局变量，所以测试时换掉其中任何一个依赖都很容易。真实简历的 bullet bank 路径和 env 文件路径写在被 gitignore 的 `jobpulse.local.toml` 里，公开仓库里只有 demo 人设。
**(c) 语法**：`@dataclass` 里给 `settings` 用 `field(default_factory=Settings)` 作默认值。

---

## 第二部分：流水线六步

### 9. `src/jobpulse/sources/simplify.py`、`src/jobpulse/sources/ats.py`：数据源
**(a) 骨架**
- `parse_listings`：过滤条件依次是 active + visible、Summer 2027、SWE 类别（AI/ML 类别要求标题带 ML/AI/工程关键词）、美国地点、发布日期在 `[D-lookback, D]` 之内。
- `dedupe`：同公司 + 同标题视为同一个岗位（多地点重复发布）。
- `ats.py`：从 jd-resume-tailor 改写而来（MIT，README 里有署名），能识别 Greenhouse、Lever、Ashby、Workday、Oracle、SmartRecruiters 的 URL，并调用对应的公开 JSON 接口。

**(b) 为什么**：大多数招聘页是 JS 渲染的，直接爬 HTML 拿不到内容；但主流 ATS 都有公开的 JSON 接口。删掉了原脚本里的「通用 HTML 兜底」和「跟随重定向」，只走结构化 API，更可控，也更礼貌。
**(c) 语法**：`FETCHERS` 是函数列表，按顺序尝试，第一个返回非 None 的生效（责任链模式）。`str.removesuffix("/apply")` 是 Python 3.9+ 的写法。

### 10. `src/jobpulse/pipeline/constraints.py`：硬约束规则
**(a) 骨架**：用正则识别五类约束：学位层次、只招 PhD、毕业时间窗、不提供 sponsorship、要求公民身份 / ITAR、安全许可（clearance）。
**(b) 为什么**
- 用规则而不用 LLM：这些判断出错的代价是错过一次投递，规则可审计、可单测、结果确定。
- 规则是对着真实 JD 审计修过的。三个误报（测试里都有原句）：
  1. "PhD **or MS** program" 被判成只招 PhD；
  2. "US Citizenship Required: **No**" 被判成要求公民身份；
  3. "U.S. person **or non-U.S. person**" 被判成要求美国人身份。

**(c) 语法**：负向前瞻 `(?!\s*(or|/|,)\s*(ms|master...))` 排除「PhD or MS」这种情况。`re.I` 表示忽略大小写。

### 11. `src/jobpulse/pipeline/ingest.py`：第 1 步
**(a) 骨架**：拉 listings → 过滤、去重 → 跳过以前某天见过的 → 按「最新优先」取前 N 条（每天的上限 `max_postings_per_run`）→ 逐条抓 JD、抽约束 → 入库。
**(b) 为什么**
- **局部失败**：单条 JD 抓取失败（HttpError，或者某家 ATS 返回了奇怪的 JSON）只计数、打日志，然后继续下一条，不影响整次运行。
- 重跑同一天时，已有 JD 的岗位直接复用，不重新抓：既保证幂等，也对数据源更礼貌。
- 每天有上限，所以 LLM 调用次数和 Lambda 时长都是有界的。

**(c) 语法**：海象运算符 `if (existing := ctx.repos.posting(id)) is not None`，在条件里同时赋值。

### 12. `src/jobpulse/pipeline/extract.py`：第 2 步（LLM 抽取）
**(a) 骨架**：先查缓存（key 是 JD 哈希 + 版本），命中就复用；没命中就调 LLM → `parse_llm_json`（能容忍 ```json 包裹和前置废话）→ `validate_extraction`（校验 schema，再把技能归一到词表）。失败就把错误信息塞回 prompt 再试一次；两次都失败就退回 `keyword_extract`。
**(b) 为什么**
- **schema 校验**：LLM 输出是不可信输入，所以字段和类型要逐个检查，词表里映射不上的技能单独放进 `unmapped_skills`，不计入需求统计。
- **带错误信息的重试**：比盲目重试的成功率高。
- **兜底**：LLM 挂了，流水线也照样产出结果，只是 `method` 字段会诚实地标成 "keyword"。
- **并发**：LLM 调用放进 4 线程的线程池（I/O 密集型）；所有存储写入留在主线程，避免 SQLite 跨线程的问题。
- prompt 里把词表的标准名列给模型，要求它「只写 JD 里真的出现过的技能」，减少臆造。

**(c) 语法**：`ThreadPoolExecutor.map` 保持输入顺序返回结果。`except (LLMError, SchemaError)` 一次捕获多种异常，`BudgetExceeded` 是 `LLMError` 的子类，也会被捕获。

### 13. `src/jobpulse/evaluation.py` + `eval/`：抽取质量评测
**(a) 骨架**
- 选题：30 份真实 JD，按岗位 id 的 sha256 排序后取前 30 条（确定性），每份 JD 至少 800 字符。
- 标注：由一个**独立的 Claude agent 盲标**，标注时还没有任何系统输出，共 304 个技能标签。
- 指标：micro precision / recall / F1。LLM 跑 3 次，每次都不走缓存，用来测方差；关键词基线是确定性的，跑一次就够。

**(b) 为什么**
- 盲标是为了避免「对着答案出题」。
- 不走缓存，方差才是真实的方差。
- 公开预测文件（`eval/predictions/`），任何人都能复算。
- **局限**：标注员用的是同一套词表，这对关键词方法的召回有利。所以关键词召回（0.954）比 LLM 高，而 LLM 赢在精确率（0.961 对 0.846）和「必需 / 加分」分档（required-only F1 0.904 对 0.810）。

**(c) 语法**：`statistics.stdev` 是样本标准差。`itertools.combinations(runs, 2)` 两两组合，算运行之间的 Jaccard 一致度。

### 14. `src/jobpulse/pipeline/market.py` + `src/jobpulse/sources/trends.py`：第 3 步
**(a) 骨架**
- 每日需求：当天有抽取结果的岗位里，每个技能被提到的次数。
- `window_share`：某个窗口内「提到该技能的岗位占比」。
- `demand_table`：7 天和 30 天的占比，以及和上一个同长度窗口相比的变化（单位：百分点）。
- 趋势：GitHub 搜「近 7 天创建、按 star 排序」的仓库，HN 搜近 7 天的高分帖，映射到词表，归一化成 0~1 的分数。

**(b) 为什么**
- 用**占比**而不是绝对数：某天岗位多，不等于需求在上涨。
- 没有上一个窗口时，delta 是 `None`（未知），不是 0，避免把「数据不够」显示成「没变化」。
- 趋势源是可选的：失败只计数，不让整次运行失败。

**(c) 语法**：`Counter.update(iterable)` 累加计数。`Counter[str]()` 是带类型参数的实例化写法。

### 15. `src/jobpulse/bank.py` + `src/jobpulse/pipeline/tailor.py`：第 4 步（只选不写）
**(a) 骨架**
- bullet bank（TOML）里每条经历只写一次。
- 打分：`BM25(要点, 需求词) + 词表匹配奖励`（命中 required 的技能 ×2，命中 nice 的 ×1）。
- 选择：项目按最好的两条 bullet 的分数之和排序，取前 3 个项目，每个项目最多 3 条 bullet。
- 技能行：只调整顺序，命中的放前面，不新增任何技能。
- 校验：`verify_truthful` 逐条检查：bullet 必须逐字存在于 bank 里，数字必须来自 bank，技能行必须只是重排，项目标题不能改。

**(b) 为什么**（面试重点）
- **为什么只选不生成**：简历上的每句话都要能在面试里讲清楚、自证。让 LLM 改写，可能会改数字、夸大职责，属于事实性风险。所以只做选择和排序，真实性由**构造**保证，再由**校验**和**属性测试**双重确认。
- BM25 加词表匹配：BM25 负责词面相关性；词表能识别别名（JD 写 k8s，bullet 写 Kubernetes），两者互补。
- 兼容 jd-resume-tailor 的 bank 格式（`text = "a, b"`），所以学生的真实 bank 可以直接拿来用。

**(c) 语法**：BM25 公式里 `idf = log(1 + (N - df + 0.5) / (df + 0.5))`，k1 控制词频饱和，b 控制文档长度归一。`sorted(..., key=lambda it: -hits(it))` 是稳定排序，分数相同的保持原顺序。

### 16. `src/jobpulse/pipeline/render.py` + `src/jobpulse/pipeline/resumes.py`
**(a) 骨架**
- 用 reportlab 在内存里画一页 US Letter，返回 `fits` 表示有没有超出一页。
- `render_one_page`：超出一页时，删掉分数最低的 bullet 再重画，直到放得下（每个项目至少保留 1 条）。
- `resumes.py`：先过资格检查（只招 PhD、clearance、公民身份），再按覆盖率排序，最多生成 N 份，每份都跑 `verify_truthful`，最后记录 before / after 覆盖率。

**(b) 为什么**：溢出保护只**删**，不改，所以不会破坏真实性（属性测试同样覆盖了被删减之后的简历）。单份简历失败只会被 try 住、计数，不会拖垮整次运行。
**(c) 语法**：`nonlocal y`：在内部函数里修改外层函数的变量。`canvas.Canvas(..., invariant=1)` 让同样的输入产出字节完全相同的 PDF。

### 17. `src/jobpulse/pipeline/gaps.py`：第 5 步
**(a) 骨架**
- 缺口：「市场需要（30 天内至少出现 2 次）」但 bank 里找不到证据的技能。
- 排序：优先级 = 30 天占比 × (1 + 趋势分)。
- 生成：LLM 出 3 个项目点子（JSON），去重之后，再由代码在点子末尾补上真实的需求统计。

**(b) 为什么**
- **数字由代码写**：prompt 明确禁止 LLM 写数字，「why」里的统计句由代码根据实测数据拼出来，所以点子永远不会引用我们没测过的数字。
- 去重分三层：
  1. 标题和已有项目、所有历史点子太相似（Jaccard ≥ 0.5，先去掉通用词）；
  2. 14 天内针对同一组缺口出过点子；
  3. LLM 给的点子不够 3 个时，用确定性模板补齐。
- 一开始去重太激进：模板标题里 "service / tests" 这类通用词让所有模板都撞了，后来加了通用词表才修好（有单测）。

**(c) 语法**：嵌套函数 `taken()` 每次调用都读到最新的 `kept` 列表（闭包）。

### 18. `src/jobpulse/pipeline/publish.py`、`src/jobpulse/pipeline/linkedin.py`、`dashboard/src/main.ts`：第 6 步
**(a) 骨架**
- `publish`：写入静态文件和一份 `data/dashboard.json`，另外每天写一份历史快照。
- 前端：原生 TypeScript，零运行时依赖，用 `tsc` 编译成 `app.js`。图表是手写的 SVG（7 日滚动占比，带十字准线和 tooltip，另有表格视图）。
- 两条 roadmap 时间轴（改自 roadmap-timeline 素材）：亮线随滚动增长，正好停在最后一个已完成节点的圆心。
- `linkedin.py`：只生成周报草稿文本，数字全部从存储里读，**不实现任何发帖功能**。

**(b) 为什么**
- 静态站加 CloudFront 是最便宜的形态：没有服务器，按请求付费，而且在 CloudFront 免费额度之内。
- JSON 设 60 秒缓存，PDF 设 1 天缓存，这样不需要做 CloudFront invalidation。
- 时间轴的停止点按真实布局计算（`offsetTop`），不写死百分比，改了列表也不会错位。

**(c) 语法**：TS 的 `interface` 描述 JSON 形状，`querySelector<HTMLElement>` 是泛型写法。`"module": "none"` 加 `outFile` 编译出单个普通脚本。

---

## 第三部分：编排、可观测性、AWS

### 19. `src/jobpulse/observability.py`
**(a) 骨架**：`JsonLogger` 每条日志输出一行 JSON。`Metrics` 收集计数器，本地打印 JSON，Lambda 里输出 CloudWatch **EMF**。
**(b) 为什么**
- EMF 就是 stdout 上一行特定格式的 JSON，CloudWatch 会自动把它变成指标，不需要调 PutMetricData，也不需要装 agent。
- **踩坑**：每个「指标名 × 维度组合」都是一个计费的自定义指标（超过免费的 10 个之后每个 $0.30/月）。最初的设计会产生约 45 个，所以改成只声明 8 个白名单指标、不带维度；其它计数器仍然留在日志里，可以用 Logs Insights 查。
**(c) 语法**：`frozenset` 不可变集合，适合用作模块级常量。

### 20. `src/jobpulse/runner.py` + `src/jobpulse/cli.py`
**(a) 骨架**：`run_step(ctx, step, date)` 是本地和 Lambda **共用的唯一入口**，负责维护 RunRecord、汇总指标、处理失败。`run_pipeline` 在本地按顺序循环六步。
**(b) 为什么**：失败策略是「降级不宕机」：某一步失败就记下来，后面的步骤照常跑，dashboard 一定会发布，运行状态标为 `partial`。第一步（ingest）会重置当天的 RunRecord，保证重跑幂等。
**(c) 语法**：`dict[str, Callable[[Context, str], StepResult]]` 是函数注册表。`argparse` 的子命令配合 `set_defaults(fn=...)` 做分发。

### 21. `src/jobpulse/adapters/aws.py` + `src/jobpulse/lambdas.py`
**(a) 骨架**
- `DynamoDocStore`：文档存成 JSON 字符串属性，`put_if_absent` 用 `ConditionExpression="attribute_not_exists(pk)"`，query 会跟着 `LastEvaluatedKey` 翻页。
- `S3BlobStore`：写入时设置 `Cache-Control`。
- `read_secure_parameter`：读 SSM SecureString。
- 6 个 handler 都很薄：解析日期 → 拿到 Context（容器内复用）→ 调 `run_step`。

**(b) 为什么**
- 文档存成 JSON 字符串：DynamoDB 只需要 pk / sk 来支持访问模式，存字符串还能避开 float 被转成 Decimal 的麻烦。
- 条件写是原子的，Lambda 重试也不会产生重复。
- 日期来自 Scheduler 的 `<aws.scheduler.scheduled-time>`，手动补跑时也可以直接传 `{"date": ...}`。
- `MAX_LLM_CALLS` 是**每次调用**的安全上限，在 handler 里清零；否则容器热复用时计数会一直累加，跨天误触发上限。

**(c) 语法**：boto3 用懒加载 import（Lambda 运行时自带 boto3，不打进包里）。用模块级 `_ctx` 缓存，配合 `global` 关键字修改它。

### 22. `infra/stack.py`、`infra/bundle.py`、`infra/app.py`：CDK
**(a) 骨架**：Scheduler → Step Functions → 6 个 Lambda（arm64，带保留并发和超时），再加 DynamoDB（按需计费）、S3（私有，带生命周期）、CloudFront（OAC）、两个 Budgets（$1 / $5）、失败告警（Alarm + SNS）。
**(b) 为什么**
- **Step Functions 对比单个 Lambda**：
  - 每一步有自己的超时和内存（tailor 要 512 MB 画 PDF）；
  - 每一步有自己的重试和 catch；
  - 在可视化执行历史里能看到是哪一步失败的；
  - 不会被单个 Lambda 15 分钟的上限卡住。
  - 代价：Standard 类型按状态转换计费，一天一次执行约 9 次转换，在每月 4,000 次免费额度内。
- **catch 后继续**：和本地 runner 的失败策略一致；最后用 Choice 判断有没有 `$.errors`，有就进 Fail，让告警能触发。
- **不需要 Docker 打包**：`uv pip install --python-platform aarch64-manylinux_2_28 --only-binary :all:` 在 Mac 上直接下载 Linux arm64 的 wheel，版本取自 `uv.lock`，并在官方 Lambda 镜像里做过冒烟测试。
- **cdk-nag**：0 条未处理的发现；每条 suppression 都写了原因（WAF 至少 $5/月、PITR 没必要、KMS CMK $1/月等成本取舍）。
- **销毁**：DynamoDB 表、S3 桶、日志组都设成 `RemovalPolicy.DESTROY`，桶开了 `auto_delete_objects`，`destroy.sh` 再清理 SSM 参数和栈外的日志组。

**(c) 语法**
- 状态机是反向构建的：从 `done` 往前，每个 task 先 `add_catch(next)` 再 `next(next)`。
- `sfn.TaskInput.from_json_path_at("$")` 把整个状态输入传给 Lambda。
- `result_path="$.results.ingest"` 把结果挂到状态的一个子路径上，不覆盖原来的输入。

### 23. 测试：`tests/`
- `conftest.py`：离线的 Context，假 HTTP（固定的合成数据）+ 假 LLM + 内存存储。
- `test_truthfulness_property.py`：用 hypothesis 随机生成 JD 需求**和**随机 bank，经过溢出保护后，`verify_truthful` 仍然必须通过。1,000 + 1,000 = 2,000 个变体全部通过（`docs/measurements/truthfulness_property.json`）。
- `test_aws.py`：用 moto 模拟 DynamoDB、S3、SSM，把 6 个 handler 端到端跑一遍，还专门测了 query 翻页。
- `test_pipeline.py`：整条流水线、同日重跑快照完全一致（幂等）、某步失败时降级、数据源全挂时 dashboard 照样发布。

---

## 第四部分：面试常见问题（25 题）

**Q1. 用一句话介绍这个项目？**
每天自动抓取公开的 Summer 2027 实习岗位，用 LLM 把 JD 抽成结构化要求，统计市场技能需求和趋势，从真实经历库里**只选不写**地为每个岗位定制一页简历，再根据技能缺口每天提 3 个项目点子，最后发布成静态看板。全部跑在 AWS Serverless 上，同一份代码也能在本地跑。

**Q2. 为什么用 Step Functions，不用一个 Lambda 顺序跑完？**
1. 每步可以单独设内存和超时（tailor 要 512 MB，其它 256 MB），按需付费；
2. 每步单独 retry 和 catch，失败了也能继续往后跑；
3. 执行历史可视化，一眼看出是哪步失败；
4. 不受单个 Lambda 15 分钟的限制。

代价是状态转换计费和多一层编排的复杂度。一天一次执行大约 9 次转换，远低于每月 4,000 次的免费额度，所以几乎不花钱。

**Q3. 为什么用 DynamoDB，不用 RDS？**
访问模式都是按 key 读写：某天的岗位、某个哈希对应的缓存、需求时间序列。按需计费模式闲置时不花钱，也不需要 VPC 和 NAT（NAT 网关每月三十多美元，远超这个项目的预算）。RDS 需要常驻实例。代价是没有 ad-hoc SQL；需要分析时，本地的 SQLite 用的是同一套 schema，可以在本地查。

**Q4. 单表设计怎么做的？**
pk / sk 两个字符串 key，用前缀区分实体：`POSTING#id/META`、`DAY#date/POSTING#id`、`EXTRACT#hash/version`、`DEMAND/date`、`RUN/date`。「某天有哪些岗位」用 `query(pk=DAY#date)` 一次拿到；时间序列用 `query(pk=DEMAND)`，按 sk（日期）排序。

**Q5. 怎么保证幂等？重跑同一天会不会重复？**
所有写入的 key 要么是日期、要么是内容哈希，重跑只会覆盖同一批 item。新岗位用条件写 `attribute_not_exists`；同一天重跑时，已入库的 JD 直接复用、不重新抓；简历和点子会先按日期清掉旧的再写。有测试 `test_rerunning_a_date_is_idempotent`：跑两次以后，存储快照完全相等。

**Q6. 某条 JD 是坏数据，会不会拖垮整次运行？**
不会。失败处理分三层：
- 单条级：ingest 里每个岗位单独 try，异常只计数（`jd_fetch_failed`）；extract 失败就退回关键词抽取；tailor 里每份简历单独 try。
- 步骤级：本地 runner 和 Step Functions 都是 catch 之后继续执行下一步。
- 全局级：所有步骤跑完后，有失败就把状态标成 partial，并触发告警。

fixtures 里专门放了一个返回坏 JSON 的 Greenhouse 岗位，用来测这条路径。

**Q7. LLM 抽取怎么评测的？结果如何？**
30 份真实 JD，由独立 agent 盲标（304 个标签）。qwen3.8-flash 跑 3 次，每次都不走缓存：micro P / R / F1 = 0.961 / 0.932 / 0.946，F1 的标准差是 0.004；关键词基线是 0.846 / 0.954 / 0.896。只看「必需技能」这一档，LLM 的 F1 是 0.904，关键词是 0.810。三次运行之间的 Jaccard 一致度是 0.935。

**Q8. 关键词的召回比 LLM 还高，怎么解释？**
标注员用的是同一套词表和别名，关键词方法天然能把这些写法全找出来，所以召回高；但它分不清语境（例如公司介绍里提到的技术不算要求），所以精确率低。LLM 的强项是精确率和「必需 / 加分」的分档。这是评测设计的局限，README 里写明了。

**Q9. 怎么防止 LLM 胡编？**
- 抽取：prompt 里要求「只列 JD 里写了的技能」，输出必须通过 schema 校验，再归一到词表，映射不上的单独放进 unmapped。
- 简历：根本不让 LLM 写，只做选择。
- 项目点子：禁止 LLM 写数字，统计句由代码根据实测数据拼出来。

**Q10. 「只选不写」的真实性怎么保证？怎么验证？**
三重保证：
1. 构造上：代码里没有任何生成或修改文本的路径；
2. 运行时：每份简历生成后都跑 `verify_truthful`，检查 bullet 逐字存在于 bank、数字来自 bank、技能行只是重排、项目标题未改；
3. 测试上：hypothesis 属性测试随机生成需求和随机 bank，2,000 个变体全部通过，其中包括被溢出保护删减过的简历。

**Q11. 为什么不让 LLM 改写简历让它更贴合 JD？**
简历上的每句话都要在面试里自证。改写可能悄悄改掉数字、夸大职责，这是事实性风险。只选不写换来的是可验证性，代价是措辞不能「贴」得那么紧；覆盖率提升有多少，我们如实报告（见 README 的数字）。

**Q12. LLM 成本怎么控制？**
- 按 JD 内容哈希缓存，同样的 JD 不会再调用；
- 每天有岗位上限；
- 开发期有账本加 1,150 次硬上限；
- Lambda 里每次调用有 `MAX_LLM_CALLS` 上限；
- 并发只开 4。

账本里记录了 token 数，可以按 DashScope 的价格换算成费用。

**Q13. AWS 成本多少？做了哪些省钱设计？**
详见 `docs/COST.md`，是按公开价格加实测运行量算出的**估算**。主要省钱设计：
- 没有 VPC 和 NAT，也没有常驻计算；
- Lambda 用 arm64；
- DynamoDB 按需计费；
- CloudFront 选 PriceClass_100，并靠 Cache-Control 代替 invalidation；
- 日志只保留 14 天；
- EMF 只发 8 个指标；
- 不开 WAF、PITR 和 KMS CMK，在 cdk-nag 里写明了理由；
- S3 生命周期自动过期旧文件；
- 配了 Budgets 在 $1 / $5 告警。

**Q14. 为什么 EMF 只发 8 个指标？**
每个「指标名 × 维度」都是一个计费的自定义指标，超过免费的 10 个之后每个 $0.30/月。原设计按步骤加维度，大约会有 45 个指标，每月约 $10，比整个系统其它部分加起来还贵。现在只发 8 个运行健康指标，而且不带维度；其它数字留在 JSON 日志里，需要时用 Logs Insights 按需查。

**Q15. 密钥怎么管理？**
- 本地：从 gitignore 的配置指向的 env 文件读取，不导出成环境变量。
- AWS：放在 SSM Parameter Store 的 SecureString 里（用 AWS 托管的 KMS key，免费），只有 extract 和 gaps 两个函数有 `ssm:GetParameter` 权限，资源精确到这一个参数。
- 部署脚本用 0600 权限的临时文件加 `file://` 写入，key 不会出现在命令行参数或 shell 历史里。
- 代码层面：`__repr__` 不打印 key，异常 `from None` 切断异常链。

**Q16. 冷启动？包多大？**
包解压后 27 MB（主要是 reportlab 和 pillow），不打包 boto3（运行时自带）。在官方 Lambda arm64 镜像里，导入加渲染一份 PDF 用了 0.42 秒（colima 虚拟机里实测，`docs/measurements/lambda_image_smoke.json`）。对每天跑一次的批处理来说，冷启动不是问题。

**Q17. 为什么不用 Docker 打包 Lambda？**
`uv pip install --python-platform aarch64-manylinux_2_28 --only-binary :all:` 在 macOS 上就能下载 Linux arm64 的 wheel，`cdk synth` 不依赖容器，CI 也更快。版本从 `uv.lock` 导出，保证 Lambda 和本地一致。打出来的包在官方镜像里做过冒烟测试。

**Q18. 保留并发设成 1 有什么坑？**
新账号的并发总配额可能只有 10，而 AWS 要求未保留的并发至少剩 10，这时任何保留并发都会让部署失败。`deploy.sh` 会先查账户配额，低于 100 就自动设成 0（不保留）。

**Q19. 端口与适配器带来了什么？**
业务逻辑只写一份：本地用 SQLite 和文件，AWS 用 DynamoDB 和 S3，测试用内存实现和 moto。repository 测试参数化跑过三种存储，Lambda handler 只有十几行。

**Q20. 趋势信号怎么做的？有什么局限？**
GitHub 搜近 7 天创建的仓库（按 star 排序）、HN 搜近 7 天的高分帖，各发一次请求，按词表映射后计数，再归一到 0~1。局限有三点：
1. 只取前 100 个仓库和前 500 条帖子；
2. 补跑历史日期时，star 数是现在的值，不是当时的值；
3. 分数只是一个粗略的「开发者注意力」信号。

**Q21. 需求的 7 天 / 30 天变化怎么算？**
用占比的差值（百分点）：当前窗口里提到该技能的岗位数 ÷ 窗口内的岗位总数，减去上一个同长度窗口的同一个值。上一个窗口没有数据时，结果是 None（未知），不是 0。

**Q22. 爬取合规怎么保证？**
- 只用公开数据源和 ATS 的公开 JSON API；
- 遵守 robots.txt，包括 Crawl-delay；
- 按 host 限速，并有退避重试；
- User-Agent 不含任何个人信息；
- 不登录任何站点。

代价是 Workday 的部分租户和 SmartRecruiters 的 API 被 robots 禁止，那部分岗位拿不到 JD，我们如实计数（见 README）。

**Q23. 如果岗位量涨 100 倍，哪里先成为瓶颈？**
1. ingest 是串行礼貌爬取，会撞上 10 分钟的超时。改法：用 Step Functions 的 Map 状态按 ATS 分片，或者加 SQS 队列。
2. extract 的 LLM 并发和限流。改法：调大并发，配合退避。
3. publish 的单个 JSON 文件会变大。改法：分页或拆分文件。

DynamoDB 按需计费本身能扛住，但 `DEMAND` 和 `RUN` 这类单分区 key 在大规模下会成为热点，可以按月份分桶。

**Q24. 测试策略？CI 跑什么？**
- 单元测试：词表、约束规则、解析和校验、BM25。
- 属性测试：hypothesis 验证真实性不变量。
- 集成测试：用 fixture 跑整条流水线，覆盖幂等和降级。
- AWS 适配器：用 moto 测。

全部离线运行，不需要任何密钥。CI 依次跑 ruff、mypy --strict、pytest、dashboard 构建并检查产物是否最新、`cdk synth` 加 cdk-nag。整套测试连跑 10 次，全部通过。

**Q25. 这个项目最大的不足是什么？下一步做什么？**
- 数据源单一（只有 Simplify），而且只覆盖有公开 API 的 ATS，有选择偏差；
- 评测标注员是 AI，最好再加人工复核；
- 补跑的历史日期用的是今天的快照，已经下架的岗位看不到；
- 还没有部署到 AWS 实测成本和时延。

下一步：接入更多数据源；做人工标注抽检；按月分区 key；部署以后，用真实的 Lambda 时长校准 COST.md。
