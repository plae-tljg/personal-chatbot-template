-- ============================================================================
-- personal-chatbots — v1 schema
-- ----------------------------------------------------------------------------
-- 5 张表。分两半，界线是「离线 / 在线」：
--
--   同步而来（online，pc build 写）     knowledge / entities / entity_links / documents
--   运行时状态（online，只追加）        messages
--
-- 离线的那一半在 content/：
--   content/bot.json         配置     —— 人改，改一次 = 一次部署
--   content/*.yaml           种子     —— 人和 AI 改（经 PR），pc build 同步进库
--
-- 关键：**content/ 不是运行时的一部分。** 运行时只读这个库；YAML 每次 build
-- 只被读一次。YAML 不是第二个真相，它是同一份知识的「可编辑形态」：
-- 人能读、AI 能改、git 能 diff。库里那份是「运行形态」：有索引、能 join、毫秒可查。
--
-- pc build 的同步是幂等的：
--   knowledge 按 slug upsert；YAML 里删掉的 slug -> status='archived'，不 DELETE
--   （messages.matched_slug 的历史还指着它，访客过去的答案必须仍然可解释）
--   entities / documents 从 ingest + curation 重建；上游消失的 -> archived
--
-- 为什么没有 proposals / reviews / revisions / tests / settings 表：
--   维护者是一个通用 coding agent（openclaw / hermes / DSH），
--   它的提案系统是 git：改 content/ -> 开分支 -> PR -> CI 跑测试 -> 人 merge。
--   在数据库里重造这四样是浪费。见 docs/CONCERNS.md C5。
--
-- 为什么 knowledge 没有 hits/misses 列：
--   它每次 build 都会被同步，计数器会丢。统计一律从 messages 现算（见视图）。
--   不变量：同步来的表无状态，状态只在 messages。
--
-- 目标：SQLite >= 3.35（FTS5 + trigram）
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ============================================================================
-- 构建产物：名词
-- ============================================================================

-- repo / account / person / language / topic / project / product 都是这里的行。
-- 没有 products 表：卖东西就是 entity_type='product'。
-- 详情见 docs/CONCERNS.md C3：什么时候才真的需要一张独立的 products 表。
CREATE TABLE IF NOT EXISTS entities (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_type  TEXT NOT NULL,
  key          TEXT NOT NULL,               -- 'plae-tljg/dsh-review'、'language:python'
  name         TEXT NOT NULL,
  summary      TEXT NOT NULL DEFAULT '',    -- 一行话，直接填进 {summary}
  aliases_json TEXT NOT NULL DEFAULT '[]',  -- 归一化别名，槽位匹配靠它
  attrs_json   TEXT NOT NULL DEFAULT '{}',  -- 易变值放这里：stars、price、version、stock
  url          TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL DEFAULT 'live'
               CHECK (status IN ('live', 'draft', 'archived')),
  origin       TEXT NOT NULL DEFAULT '',    -- 'github:plae-tljg' | 'curation' | 'ingest'
  source_ref   TEXT NOT NULL DEFAULT '',
  updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (entity_type, key)
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities (entity_type, status);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities (name);

-- 一张表顶掉 repo_topics / repo_languages / account_repos 三张未来的表。
-- 关系放进 JSON 数组就再也查不了双向，所以这是唯一"现在不建、以后会痛"的。
CREATE TABLE IF NOT EXISTS entity_links (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  from_entity_id INTEGER NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
  link_type      TEXT NOT NULL,   -- owned_by | uses_language | tagged | includes | forked_from
  to_entity_id   INTEGER NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
  UNIQUE (from_entity_id, link_type, to_entity_id),
  CHECK (from_entity_id <> to_entity_id)
);

CREATE INDEX IF NOT EXISTS idx_links_from ON entity_links (from_entity_id, link_type);
CREATE INDEX IF NOT EXISTS idx_links_to   ON entity_links (to_entity_id, link_type);

-- 维护者被允许读的原文（README、profile、自己的笔记）。
-- 也是运行时最便宜的一级：FTS 命中就是真答案，0 token。
-- trigram 分词是因为要同时答中英文，unicode61 切不开中文。
CREATE TABLE IF NOT EXISTS documents (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  slug         TEXT NOT NULL UNIQUE,        -- 'readme:plae-tljg/dsh-review'
  title        TEXT NOT NULL,
  body         TEXT NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'readme',
  entity_id    INTEGER REFERENCES entities (id) ON DELETE SET NULL,
  source_ref   TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL DEFAULT '',
  fetched_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_documents_entity ON documents (entity_id);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5 (
  title, body, content = 'documents', content_rowid = 'id', tokenize = 'trigram'
);

CREATE TRIGGER IF NOT EXISTS documents_fts_ai AFTER INSERT ON documents BEGIN
  INSERT INTO documents_fts (rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS documents_fts_ad AFTER DELETE ON documents BEGIN
  INSERT INTO documents_fts (documents_fts, rowid, title, body)
  VALUES ('delete', old.id, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS documents_fts_au AFTER UPDATE ON documents BEGIN
  INSERT INTO documents_fts (documents_fts, rowid, title, body)
  VALUES ('delete', old.id, old.title, old.body);
  INSERT INTO documents_fts (rowid, title, body) VALUES (new.id, new.title, new.body);
END;

-- ============================================================================
-- 构建产物：会说什么
-- ----------------------------------------------------------------------------
-- 由 content/knowledge.yaml 载入。一行 = 一个问句形状 -> 一个确定性动作。
--
-- 最重要的一条规矩写在模板里：模板只能出现 {slot}，不能出现具体数值。
-- 价格、版本、库存、营业时间都必须从 entities 解析。
-- 理由不是洁癖，是成本：数值写进模板，改一次价格就要改 N 条知识；
-- 数值放在实体里，改一次价格是 1 行数据、0 次 AI 唤醒。
-- 详见 docs/CONCERNS.md C2。
-- ============================================================================

CREATE TABLE IF NOT EXISTS knowledge (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  slug         TEXT NOT NULL UNIQUE,    -- 'repo.about'，AI 靠 slug 改版，不靠 id
  locale       TEXT NOT NULL DEFAULT '',-- '' = 不限语言
  patterns_json TEXT NOT NULL DEFAULT '[]',  -- ["what is {repo} about", ...] 多入口少行数
  slots_json   TEXT NOT NULL DEFAULT '{}',   -- {"repo":"repo"}
  match_json   TEXT NOT NULL DEFAULT '{"require":[],"exclude":[]}',
  action_json  TEXT NOT NULL DEFAULT '{"kind":"answer","template":""}',
  citations_json TEXT NOT NULL DEFAULT '[]',
  status       TEXT NOT NULL DEFAULT 'live'
               CHECK (status IN ('draft', 'live', 'archived')),
  source       TEXT NOT NULL DEFAULT 'seed',
  updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_knowledge_live ON knowledge (status);

-- ============================================================================
-- 运行时状态：唯一被在线写入的表
-- ============================================================================

CREATE TABLE IF NOT EXISTS messages (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id        TEXT NOT NULL,
  turn              INTEGER NOT NULL DEFAULT 0,
  role              TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content           TEXT NOT NULL,
  normalized        TEXT NOT NULL DEFAULT '',   -- 归一化问句，用来聚类同类问题
  resolution_source TEXT NOT NULL DEFAULT '',   -- knowledge | entity | search | refuse
  matched_slug      TEXT NOT NULL DEFAULT '',   -- knowledge.slug 或 entity.key
  citations_json    TEXT NOT NULL DEFAULT '[]',
  -- 这一轮把哪些指代表达解析成了谁，例如 {"the second one": "x/y"}。
  -- 指代是「跨轮但无状态」的：语境从上面的 citations_json 推出来，不另存变量。
  -- 记在这里只是让人和 AI 能看见"它把你的话读成了什么"，不是运行时的必需输入。
  refs_json         TEXT NOT NULL DEFAULT '[]',
  unresolved        INTEGER NOT NULL DEFAULT 0, -- 结构答不出来 = 1。这是唯一的学习信号
  latency_ms        REAL NOT NULL DEFAULT 0,   -- sub-millisecond is the point
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, turn);
-- 收件箱索引：无论流量多大它都很小。这一条索引就是"读失败"和"读全部"的区别。
CREATE INDEX IF NOT EXISTS idx_messages_unresolved
  ON messages (created_at DESC) WHERE unresolved = 1;
CREATE INDEX IF NOT EXISTS idx_messages_slug ON messages (matched_slug);

-- ============================================================================
-- 视图：人和 AI 读同一组数字（统计一律现算，不存计数器）
-- ============================================================================

-- AI 的收件箱：真实问过、结构答不出来的问题，按形状**聚类**。
--
-- 一处 GROUP BY，不是一个窗口函数：窗口函数会每个 message 返回一行，同一个问句
-- 问十次就在收件箱里出现十行。收件箱要回答的是"有多少种问题答不出来、各被问了
-- 几次"，不是"有多少条消息"。这个 bug 在第一次真跑一圈时就被看到了。
CREATE VIEW IF NOT EXISTS v_unresolved_inbox AS
SELECT
  MIN(m.id)         AS message_id,
  m.normalized,
  (SELECT x.content FROM messages x
    WHERE x.normalized = m.normalized AND x.unresolved = 1 AND x.role = 'user'
    ORDER BY x.id DESC LIMIT 1)  AS content,
  COUNT(*)          AS same_shape_count,
  MIN(m.created_at) AS first_at,
  MAX(m.created_at) AS last_at
FROM messages m
WHERE m.unresolved = 1 AND m.role = 'user'
GROUP BY m.normalized
ORDER BY same_shape_count DESC, last_at DESC;

-- 各级回答占比：refuse 的比例就是 kappa 的反面
CREATE VIEW IF NOT EXISTS v_resolution_mix AS
SELECT COALESCE(NULLIF(resolution_source, ''), 'unknown') AS resolution_source,
       COUNT(*) AS turns, ROUND(AVG(latency_ms), 1) AS avg_latency_ms
FROM messages WHERE role = 'assistant'
GROUP BY 1 ORDER BY turns DESC;

-- 每一行知识被命中多少次（现算，不存列，build 重建也不会丢）
CREATE VIEW IF NOT EXISTS v_knowledge_coverage AS
SELECT k.slug, k.status,
       SUM(CASE WHEN m.unresolved = 0 THEN 1 ELSE 0 END) AS hits,
       k.updated_at
FROM knowledge k
LEFT JOIN messages m ON m.matched_slug = k.slug AND m.role = 'assistant'
GROUP BY k.slug;

-- 上线 30 天从没命中过的行。该归档，不是该加更多规则。
CREATE VIEW IF NOT EXISTS v_dead_knowledge AS
SELECT k.slug, k.patterns_json
FROM knowledge k
LEFT JOIN messages m ON m.matched_slug = k.slug
WHERE k.status = 'live' AND m.id IS NULL;

-- ----------------------------------------------------------------------------
-- 会话列表：messages 上的一个视图，不是一张表。
--
-- 为什么不是 sessions 表：会话的全部信息（标题、轮数、时间）都能从 messages
-- 现算出来。加一张表就要维护两份真相，还会在 build 时引入"哪些算活数据"的
-- 新问题。等真的需要 per-session 元数据（locale、referrer、用户自己改的标题）
-- 时，那张表才有理由存在 —— 那时它承载的是现有表表达不了的信息。
-- ----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_sessions AS
SELECT
  m.session_id,
  (SELECT u.content FROM messages u
    WHERE u.session_id = m.session_id AND u.role = 'user'
    ORDER BY u.turn, u.id LIMIT 1)                       AS title,
  COUNT(*)                                               AS messages,
  SUM(CASE WHEN m.role = 'assistant' THEN 1 ELSE 0 END)   AS turns,
  SUM(CASE WHEN m.unresolved = 1 THEN 1 ELSE 0 END)       AS unresolved,
  MIN(m.created_at)                                      AS started_at,
  MAX(m.created_at)                                      AS last_at,
  MAX(m.id)                                              AS last_message_id
FROM messages m
GROUP BY m.session_id
ORDER BY last_message_id DESC;
