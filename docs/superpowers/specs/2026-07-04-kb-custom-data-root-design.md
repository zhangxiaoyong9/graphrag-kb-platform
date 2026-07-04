# 2026-07-04 — KB 创建自定义 data_root + 默认按 KB 隔离

## 背景

`KnowledgeBase.data_root` 是每个 KB 的数据目录:graphrag 把 `entities.parquet` / `relationships.parquet` / `communities.parquet` / `reports/` / `extractions/` / `text_units.parquet` 写在它下面,LanceDB 向量落在 `<data_root>/vectors/`。orchestrator / worker / query / export / stats 等处处 `Path(kb.data_root)` **原样消费,不拼任何 KB 子目录**。

但 `create_kb`（`kb_platform/api/routes_kbs.py:163`）现在把**全局** `request.app.state.data_root` 盖到每个 KB 行上 —— 所以**默认情况下,同一服务上的多个 KB 共用同一个目录**,parquet 与向量互相覆盖。KB 本应是隔离边界(否则没必要分多个 KB),这个默认是错的。前端 `KbForm` 也没有 data_root 输入,用户无从指定。

## 目标

- **默认按 KB 隔离**:新建 KB 不带 data_root → `data_root = {global_abs}/{kb.id}`。
- **可自定义**:新建 KB 带绝对路径 → 原样用作该 KB 的数据目录。
- **create-only**:不可经 `KbUpdate` 修改(改 data_root = 孤立图谱/向量)。
- **详情页可见** data_root。

## 非目标

- **不迁移存量 KB**:已存在的 KB 行 data_root 不动(保持其原值);新默认只对**新建** KB 生效。
- create 时不 mkdir(create 保持无副作用;目录由引擎在首次索引时建)。
- `KbUpdate` 不暴露 data_root。
- 不做多租户强权限校验(自托管,操作员即用户)。
- 不在 create 时预检路径可写性(索引时才报错;可接受,后续可加可选预检)。

## 设计

### 后端

**模型**(`kb_platform/api/models.py`)
- `KbCreate` 加 `data_root: str | None = None`。
- `KbUpdate` **不加** data_root(pydantic 默认忽略未知字段,客户端即便发了 data_root 也会被忽略 → 更新无效)。
- `KbDetailOut` 加 `data_root: str`(详情页可见);`KbOut`(列表)**不加**,保持精简。

**校验纯函数**(`routes_kbs.py` 内,便于单测):`validate_data_root(path: str) -> str`
- 必须 `os.path.isabs(path)` —— 否则 `raise HTTPException(400, "data_root 必须为绝对路径")`。
- `Path(path).parts` 不得含 `".."` —— 否则 `raise HTTPException(400, "data_root 不得含 .. ")`。
- 返回原字符串(原样用,不做 normalize/resolve —— 用户给的路径即最终路径)。

**`create_kb` 改动**(`routes_kbs.py:149`):
- 若 `payload.data_root` 提供 → 先 `validate_data_root` 校验。
- 插入 KB 行(`data_root` 暂留空字符串/占位)→ `s.flush()` 拿到 `kb.id` →
  - 提供 → `kb.data_root = payload.data_root`
  - 未提供 → `kb.data_root = str(Path(request.app.state.data_root).resolve() / str(kb.id))`
- 再 `s.flush()` 持久化。
- 现有 profile/settings 校验逻辑不变。

> 注:`data_root` 列 NOT NULL(`models.py:23`),而默认值依赖 flush 后才拿到的 `kb.id`。实现:先用全局 `app.state.data_root` 占位构造 KB 行 → `s.flush()` 拿 id → `setattr(kb, "data_root", 最终值)` → 再 `s.flush()`。占位值会被最终值覆盖,语义不变。

### 前端

- `web/src/api/types.ts`:`KbCreate` 加 `data_root?: string | null`;`KbDetail`(或 `KbDetailOut` 对应类型)加 `data_root: string`。
- `web/src/components/KbForm.tsx`:加一个可选 `data_root` 输入 —— **创建模式可填,编辑模式只读或隐藏**;placeholder:`留空 = 自动按 KB 隔离`。
- KB 详情展示(`KbOverviewPage` 或 `KbLayout` 顶栏):只读展示 `data_root`,让用户知道该 KB 的图谱落在哪。

### 行为矩阵

| 创建时 `data_root` | 结果 |
|---|---|
| 省略 | `{global_abs}/{kb.id}`(自动隔离) |
| 合法绝对路径、无 `..` | 原样用 |
| 相对路径 | 400 |
| 含 `..` 段 | 400 |

## 契约小结

- `KbCreate.data_root?: str | None`;`KbDetailOut.data_root: str`;`KbUpdate` 无 data_root(发了也被忽略)。
- 默认值:`str(Path(app.state.data_root).resolve() / str(kb.id))`。
- 校验:`os.path.isabs` + `Path(path).parts` 不含 `".."`,否则 400。
- create-only;不 mkdir;不迁存量。

## 测试策略

**后端**(`tests/test_api_kbs.py` 或对应文件)
- create 不带 data_root → 落库 `data_root` endswith `"/{id}"`(且父目录 = resolve 后的全局根)。
- create 带合法绝对路径 → 原样落库,`GET /kbs/{id}` 返回该路径。
- create 带相对路径 → 400,错误信息含"绝对路径"。
- create 带 `..` 段 → 400,错误信息含"`..`"。
- `PATCH /kbs/{id}` body 带 `data_root` → 字段被忽略,`GET` 回读 data_root 不变。
- `validate_data_root` 纯函数单测:合法/相对/`..`/正常绝对路径各一条。

**前端**
- `KbForm` 创建模式:出现 data_root 输入;留空提交 → body 不含 data_root;填写 `/abs/path` 提交 → body 含之。
- `KbForm` 编辑模式:不显示 data_root 输入(或只读)。
- 详情页渲染 data_root 路径文本。

## 风险与回滚

- 纯加字段(create 入参可空)+ create 默认值改变;`data_root` 列已存在,**无 Alembic 迁移**。
- 回滚:还原 create 默认为全局 `app.state.data_root`、删 `KbCreate.data_root` / `KbDetailOut.data_root` / 前端输入即可。
- **存量碰撞提醒**(不在范围):若你之前在同一服务上索引过多个 KB,它们的数据可能已在全局目录互相覆盖;本特性只保证**新建** KB 隔离。迁移存量是另一个操作(需按 KB 把老数据移进子目录,并更新行)。
- 已知取舍:用户给了不可写/不存在的自定义路径 → 索引时才报错(create 不预检)。自托管场景可接受。
