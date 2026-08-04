# Neo4j 法律知识图谱

## 目标

知识图谱是现有法律检索的可选增强层，不替代 MySQL、Chroma 或 Qdrant：

1. MySQL 仍是法源、条文和版本元数据的唯一事实来源；
2. Chroma / Qdrant 负责语义召回，关键词检索负责精确法条和词法召回；
3. Neo4j 使用 Cypher 保存法律资料的修订关系和领域关系，仅为已召回候选提供关系证据与排序加权；
4. Neo4j 未配置、连接失败或数据未同步时，系统自动退回原有“关键词 + 向量 + RRF”检索链路。

这种设计避免 Graph RAG 在关系扩展时引入与问题无关的法源，也保证图数据库短暂不可用不会影响咨询服务。

## 图谱模型

```text
(:LegalSource)-[:HAS_ARTICLE]->(:LegalArticle)
(:LegalSource)-[:IN_LAW_AREA]->(:LegalLawArea)
(:LegalSource)-[:TAGGED_WITH]->(:LegalKeyword)
(:LegalSource)-[:AMENDS]->(:LegalSource)
(:LegalSource)-[:AMENDED_BY]->(:LegalSource)
```

所有节点都带有 `user_id` 与租户化 `key`，关系查询始终按当前用户过滤，防止跨用户法源关联。法源状态为 `inactive` 的节点不会为图谱排序提供证据。

## 启用方式

安装后端依赖并在 `.env` 中配置已有的 Neo4j 实例：

```dotenv
NEO4J_ENABLED=true
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-password
NEO4J_DATABASE=neo4j
```

Docker Compose 部署时，`NEO4J_URI` 应指向容器可访问的 Neo4j 地址，而不是容器内的 `localhost`。

## 同步和查询

- 创建、编辑、变更状态或删除单条法源时，服务会尝试同步对应图谱节点；失败不会回滚主业务操作。
- `POST /api/legal/sources/{source_id}/reindex` 会在成功重建条文向量后同步该法源及其条文节点。
- `POST /api/legal/knowledge-graph/reindex` 用于回填当前管理员的所有法源；可增加 `?source_id=123` 只回填一个法源。
- `GET /api/legal/knowledge-graph/health` 查看 Neo4j 连接状态。
- `GET /api/legal/sources/{source_id}/knowledge-graph?depth=2` 查询指定法源的修订关系图，最大深度为 3。

## 检索融合

法律条文检索先计算关键词和向量召回的 RRF 分数，再向 Neo4j 查询候选条文之间的两类关系：

- 法源存在 `AMENDS` 或 `AMENDED_BY` 版本关系；
- 法源归属相同的法律领域。

存在关系证据的候选会获得一项有界的排序增益，默认每条支持关系增加 `0.001`，最多按 3 条关系累计。该增益用于打破近似候选的排序，不应压过关键词、精确条文引用或向量召回的强相关结果。可通过以下配置调整：

```dotenv
LEGAL_GRAPH_EVIDENCE_BOOST=0.001
LEGAL_GRAPH_EVIDENCE_MAX_SUPPORT_COUNT=3
```

响应中的 `score_breakdown.graph_support` 会返回关系类型、关联条文 ID、是否共享法律领域、支持数量及实际 `boost`，供前端或审核人员解释排序来源。

## 回归评测

运行以下命令可验证图谱层不会扩展原始候选集，并会在法规修订关系或相同法律领域的近似候选中按预期调整排序：

```powershell
python eval/run_graph_rag_eval.py --pretty --output eval/outputs/graph_rag_ablation_report.json
```

该脚本使用 `eval/graph_rag_eval_cases.json` 中的确定性关系夹具，不依赖 Neo4j 和模型服务，适合 CI 回归。它验证的是图谱融合算法的边界，不是实际法规语料的质量结论。启用真实 Neo4j 后，应另行构建包含法规修订、失效法规和同领域歧义问题的脱敏评测集，并对比图谱开关前后的 Hit@K、MRR、引用正确率和拒答准确率。
