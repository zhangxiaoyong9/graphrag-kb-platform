# Phase 2b-2 — 仪表盘 + API 收口 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 2b-1 后端加一个 React 仪表盘(建库/传文档/触发/看每步每单元进度/手动重试),并顺手收口 API(Pydantic 校验、统一响应、任务列表、进度字段)。

**Architecture:** 前端 React+TS+Vite SPA 在 `web/`,FastAPI 用 StaticFiles 托管构建产物(prod)/ Vite 代理(dev);实时进度用前端轮询 `GET /jobs/{id}`(~2s);后端先收口 API 契约(Pydantic 模型 + 统一响应 + jobs 列表 + progress),前端再依契约搭建。Tailwind 样式。

**Tech Stack:** 后端 FastAPI + Pydantic v2;前端 React 18 + TypeScript + Vite 5 + Tailwind CSS 3 + Vitest + React Testing Library + msw;Python `>=3.11,<3.14`;`graphrag==3.1.*`。

## Global Constraints

- Python `>=3.11,<3.14`;SQLite WAL;2b-1 的 64 个测试必须回归通过(后端只增不改既有端点行为)。
- 前端在 `graphrag-kb-platform/web/`,独立 `package.json`;**`web/node_modules` 与 `web/dist` 入 `.gitignore`**。
- graphrag 内部仍只能在 `kb_platform/graph/graphrag_adapter.py` 引用。
- 每任务 TDD:失败测试 → 红 → 最小实现 → 绿 → 提交;约定式前缀。前端测试用 Vitest + RTL + msw,不依赖真后端。
- 后端响应以 Pydantic `response_model` 强制 schema;前端 `api/types.ts` 与后端模型字段一致。
- 查询 / WebSocket / 增量 / 鉴权 均不在本计划。

## 关键接口契约(跨任务共享)

```python
# kb_platform/api/models.py  (Task 1 定义)
class KbCreate(BaseModel):
    name: str; method: str = "standard"; settings_yaml: str = "{}"; min_unit_success_ratio: float = 1.0
class DocumentCreate(BaseModel):
    title: str; text: str
class JobCreate(BaseModel):
    method: str = "standard"
class KbOut(BaseModel):
    id: int; name: str; method: str
class DocumentOut(BaseModel):
    id: int; title: str; status: str | None = None
class UnitProgress(BaseModel):
    pending: int; running: int; succeeded: int; failed: int; total: int
class StepOut(BaseModel):
    id: int; name: str; ordinal: int; kind: str; status: str; progress: UnitProgress | None = None
class JobOut(BaseModel):
    id: int; status: str; steps: list[StepOut]
class UnitOut(BaseModel):
    id: int; subject_id: str; status: str; error: str | None = None
    llm_raw_output: str | None = None; needs_reconsolidation: bool = False
```

```python
# kb_platform/db/repository.py 新增 (Task 2)
def list_jobs_by_kb(self, kb_id: int) -> list[Job]: ...
def unit_counts_by_status(self, step_id: int) -> dict: ...   # {pending,running,succeeded,failed,total}
```

```ts
// web/src/api/types.ts (Task 5) — 镜像后端模型
export interface KbOut { id: number; name: string; method: string }
export interface DocumentOut { id: number; title: string; status: string | null }
export interface UnitProgress { pending: number; running: number; succeeded: number; failed: number; total: number }
export interface StepOut { id: number; name: string; ordinal: number; kind: string; status: StepStatus; progress: UnitProgress | null }
export interface JobOut { id: number; status: JobStatus; steps: StepOut[] }
export interface UnitOut { id: number; subject_id: string; status: UnitStatus; error: string | null; llm_raw_output: string | null; needs_reconsolidation: boolean }
```

---

### Task 1: 后端 —— Pydantic 请求/响应模型 + response_model 强制

**Files:**
- Create: `kb_platform/api/models.py`
- Modify: `kb_platform/api/routes_kbs.py`、`kb_platform/api/routes_jobs.py`
- Test: `tests/test_api_kbs.py`(扩充)、`tests/test_api_jobs.py`(扩充)

**Interfaces:**
- Produces: `models.py`(上列 BaseModel);写端点用请求模型(422);所有端点 `response_model=XxxOut`。

- [ ] **Step 1: 写 `models.py`** —— 按契约创建 `kb_platform/api/models.py`(上列全部类)。

- [ ] **Step 2: 写失败测试(422 + 响应模型)**

`tests/test_api_kbs.py` 追加:
```python
def test_create_kb_422_on_missing_name(client):
    r = client.post("/kbs", json={"method": "standard"})  # 缺 name
    assert r.status_code == 422


def test_create_kb_response_shape(client):
    r = client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    body = r.json()
    assert set(body.keys()) == {"id", "name", "method"}  # response_model 限定字段
```
`tests/test_api_jobs.py` 追加:
```python
def test_trigger_job_422_on_bad_body(client):
    r = client.post("/kbs/1/jobs", json={"method": 123})  # 类型错
    assert r.status_code == 422
```

- [ ] **Step 3: 跑红**

Run: `uv run pytest tests/test_api_kbs.py tests/test_api_jobs.py -q`
Expected: FAIL(现在 `dict` 入参不校验,返回 500 或缺字段)。

- [ ] **Step 4: 改 `routes_kbs.py`**

把 `create_kb` / `list_kbs` / `get_kb` 改用请求/响应模型:
```python
from kb_platform.api.models import KbCreate, KbOut, DocumentCreate, DocumentOut
from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase
from sqlalchemy import select


@router.post("/kbs", response_model=KbOut, status_code=201)
def create_kb(payload: KbCreate, request: Request):
    import json

    repo = request.app.state.repo
    settings = json.dumps(json.loads(payload.settings_yaml or "{}"))
    with session_scope(repo.engine) as s:
        kb = KnowledgeBase(name=payload.name, method=payload.method, settings_json=settings, data_root=request.app.state.data_root)
        s.add(kb); s.flush()
        return KbOut(id=kb.id, name=kb.name, method=kb.method)


@router.get("/kbs", response_model=list[KbOut])
def list_kbs(request: Request):
    repo = request.app.state.repo
    with session_scope(repo.engine) as s:
        return [KbOut(id=k.id, name=k.name, method=k.method) for k in s.scalars(select(KnowledgeBase))]


@router.get("/kbs/{kb_id}", response_model=KbOut)
def get_kb(kb_id: int, request: Request):
    repo = request.app.state.repo
    with session_scope(repo.engine) as s:
        kb = s.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404)
        return KbOut(id=kb.id, name=kb.name, method=kb.method)
```
`add_document` / `list_documents` 改 JSON 分支用 `DocumentCreate`(multipart 分支保留),响应 `DocumentOut`:
```python
@router.post("/kbs/{kb_id}/documents", response_model=DocumentOut, status_code=201)
async def add_document(kb_id: int, request: Request, title: str | None = None, text: str | None = None, file: UploadFile | None = File(None)):
    repo = request.app.state.repo
    if file is not None:
        raw = file.file.read().decode("utf-8", errors="replace")
        doc = repo.add_document(kb_id=kb_id, title=title or file.filename, text=raw)
    elif text is not None:
        doc = repo.add_document(kb_id=kb_id, title=title or "untitled", text=text)
    else:
        raise HTTPException(400, "provide 'text' or 'file'")
    return DocumentOut(id=doc.id, title=doc.title, status=doc.status)


@router.get("/kbs/{kb_id}/documents", response_model=list[DocumentOut])
def list_documents(kb_id: int, request: Request):
    repo = request.app.state.repo
    return [DocumentOut(id=d.id, title=d.title, status=d.status) for d in repo.get_documents(kb_id)]
```
> 保留 `UploadFile` 现有 import 来源;`HTTPException`、`session_scope` 已 import。

- [ ] **Step 5: 改 `routes_jobs.py`**

`trigger_job` 用 `JobCreate`,各读端点加 `response_model`:
```python
from kb_platform.api.models import JobCreate, JobOut, StepOut, UnitOut, UnitProgress

@router.post("/kbs/{kb_id}/jobs", response_model=KbOut, status_code=202)
def trigger_job(kb_id: int, payload: JobCreate, request: Request):
    # ... 404 检查保留 ...
    job = repo.create_job_pending(kb_id=kb_id, method=payload.method)
    return KbOut(id=job.id, name=job.status, method=...)  # 见下注
```
> **注:** `trigger_job` 原返回 `{id, status}`。为复用 `KbOut` 不合适(字段不匹配)。改为定义一个专用响应:`JobCreated(BaseModel): id: int; status: str`(加到 models.py),`response_model=JobCreated`,返回 `JobCreated(id=job.id, status=job.status)`。**不要**硬塞进 `KbOut`。`GET /jobs/{id}` / `/steps` / `/units` 用 `JobOut` / `list[StepOut]` / `list[UnitOut]`(progress 字段在 Task 2 填)。
```python
@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, request: Request): ...  # 构造 StepOut(id,name,ordinal,kind,status, progress=None)(Task 2 填 progress)

@router.get("/jobs/{job_id}/steps", response_model=list[StepOut])
def get_steps(job_id: int, request: Request): ...

@router.get("/steps/{step_id}/units", response_model=list[UnitOut])
def get_units(step_id: int, request: Request, status: str | None = None): ...
```

- [ ] **Step 6: 跑绿 + 全量**

Run: `uv run pytest -q`
Expected: 新 422/响应测试通过 + 2b-1 全部回归通过(既有断言用的字段仍在)。若 2b-1 测试因响应字段变化失败,调整测试断言到新 schema(字段是超集,通常不破坏)。

- [ ] **Step 7: 提交**

```bash
git add kb_platform/api/models.py kb_platform/api/routes_kbs.py kb_platform/api/routes_jobs.py tests/test_api_kbs.py tests/test_api_jobs.py
git commit -m "feat: pydantic request/response models with 422 validation"
```

---

### Task 2: 后端 —— `GET /kbs/{id}/jobs` + 每步 progress

**Files:**
- Modify: `kb_platform/db/repository.py`(`list_jobs_by_kb`、`unit_counts_by_status`)
- Modify: `kb_platform/api/routes_jobs.py`、`kb_platform/api/routes_kbs.py`
- Test: `tests/test_api_jobs.py`(扩充)

**Interfaces:**
- Produces: `list_jobs_by_kb(kb_id)`、`unit_counts_by_status(step_id)->{pending,running,succeeded,failed,total}`;`GET /kbs/{id}/jobs`;`GET /jobs/{id}` 每步带 `progress`。

- [ ] **Step 1: 写失败测试**

`tests/test_api_jobs.py` 追加:
```python
def test_list_jobs_by_kb(client):
    j1 = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    j2 = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    jobs = client.get("/kbs/1/jobs").json()
    assert {j["id"] for j in jobs} == {j1, j2}


def test_job_progress_per_step(client):
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    # 手动给 extract_graph 步种几个 unit
    steps = client.get(f"/jobs/{job_id}/steps").json()
    extract = [s for s in steps if s["name"] == "extract_graph"][0]
    from kb_platform.db.repository import Repository
    # 通过 app.state.repo 注入(测试里直接拿 client.app)
    repo = client.app.state.repo
    repo.add_units(extract["id"], [("chunk", "c1"), ("chunk", "c2")])
    body = client.get(f"/jobs/{job_id}").json()
    ex = [s for s in body["steps"] if s["name"] == "extract_graph"][0]
    assert ex["progress"]["total"] == 2 and ex["progress"]["pending"] == 2
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_api_jobs.py -q`
Expected: FAIL(`/kbs/1/jobs` 不存在;progress 缺)。

- [ ] **Step 3: 加 repo 方法**

`kb_platform/db/repository.py`:
```python
    def list_jobs_by_kb(self, kb_id: int) -> list:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(Job).where(Job.kb_id == kb_id).order_by(Job.id.desc())))

    def unit_counts_by_status(self, step_id: int) -> dict:
        with session_scope(self.engine) as s:
            units = list(s.scalars(select(Unit).where(Unit.step_id == step_id)))
        counts = {"pending": 0, "running": 0, "succeeded": 0, "failed": 0, "total": len(units)}
        for u in units:
            if u.status in counts:
                counts[u.status] += 1
        return counts
```

- [ ] **Step 4: 加端点 + 填 progress**

`routes_kbs.py` 加(或放 routes_jobs.py 也可):
```python
@router.get("/kbs/{kb_id}/jobs", response_model=list)
def list_jobs(kb_id: int, request: Request):
    repo = request.app.state.repo
    return [{"id": j.id, "status": j.status} for j in repo.list_jobs_by_kb(kb_id)]
```
> 用 `response_model=list`(元素是 `{id,status}`);或定义 `JobListItem(BaseModel): id:int; status:str` 更严谨 —— 推荐定义并 `response_model=list[JobListItem]`(加到 models.py)。

`routes_jobs.py` `get_job` / `get_steps` 填 progress:
```python
def _step_out(repo, s) -> StepOut:
    progress = None
    if s["kind"] == "unit_fanout":
        progress = UnitProgress(**repo.unit_counts_by_status(s["id"]))
    return StepOut(id=s["id"], name=s["name"], ordinal=s["ordinal"], kind=s["kind"], status=s["status"], progress=progress)
```
> `get_job`/`get_steps` 内构造 StepOut 时调 `_step_out`。step dict 来自 `repo.get_steps` → 转 dict(注意 detached 对象:读标量 `.id/.name/.ordinal/.kind/.status`)。

- [ ] **Step 5: 跑绿 + 全量**

Run: `uv run pytest -q && uv run ruff check kb_platform tests`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add kb_platform/db/repository.py kb_platform/api/routes_jobs.py kb_platform/api/routes_kbs.py kb_platform/api/models.py tests/test_api_jobs.py
git commit -m "feat: list jobs by kb + per-step unit progress"
```

---

### Task 3: 后端 —— FastAPI 托管 SPA(StaticFiles + history fallback)

**Files:**
- Modify: `kb_platform/api/app.py`
- Test: `tests/test_api_app.py`(新建)

**Interfaces:**
- Produces: `create_app` 在 `web/dist` 存在时挂 `StaticFiles`(html=True);`/api` 路由优先。无 `web/dist` 时(Task 4 前)不挂,API 仍可用。

- [ ] **Step 1: 写失败测试**

`tests/test_api_app.py`:
```python
import os
from fastapi.testclient import TestClient
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from kb_platform.api.app import create_app


def test_api_routes_work_without_spa(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    c = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    assert c.get("/kbs").status_code == 200  # API 可用,即使无 web/dist


def test_spa_served_when_dist_exists(tmp_path, monkeypatch):
    web = tmp_path / "web" / "dist"
    web.mkdir(parents=True)
    (web / "index.html").write_text("<html>SPA</html>")
    monkeypatch.setattr("kb_platform.api.app.WEB_DIST", str(web))
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    c = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    r = c.get("/")
    assert r.status_code == 200 and "SPA" in r.text
    # history fallback:未知非 /api 路径回 index.html
    assert c.get("/kbs/1/jobs/5").status_code == 200
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_api_app.py -q`
Expected: FAIL(无 SPA 托管)。

- [ ] **Step 3: 改 app.py**

```python
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from kb_platform.api.routes_kbs import router as kbs_router
from kb_platform.api.routes_jobs import router as jobs_router
from kb_platform.db.repository import Repository

WEB_DIST = os.environ.get("KB_WEB_DIST", str(Path(__file__).resolve().parents[2] / "web" / "dist"))


def create_app(repo: Repository, data_root: str = ".") -> FastAPI:
    app = FastAPI(title="KB Platform")
    app.state.repo = repo
    app.state.data_root = data_root
    app.include_router(kbs_router)
    app.include_router(jobs_router)
    if Path(WEB_DIST).exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="spa")
    return app
```
> `/` 挂在最后,`/api`... 不存在(路由无 `/api` 前缀)—— 实际 `/kbs` 等已注册,`StaticFiles` 兜底其余。`html=True` 提供 index.html + 目录 fallback;SPA history 路由(如 `/kbs/1/jobs/5`)会被 StaticFiles 当文件找不到 → 返回 index.html?StaticFiles 对不存在的路径返回 404,不会回 index.html。**需补一个 catch-all 回退到 index.html**:

```python
    if Path(WEB_DIST).exists():
        from fastapi import Request
        from fastapi.responses import FileResponse

        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str, request: Request):
            return FileResponse(WEB_DIST / "index.html")
```
> 把 SPA 托管拆成:`/assets`(Vite 产物静态)+ catch-all `/{full_path:path}` 返回 index.html。**注意 catch-all 必须不拦截已注册的 API 路由** —— FastAPI 路由匹配优先于 catch-all(显式路由先注册)。验证:`GET /kbs` 命中 router,`GET /kbs/1/jobs/5` 命中 router 里的 `/kbs/{kb_id}`?不 —— `/kbs/1/jobs/5` 没有匹配的 API 路由,落入 catch-all → index.html。✓ 但 `GET /kbs/1`(API)命中 router,不落入 catch-all。✓ 测试 `test_spa_served_when_dist_exists` 断言 `/kbs/1/jobs/5` → 200 index.html(SPA 路由),`/` → index.html。第二个测试里没测 API 命中,但第一个测试保证无 dist 时 API 可用。**额外**:在有 dist 时也应保证 `/kbs` 仍返回 JSON —— catch-all 不应吞 API。FastAPI 中显式注册的路由优先于 `{full_path:path}` catch-all,故 `/kbs` 命中 router。实现者验证 `GET /kbs`(有 dist 时)返回 JSON list 而非 index.html。

- [ ] **Step 4: 跑绿 + 全量**

Run: `uv run pytest -q && uv run ruff check kb_platform tests`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/api/app.py tests/test_api_app.py
git commit -m "feat: serve built SPA with history fallback"
```

---

### Task 4: 前端脚手架 —— Vite + React + TS + Tailwind + Vitest + msw

**Files:**
- Create: `web/package.json`、`web/vite.config.ts`、`web/tsconfig.json`、`web/tailwind.config.js`、`web/postcss.config.js`、`web/index.html`、`web/src/main.tsx`、`web/src/App.tsx`、`web/src/index.css`、`web/src/setupTests.ts`
- Modify: `.gitignore`(`web/node_modules`、`web/dist`)

**Interfaces:**
- Produces: 可 `npm install && npm run dev`(代理 `/` → 8000)、`npm run build`、`npm test` 的 React 工程;`App.tsx` 渲染占位。

- [ ] **Step 1: 写 `.gitignore` 追加**

```
web/node_modules/
web/dist/
```

- [ ] **Step 2: 写 `web/package.json`**

```json
{
  "name": "kb-platform-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "test:watch": "vitest",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.0"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.0",
    "@testing-library/react": "^16.0.0",
    "@testing-library/user-event": "^14.5.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "autoprefixer": "^10.4.20",
    "jsdom": "^25.0.0",
    "msw": "^2.4.0",
    "postcss": "^8.4.41",
    "tailwindcss": "^3.4.10",
    "typescript": "^5.5.0",
    "vite": "^5.4.0",
    "vitest": "^2.0.0"
  }
}
```

- [ ] **Step 3: 写配置文件**

`web/vite.config.ts`:
```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/kbs": "http://localhost:8000", "/jobs": "http://localhost:8000", "/steps": "http://localhost:8000", "/units": "http://localhost:8000" } },
  test: { environment: "jsdom", setupFiles: ["./src/setupTests.ts"], globals: true },
});
```
`web/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020", "lib": ["ES2020", "DOM", "DOM.Iterable"], "module": "ESNext",
    "skipLibCheck": true, "moduleResolution": "bundler", "jsx": "react-jsx", "strict": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"]
}
```
`web/tailwind.config.js`:
```js
export default { content: ["./index.html", "./src/**/*.{ts,tsx}"], theme: { extend: {} }, plugins: [] };
```
`web/postcss.config.js`:
```js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```
`web/index.html`:
```html
<!doctype html><html lang="en"><head><meta charset="UTF-8" /><title>KB Platform</title></head>
<body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>
```
`web/src/index.css`:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```
`web/src/main.tsx`:
```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><BrowserRouter><App /></BrowserRouter></React.StrictMode>);
```
`web/src/setupTests.ts`:
```ts
import "@testing-library/jest-dom";
```

- [ ] **Step 4: 写占位 App + 一个冒烟测试**

`web/src/App.tsx`:
```tsx
export default function App() {
  return <div className="p-4 text-lg">KB Platform</div>;
}
```
`web/src/App.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import App from "./App";

test("renders title", () => {
  render(<App />);
  expect(screen.getByText("KB Platform")).toBeInTheDocument();
});
```

- [ ] **Step 5: 安装 + 跑**

```bash
cd web && npm install && npm test && npm run build
```
Expected: vitest 1 passed;`npm run build` 产出 `web/dist`(验证 `ls dist/index.html`)。

- [ ] **Step 6: 提交**

```bash
git add .gitignore web/package.json web/vite.config.ts web/tsconfig.json web/tailwind.config.js web/postcss.config.js web/index.html web/src
git commit -m "feat: scaffold react+vite+tailwind+vitest frontend"
```

---

### Task 5: 前端 —— API 类型 + client

**Files:**
- Create: `web/src/api/types.ts`、`web/src/api/client.ts`
- Test: `web/src/api/client.test.ts`

**Interfaces:**
- Produces: `types.ts`(镜像后端模型)、`client.ts`(`listKbs`、`createKb`、`getKb`、`listDocuments`、`addDocument`、`listJobsByKb`、`triggerJob`、`getJob`、`getSteps`、`getUnits`、`retryUnit`、`retryStep`)。

- [ ] **Step 1: 写 `types.ts`**

```ts
export type JobStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";
export type StepStatus = "pending" | "running" | "succeeded" | "partially_failed" | "failed";
export type UnitStatus = "pending" | "running" | "succeeded" | "failed";

export interface KbOut { id: number; name: string; method: string }
export interface DocumentOut { id: number; title: string; status: string | null }
export interface UnitProgress { pending: number; running: number; succeeded: number; failed: number; total: number }
export interface StepOut { id: number; name: string; ordinal: number; kind: string; status: StepStatus; progress: UnitProgress | null }
export interface JobOut { id: number; status: JobStatus; steps: StepOut[] }
export interface UnitOut { id: number; subject_id: string; status: UnitStatus; error: string | null; llm_raw_output: string | null; needs_reconsolidation: boolean }
export interface KbCreate { name: string; method?: string; settings_yaml?: string; min_unit_success_ratio?: number }
export interface DocumentCreate { title: string; text: string }
```

- [ ] **Step 2: 写 `client.ts` + 测试**

`web/src/api/client.ts`:
```ts
import { KbOut, DocumentOut, JobOut, StepOut, UnitOut, KbCreate, DocumentCreate } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json() as Promise<T>;
}
export const listKbs = () => req<KbOut[]>("/kbs");
export const createKb = (b: KbCreate) => req<KbOut>("/kbs", { method: "POST", body: JSON.stringify(b) });
export const getKb = (id: number) => req<KbOut>(`/kbs/${id}`);
export const listDocuments = (kbId: number) => req<DocumentOut[]>(`/kbs/${kbId}/documents`);
export const addDocument = (kbId: number, b: DocumentCreate) => req<DocumentOut>(`/kbs/${kbId}/documents`, { method: "POST", body: JSON.stringify(b) });
export const listJobsByKb = (kbId: number) => req<{ id: number; status: string }[]>(`/kbs/${kbId}/jobs`);
export const triggerJob = (kbId: number, method = "standard") => req<{ id: number; status: string }>(`/kbs/${kbId}/jobs`, { method: "POST", body: JSON.stringify({ method }) });
export const getJob = (id: number) => req<JobOut>(`/jobs/${id}`);
export const getSteps = (jobId: number) => req<StepOut[]>(`/jobs/${jobId}/steps`);
export const getUnits = (stepId: number, status?: string) => req<UnitOut[]>(`/steps/${stepId}/units` + (status ? `?status=${status}` : ""));
export const retryUnit = (id: number) => req<{ ok: boolean }>(`/units/${id}/retry`, { method: "POST" });
export const retryStep = (id: number) => req<{ reset: number }>(`/steps/${id}/retry`, { method: "POST" });
```
`web/src/api/client.test.ts`(用 msw):
```tsx
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { listKbs, createKb, retryUnit } from "./client";

const server = setupServer(
  http.get("/kbs", () => HttpResponse.json([{ id: 1, name: "kb1", method: "standard" }])),
  http.post("/kbs", async ({ request }) => HttpResponse.json({ id: 2, name: (await request.json()).name, method: "standard" })),
  http.post("/units/5/retry", () => HttpResponse.json({ ok: true })),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("listKbs + createKb + retryUnit", async () => {
  const kbs = await listKbs();
  expect(kbs[0].name).toBe("kb1");
  const kb = await createKb({ name: "kb2" });
  expect(kb.id).toBe(2);
  expect((await retryUnit(5)).ok).toBe(true);
});
```

- [ ] **Step 3: 跑**

```bash
cd web && npm test
```
Expected: 2 tests pass(App + client)。

- [ ] **Step 4: 提交**

```bash
git add web/src/api
git commit -m "feat: typed api client + msw tests"
```

---

### Task 6: 前端 —— KB 列表页 + KbForm

**Files:**
- Create: `web/src/pages/KbListPage.tsx`、`web/src/components/KbForm.tsx`、`web/src/components/StatusBadge.tsx`
- Modify: `web/src/App.tsx`(路由)
- Test: `web/src/pages/KbListPage.test.tsx`

**Interfaces:**
- Produces: `KbListPage`(列出 KB + 新建)、`KbForm`(name/method/settings_yaml/min_success_ratio → createKb)、`StatusBadge`。

- [ ] **Step 1: 写 `StatusBadge` + `KbForm` + `KbListPage`**

`web/src/components/StatusBadge.tsx`:
```tsx
const COLORS: Record<string, string> = { succeeded: "bg-green-100 text-green-800", failed: "bg-red-100 text-red-800", running: "bg-blue-100 text-blue-800", pending: "bg-gray-100 text-gray-700", partially_failed: "bg-yellow-100 text-yellow-800" };
export default function StatusBadge({ status }: { status: string }) {
  return <span className={`px-2 py-0.5 rounded text-xs ${COLORS[status] ?? "bg-gray-100"}`}>{status}</span>;
}
```
`web/src/components/KbForm.tsx`:
```tsx
import { useState } from "react";
import { createKb } from "../api/client";
import type { KbOut } from "../api/types";

export default function KbForm({ onCreated }: { onCreated: (kb: KbOut) => void }) {
  const [name, setName] = useState("");
  const [method, setMethod] = useState("standard");
  const [settings, setSettings] = useState("{}");
  const [ratio, setRatio] = useState("1.0");
  return (
    <form onSubmit={async (e) => { e.preventDefault(); const kb = await createKb({ name, method, settings_yaml: settings, min_unit_success_ratio: parseFloat(ratio) }); onCreated(kb); }} className="space-y-2">
      <input className="border p-1 w-full" placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />
      <select className="border p-1" value={method} onChange={(e) => setMethod(e.target.value)}><option>standard</option><option>fast</option></select>
      <textarea className="border p-1 w-full h-24" value={settings} onChange={(e) => setSettings(e.target.value)} placeholder='{"llm":{"model_provider":"deepseek","model":"deepseek-chat"}}' />
      <input className="border p-1 w-24" type="number" step="0.01" value={ratio} onChange={(e) => setRatio(e.target.value)} />
      <button className="bg-blue-600 text-white px-3 py-1 rounded">Create KB</button>
    </form>
  );
}
```
`web/src/pages/KbListPage.tsx`:
```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listKbs } from "../api/client";
import type { KbOut } from "../api/types";
import KbForm from "../components/KbForm";

export default function KbListPage() {
  const [kbs, setKbs] = useState<KbOut[]>([]);
  const reload = () => listKbs().then(setKbs);
  useEffect(() => { reload(); }, []);
  return (
    <div className="p-4 space-y-4">
      <h1 className="text-xl font-bold">Knowledge Bases</h1>
      <KbForm onCreated={reload} />
      <ul className="space-y-1">
        {kbs.map((k) => <li key={k.id}><Link to={`/kbs/${k.id}`} className="text-blue-600 underline">{k.name}</Link> <span className="text-gray-500">({k.method})</span></li>)}
      </ul>
    </div>
  );
}
```
`App.tsx`:
```tsx
import { Routes, Route, Navigate } from "react-router-dom";
import KbListPage from "./pages/KbListPage";
export default function App() {
  return <Routes><Route path="/" element={<KbListPage />} /><Route path="*" element={<Navigate to="/" />} /></Routes>;
}
```
> 后续 Task 7/8 加更多 Route。

- [ ] **Step 2: 写测试**

`web/src/pages/KbListPage.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router-dom";
import KbListPage from "./KbListPage";

const server = setupServer(
  http.get("/kbs", () => HttpResponse.json([{ id: 1, name: "demo", method: "standard" }])),
  http.post("/kbs", async ({ request }) => HttpResponse.json({ id: 2, name: (await request.json()).name, method: "standard" })),
);
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

test("lists kbs and creates one", async () => {
  render(<MemoryRouter><KbListPage /></MemoryRouter>);
  expect(await screen.findByText("demo")).toBeInTheDocument();
  await userEvent.type(screen.getByPlaceholderText("name"), "newkb");
  await userEvent.click(screen.getByText("Create KB"));
  expect(await screen.findByText("newkb")).toBeInTheDocument();
});
```

- [ ] **Step 3: 跑 + 提交**

```bash
cd web && npm test && npm run build
git add web/src/pages web/src/components web/src/App.tsx
git commit -m "feat: kb list page + create form"
```

---

### Task 7: 前端 —— KB 详情页(文档 + 任务列表 + 触发)

**Files:**
- Create: `web/src/pages/KbDetailPage.tsx`、`web/src/components/DocumentUpload.tsx`
- Modify: `web/src/App.tsx`(路由)
- Test: `web/src/pages/KbDetailPage.test.tsx`

**Interfaces:**
- Produces: `KbDetailPage`(文档列表 + 上传 + 任务列表 + 触发按钮)、`DocumentUpload`(文件/文本)。

- [ ] **Step 1: 写组件**

`web/src/components/DocumentUpload.tsx`:
```tsx
import { useState } from "react";
import { addDocument } from "../api/client";

export default function DocumentUpload({ kbId, onUploaded }: { kbId: number; onUploaded: () => void }) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  return (
    <form onSubmit={async (e) => { e.preventDefault(); await addDocument(kbId, { title: title || "untitled", text }); setTitle(""); setText(""); onUploaded(); }} className="space-y-2">
      <input className="border p-1 w-full" placeholder="title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <textarea className="border p-1 w-full h-24" placeholder="paste text" value={text} onChange={(e) => setText(e.target.value)} />
      <button className="bg-blue-600 text-white px-3 py-1 rounded">Add Document</button>
    </form>
  );
}
```
`web/src/pages/KbDetailPage.tsx`:
```tsx
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getKb, listDocuments, listJobsByKb, triggerJob } from "../api/client";
import type { KbOut, DocumentOut } from "../api/types";
import DocumentUpload from "../components/DocumentUpload";
import StatusBadge from "../components/StatusBadge";

export default function KbDetailPage() {
  const { id } = useParams();
  const kbId = Number(id);
  const [kb, setKb] = useState<KbOut | null>(null);
  const [docs, setDocs] = useState<DocumentOut[]>([]);
  const [jobs, setJobs] = useState<{ id: number; status: string }[]>([]);
  const reload = () => { getKb(kbId).then(setKb); listDocuments(kbId).then(setDocs); listJobsByKb(kbId).then(setJobs); };
  useEffect(() => { reload(); }, [kbId]);
  if (!kb) return <div className="p-4">loading…</div>;
  return (
    <div className="p-4 space-y-4">
      <h1 className="text-xl font-bold">{kb.name} <span className="text-gray-500">({kb.method})</span></h1>
      <section><h2 className="font-semibold">Documents</h2><ul>{docs.map((d) => <li key={d.id}>{d.title}</li>)}</ul>
        <DocumentUpload kbId={kbId} onUploaded={reload} /></section>
      <section><h2 className="font-semibold">Jobs</h2>
        <button onClick={async () => { await triggerJob(kbId); reload(); }} className="bg-green-600 text-white px-3 py-1 rounded">Trigger Index</button>
        <ul>{jobs.map((j) => <li key={j.id}><Link to={`/kbs/${kbId}/jobs/${j.id}`}>job {j.id}</Link> <StatusBadge status={j.status} /></li>)}</ul>
      </section>
    </div>
  );
}
```
`App.tsx` 加 Route:`<Route path="/kbs/:id" element={<KbDetailPage />} />`(import 之)。

- [ ] **Step 2: 写测试**

`web/src/pages/KbDetailPage.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import KbDetailPage from "./KbDetailPage";

const server = setupServer(
  http.get("/kbs/1", () => HttpResponse.json({ id: 1, name: "demo", method: "standard" })),
  http.get("/kbs/1/documents", () => HttpResponse.json([{ id: 1, title: "doc1", status: "parsed" }])),
  http.get("/kbs/1/jobs", () => HttpResponse.json([{ id: 7, status: "succeeded" }])),
  http.post("/kbs/1/jobs", () => HttpResponse.json({ id: 8, status: "pending" })),
);
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

test("shows kb, documents, jobs; trigger adds a job", async () => {
  render(<MemoryRouter initialEntries={["/kbs/1"]}><Routes><Route path="/kbs/:id" element={<KbDetailPage />} /></Routes></MemoryRouter>);
  expect(await screen.findByText("demo")).toBeInTheDocument();
  expect(screen.getByText("doc1")).toBeInTheDocument();
  expect(await screen.findByText("job 7")).toBeInTheDocument();
  await userEvent.click(screen.getByText("Trigger Index"));
  expect(await screen.findByText("job 8")).toBeInTheDocument();
});
```

- [ ] **Step 3: 跑 + 提交**

```bash
cd web && npm test && npm run build
git add web/src/pages web/src/components web/src/App.tsx
git commit -m "feat: kb detail page (documents + jobs + trigger)"
```

---

### Task 8: 前端 —— 任务详情页(步骤时间线 + 单元表 + 重试 + 轮询)

**Files:**
- Create: `web/src/pages/JobDetailPage.tsx`、`web/src/components/StepTimeline.tsx`、`web/src/components/UnitTable.tsx`、`web/src/hooks/useJobPolling.ts`
- Modify: `web/src/App.tsx`(路由)
- Test: `web/src/pages/JobDetailPage.test.tsx`、`web/src/hooks/useJobPolling.test.ts`

**Interfaces:**
- Produces: `useJobPolling(jobId)`(2s 轮询,终态停)、`StepTimeline`、`UnitTable`(过滤+重试)、`JobDetailPage`。

- [ ] **Step 1: 写 `useJobPolling`**

`web/src/hooks/useJobPolling.ts`:
```ts
import { useEffect, useState } from "react";
import { getJob } from "../api/client";
import type { JobOut } from "../api/types";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
export function useJobPolling(jobId: number | null) {
  const [job, setJob] = useState<JobOut | null>(null);
  useEffect(() => {
    if (jobId == null) return;
    let stop = false;
    const tick = async () => { const j = await getJob(jobId); if (!stop) setJob(j); return j; };
    tick();
    const h = setInterval(async () => { const j = await tick(); if (j && TERMINAL.has(j.status)) clearInterval(h); }, 2000);
    return () => { stop = true; clearInterval(h); };
  }, [jobId]);
  return job;
}
```

- [ ] **Step 2: 写 `StepTimeline` + `UnitTable`**

`web/src/components/StepTimeline.tsx`:
```tsx
import type { StepOut } from "../api/types";
import StatusBadge from "./StatusBadge";
export default function StepTimeline({ steps, selected, onSelect }: { steps: StepOut[]; selected: number | null; onSelect: (id: number) => void }) {
  return (
    <ol className="space-y-1">
      {steps.map((s) => {
        const p = s.progress;
        const pct = p && p.total ? Math.round((p.succeeded / p.total) * 100) : null;
        return (
          <li key={s.id} className={`p-2 border rounded cursor-pointer ${selected === s.id ? "border-blue-600" : ""}`} onClick={() => onSelect(s.id)}>
            <div className="flex items-center gap-2"><span className="font-medium">{s.name}</span> <StatusBadge status={s.status} /></div>
            {pct != null && <div className="h-2 bg-gray-200 rounded mt-1"><div className="h-2 bg-blue-600 rounded" style={{ width: `${pct}%` }} /></div>}
            {p && <div className="text-xs text-gray-500">{p.succeeded}/{p.total} units</div>}
          </li>
        );
      })}
    </ol>
  );
}
```
`web/src/components/UnitTable.tsx`:
```tsx
import { useEffect, useState } from "react";
import { getUnits, retryUnit } from "../api/client";
import type { UnitOut } from "../api/types";
import StatusBadge from "./StatusBadge";
export default function UnitTable({ stepId, active }: { stepId: number | null; active: boolean }) {
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [filter, setFilter] = useState("");
  const reload = () => { if (stepId != null) getUnits(stepId, filter || undefined).then(setUnits); };
  useEffect(reload, [stepId, filter]);
  useEffect(() => { if (active) { const h = setInterval(reload, 2000); return () => clearInterval(h); } }, [active, stepId]);
  return (
    <div>
      <div className="flex gap-2 my-2">{["", "pending", "running", "succeeded", "failed"].map((f) => <button key={f} className={`px-2 py-0.5 rounded border ${filter === f ? "bg-blue-600 text-white" : ""}`} onClick={() => setFilter(f)}>{f || "all"}</button>)}</div>
      <table className="w-full text-sm"><tbody>
        {units.map((u) => <tr key={u.id} className="border-t"><td className="p-1 font-mono text-xs">{u.subject_id.slice(0, 12)}</td><td className="p-1"><StatusBadge status={u.status} /></td>
          <td className="p-1">{u.status === "failed" && <button onClick={async () => { await retryUnit(u.id); reload(); }} className="text-blue-600 underline">retry</button>}</td>
          <td className="p-1 text-xs text-gray-600">{u.error && <details><summary>error</summary><pre className="whitespace-pre-wrap">{u.error}</pre></details>}</td></tr>)}
      </tbody></table>
    </div>
  );
}
```

- [ ] **Step 3: 写 `JobDetailPage`**

`web/src/pages/JobDetailPage.tsx`:
```tsx
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useJobPolling } from "../hooks/useJobPolling";
import StepTimeline from "../components/StepTimeline";
import UnitTable from "../components/UnitTable";
import StatusBadge from "../components/StatusBadge";

export default function JobDetailPage() {
  const { jobId } = useParams();
  const id = Number(jobId);
  const job = useJobPolling(id);
  const [selected, setSelected] = useState<number | null>(null);
  if (!job) return <div className="p-4">loading…</div>;
  const step = job.steps.find((s) => s.id === selected) ?? null;
  return (
    <div className="p-4 grid grid-cols-2 gap-4">
      <div><h1 className="text-xl font-bold">Job {job.id} <StatusBadge status={job.status} /></h1>
        <StepTimeline steps={job.steps} selected={selected} onSelect={setSelected} /></div>
      <div><h2 className="font-semibold">{step ? step.name : "select a step"}</h2>{step && <UnitTable stepId={step.id} active={job.status === "running"} />}</div>
    </div>
  );
}
```
`App.tsx` 加 Route:`<Route path="/kbs/:id/jobs/:jobId" element={<JobDetailPage />} />`。

- [ ] **Step 4: 写测试**

`web/src/hooks/useJobPolling.test.tsx`:
```tsx
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { useJobPolling } from "./useJobPolling";

const server = setupServer(http.get("/jobs/1", () => HttpResponse.json({ id: 1, status: "succeeded", steps: [] })));
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

test("loads job and stops on terminal", async () => {
  const { result } = renderHook(() => useJobPolling(1));
  await waitFor(() => expect(result.current?.status).toBe("succeeded"));
});
```
`web/src/pages/JobDetailPage.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import JobDetailPage from "./JobDetailPage";

const server = setupServer(
  http.get("/jobs/9", () => HttpResponse.json({ id: 9, status: "partially_failed", steps: [{ id: 91, name: "extract_graph", ordinal: 2, kind: "unit_fanout", status: "partially_failed", progress: { pending: 0, running: 0, succeeded: 1, failed: 1, total: 2 } }] })),
  http.get("/steps/91/units", () => HttpResponse.json([{ id: 911, subject_id: "chunk-fail", status: "failed", error: "boom", llm_raw_output: null, needs_reconsolidation: false }])),
  http.post("/units/911/retry", () => HttpResponse.json({ ok: true })),
);
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

test("shows steps, units, retry failed unit", async () => {
  render(<MemoryRouter initialEntries={["/kbs/1/jobs/9"]}><Routes><Route path="/kbs/:id/jobs/:jobId" element={<JobDetailPage />} /></Routes></MemoryRouter>);
  expect(await screen.findByText("extract_graph")).toBeInTheDocument();
  await userEvent.click(screen.getByText("extract_graph"));
  expect(await screen.findByText("chunk-fail".slice(0, 12))).toBeInTheDocument();
  await userEvent.click(screen.getByText("retry"));
});
```

- [ ] **Step 5: 跑 + 提交**

```bash
cd web && npm test && npm run build
git add web/src
git commit -m "feat: job detail page (step timeline + unit table + retry + polling)"
```

---

### Task 9: 构建 + 集成冒烟

**Files:**
- Test: `tests/test_spa_served.py`(后端,验 `web/dist` 存在时被托管)
- Verify: `cd web && npm run build` 产出 dist;后端 `create_app` 托管它。

**Interfaces:**
- Produces: 验证 `npm run build` → `web/dist`,FastAPI 托管 index.html,`/kbs` 仍返回 JSON(catch-all 不吞 API)。

- [ ] **Step 1: 构建前端**

```bash
cd web && npm run build && ls dist/index.html dist/assets
```
Expected: dist 产物存在。

- [ ] **Step 2: 后端冒烟测试**

`tests/test_spa_served.py`:
```python
from pathlib import Path
from fastapi.testclient import TestClient
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from kb_platform.api.app import create_app

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


def test_api_and_spa_coexist(tmp_path, monkeypatch):
    if not DIST.exists():
        import pytest
        pytest.skip("web/dist not built; run `npm run build` in web/")
    monkeypatch.setattr("kb_platform.api.app.WEB_DIST", str(DIST))
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    c = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    assert c.get("/kbs").status_code == 200  # API 仍是 JSON
    import json
    assert isinstance(c.get("/kbs").json(), list)
    root = c.get("/")
    assert root.status_code == 200 and "<div id=\"root\">" in root.text  # SPA
    assert c.get("/kbs/1/jobs/5").status_code == 200  # history fallback → index.html
```

- [ ] **Step 3: 跑全量**

```bash
cd .. && uv run pytest -q && uv run ruff check kb_platform tests && (cd web && npm run build)
```
Expected: 后端全绿 + ruff clean + 前端构建成功。

- [ ] **Step 4: 提交**

```bash
git add tests/test_spa_served.py
git commit -m "test: spa served + api coexist smoke"
```

---

## Self-Review(写完后自查)

**1. Spec 覆盖:**
- React SPA + FastAPI 托管 + 轮询 → Task 3(SPA 托管)+ Task 4(脚手架)+ Task 8(轮询)✓
- 三视图 → Task 6/7/8 ✓
- 任务详情页(步骤时间线 + 单元表 + 重试)→ Task 8 ✓
- Tailwind → Task 4 ✓
- API 收口(Pydantic 422 / 统一响应 / `GET /kbs/{id}/jobs` / `progress`)→ Task 1 + Task 2 ✓
- 测试(后端 TestClient / 前端 Vitest+RTL+msw / 2b-1 回归)→ 各任务 + Task 9 ✓
- WebSocket / 查询 / 增量 / 鉴权 → 显式非目标 ✓

**2. 占位符扫描:** 无 TBD;Task 3 的 SPA 托管给出 catch-all 实现 + 明确 API 路由优先的验证要求。`trigger_job` 用专用 `JobCreated` 响应模型(不硬塞 KbOut)已注明。

**3. 类型一致性:** 后端 `models.py` 字段 ↔ 前端 `types.ts` 字段一致(KbOut/DocumentOut/StepOut/JobOut/UnitOut/UnitProgress);client.ts 方法名 ↔ 各页面调用一致(listKbs/createKb/getKb/listDocuments/addDocument/listJobsByKb/triggerJob/getJob/getSteps/getUnits/retryUnit/retryStep)。

**已识别范围说明:** Playwright E2E 默认延后(组件测试 + 构建冒烟足够)。`community_reports` 在 DeepSeek 上为空的问题不属本计划(Phase 3)。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-25-phase2b2-dashboard.md`. Two execution options:

**1. Subagent-Driven(推荐)** — 每任务派发独立 subagent + 两阶段评审(后端任务 Python subagent,前端任务同样 general-purpose subagent 跑 npm)。
**2. Inline Execution** — 当前会话批量执行 + 检查点。

Which approach?
