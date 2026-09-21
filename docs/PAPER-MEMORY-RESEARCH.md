# 论文索引与长期记忆研究

调研日期：2026-09-21。需求：几百到几千篇、本地 PDF / Zotero，既精确查证也跨论文总结。这里是基于官方资料的适配分析，尚未在你的论文库运行性能/质量基准，也没有上传私人论文。

## 建议

第一候选组合：**Zotero/PDF → Docling → 保留页码与坐标的结构化原文 → 关键词 + 小型多语言 embedding → 重排 → 页级引用**。模型记忆只保存偏好、规则和研究进度，论文全文进入知识库。

对你的规模，优先以 **SQLite FTS + Qdrant** 做可替换检索试点，复用现有 SQLite 业务；若下一版任务服务决定统一采用 PostgreSQL，则改用 **PostgreSQL + pgvector**，避免为同一个需求同时运维两套向量后端。选择数据库的主要依据是部署与过滤能力，解析质量和检索模型才更直接影响论文问答。

## 为什么不能只选一个“长记忆框架”

| 问题 | 应由谁解决 |
|---|---|
| 用户偏好、纠正和研究目标跨会话保留 | 可编辑 Memory / Rules 层 |
| 两千篇论文里找具体实验设置 | 文档解析与检索层 |
| 从几十篇论文归纳方法和争议 | 有覆盖范围的多步研究流程 |
| 三天后继续任务，不重复发送邮件 | 持久任务与对外操作记录 |
| 当前模型窗口太长 | 预算化上下文、摘要和按需回读 |

Letta 的核心记忆与历史消息机制、Mem0 的记忆层、Graphiti 的时序关系建模各有用途，但都不能省略论文解析、原文证据和评测。对 oneAI，先把这些职责拆开，比直接迁入某个“无限记忆”系统更可控。[Letta](https://docs.letta.com/v1-sdk/concepts/stateful-agents)、[Mem0](https://github.com/mem0ai/mem0)、[Graphiti](https://github.com/getzep/graphiti)

## 1. 接入 Zotero

以 library ID + item key 为逻辑身份，附件内容 hash 作为文件版本，DOI/arXiv ID 作为辅助去重依据。不要仅按文件名判重；同一 DOI 的预印本与正式版本也不自动丢弃其一。

Zotero 官方 API 支持库版本、增量同步、分页和删除记录。元数据同步与 PDF 文件获取应分别记录状态；附件可能在 Zotero Storage、WebDAV、链接文件或本机，不能假设 Web API 有记录就一定能下载文件。[Zotero 同步文档](https://www.zotero.org/support/dev/web_api/v3/syncing)

原始论文、笔记、高亮、个人理解分别索引并注明来源类型，避免把你的阅读推测当成作者实验结果。

## 2. PDF 解析选型

| 方案 | 官方支持与适配判断 | 在本项目中的位置 |
|---|---|---|
| Docling | 文档对象包含页号、bounding box 等 provenance；统一结构便于转 Markdown 与保留证据 | 默认试点解析器，保存完整结构化对象，不只导出 Markdown |
| MinerU | 当前官方仓库提供多种解析层级、文档服务和稳定页/块定位，覆盖复杂布局 | 与 Docling 对照测试公式、多栏、中文与扫描页；按实际版本/部署模式评估资源 |
| GROBID | 专注科技论文，提取书目信息、引用、结构化正文和 PDF 坐标 | 引文关系/参考文献需求明确时作为补充，初版不强制多跑一遍全部文档 |
| ColPali / ColVision | 以视觉文档表示支持页面检索 | 表格/图像漏检较多时作为第二检索通道，不能替代答案的页内核验 |

依据：[Docling provenance](https://docling-project.github.io/docling/reference/docling_document/)、[MinerU](https://github.com/opendatalab/MinerU)、[GROBID](https://github.com/grobidOrg/grobid)、[ColVision](https://github.com/illuin-tech/colpali)。上表不声称任何解析器在你的论文上已获胜。

值得注意：MinerU 当前仓库标注的是基于 Apache 2.0 并带附加条件的项目许可证，不能照搬旧文章中的许可描述。实际采用时锁定代码、模型与依赖版本，并分别查看对应许可；本报告不作法律结论。[项目许可入口](https://github.com/opendatalab/MinerU#license-information)

解析缓存键必须包含 `source_hash + parser_name + parser_version + options_hash`。失败文件不进入“已成功索引”计数；OCR 低置信度、缺页、公式缺失应保留诊断和可重跑状态。

## 3. 检索模型与数据库

| 候选 | 官方能力 | 建议 |
|---|---|---|
| BGE-M3 | 多语言，dense/sparse/multivector，1024 维，最长 8192 token | 成熟对照基线；不意味着把整篇论文塞进一个向量 |
| Qwen3-Embedding-0.6B | 多语言、最长 32K，上限 1024 维且可调输出维度 | 轻量候选，与 BGE-M3 用同一语料/问题比较；先试 512/1024 维 |
| Qwen3-Reranker-0.6B | 独立重排模型 | 对召回后的少量片段试验收益、延迟与成本，不对全库逐项重排 |
| Qdrant | Query API 支持多路 prefetch、融合与多阶段检索 | 保留当前 SQLite 时的首选独立索引服务候选 |
| pgvector | PostgreSQL 内保存向量，支持精确/近似检索，能与 SQL 数据关联 | 后台已使用 PostgreSQL 时优先；中文词法检索需单独选 tokenizer/稀疏方案 |

官方来源：[BGE-M3](https://huggingface.co/BAAI/bge-m3)、[Qwen embedding](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)、[Qwen reranker](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)、[Qdrant hybrid](https://qdrant.tech/documentation/search/hybrid-queries/)、[pgvector](https://github.com/pgvector/pgvector)。不同模型的公开榜单不能直接决定你库里的效果。

推荐初始检索实验：关键词候选 50 + dense 候选 50 → 去重 / RRF → 重排前 20 → 返回 6–10 段。以上是试验参数，不是已经证明的最佳值。精确数字、符号、缩写和引用名依赖词法检索；中文问题找英文证据依赖跨语言语义召回；二者分别测试。

切分以章节和段落为主，尝试 400–800 token 的片段并保留上下文路径。表格与图注独立记录、连接其解释段；公式保留原始页与提取表示。检索片段后再读取其父段/章节，避免半句话失去条件。页码引用必须来自解析器 provenance，而非将 Markdown 行号换算成页号。

## 4. 完整框架与轻量组合

| 方案 | 适合什么 | 对 oneAI 的判断 |
|---|---|---|
| 直接组合解析器 + 检索 + 自有证据结构 | 工具数量有限，需要严格控制数据与 UI | 首选，模块可替换，避免把整个个人助手绑定到 RAG 平台 |
| LlamaIndex | 接入与索引组件较多，希望复用编排接口 | 按需取组件；自有 Evidence schema 保持独立 |
| RAGFlow | 想快速部署完整文档问答后台 | 可作体验对照，但官方自托管前置资源为至少 4 核、16GB RAM、50GB 磁盘，不符合最小便宜转发机的定位 |
| LightRAG | 关系丰富、跨文档关联是主要难点 | 在普通混合检索基线后再评估；实体抽取增加索引成本与错误传播路径 |

依据：[LlamaIndex](https://github.com/run-llama/llama_index)、[RAGFlow 自托管要求](https://github.com/infiniflow/ragflow#-self-hosting)、[LightRAG](https://github.com/HKUDS/LightRAG)。这是适配判断，不把项目自己的性能描述当成 oneAI 实测。

## 5. 精确查证与跨论文综述采用不同流程

精确查证：问题 → 关键词/向量检索 → 重排 → 读原文页 → 答案 + 对应证据。对表格数值、公式和限定条件，必要时查看页图。证据不足时保留未知，不从摘要补数值。

跨论文综述：先确定主题、年份和纳入范围 → 找候选论文 → 每篇抽取结构化证据卡 → 方法/假设/实验/局限对照表 → 识别一致、冲突和空白 → 逐项回原文核验。每次展示实际纳入多少篇、哪些还未读取，不以 top-k 命中冒充全库系统综述。

论文摘要与主题总结是派生物；原文版本变化时标记待更新。你的批注是独立来源，助手归纳是另一种来源。三者可一起检索，但回答必须说明是谁的判断。

## 6. 规模与资源估算（假设，不是实测）

假设 2,000 篇 × 平均 10 页 × 每页 3 个片段 = 60,000 个片段。

- 1024 维 float32 原始 dense 向量：60,000 × 1024 × 4 ≈ 246 MB（十进制）。
- 不含 ANN 索引、文本、稀疏向量、元数据、PDF、页图、数据库缓存与备份；不能据此承诺 256MB 内存足够。
- 解析和 embedding 通常是批量计算负担，应与轻量常在线任务服务分离。可在 Mac 开机时批处理后上传授权索引，或按需使用临时云端计算；既有索引在 Mac 离线时仍可查询。
- 视觉多向量按页保存多个向量，不能沿用“每片段一个 dense 向量”的容量估算；必须单独测量。
- 模型 API/本地推理选择以隐私范围、网络、预算与实际延迟为依据，本轮没有取得报价或性能实测。

## 7. 可执行的选型实验

第一轮只用 50 篇代表样本：双栏数字 PDF、公式/表格密集、扫描、中文/英文以及少量同一论文多版本。人工选取约 20 页作为解析 gold，标注阅读顺序、表格关键单元格、公式和正确页号。

建立 60 个真实问题：20 个单篇精确定位、15 个中文问英文资料、15 个跨论文比较、10 个无答案或版本冲突。单篇与跨篇分别定义 gold 证据集合，预留一部分只用于最终验证，避免调参污染。

| 阶段 | 对照 | 指标 |
|---|---|---|
| 解析 | Docling vs MinerU；必要时 GROBID 补元数据 | 缺页/错序率、表格/公式证据正确率、页定位、秒/页和失败率 |
| 召回 | FTS 基线、dense、hybrid | Recall@20、MRR@10、按语言和题型分组结果 |
| 重排 | 无重排 vs 小型 reranker | nDCG@10、证据覆盖、p50/p95 延迟 |
| 答案 | 精确查证 / 多篇证据卡 | 引用支持率、无答案拒答、约束/数值准确性；人工复核，不只让模型给自己打分 |
| 运维 | 增量更新、删除、版本变化、中断重启 | 无陈旧命中、引用可恢复、幂等导入、恢复时间和成本 |

判定方式：优先达到来源定位和事实正确性的门槛，再比较资源成本。默认只在 hybrid 明显漏掉关系问题时才增加图层；只在图表问题明显漏召回时才增加视觉索引。没有样本数据前不宣布“哪个最好”。

## 8. 下一步推荐决策

采用“Docling + hybrid 检索”作为第一轮候选，BGE-M3 与 Qwen3-Embedding-0.6B 做同库对照；MinerU 作为解析对照。先为 50 篇样本建立 gold 与测量，再决定模型和数据库，不直接把几千篇全部重跑。

需要实际实施时，可先提供 Zotero 导出或指定本地论文目录；本轮只做公开资料研究，未扫描个人文件库，也未安装大型解析/模型依赖。


## 本地实测补充

桌面 30 篇去重论文的解析和模型试跑见 [PAPER-PILOT-2026-09-21.md](PAPER-PILOT-2026-09-21.md)。这里的选型建议需结合该报告的样本边界，不把模型公开评测当成个人语料实测。
