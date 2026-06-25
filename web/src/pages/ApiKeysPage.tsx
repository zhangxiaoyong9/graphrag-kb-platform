import { Link } from "react-router-dom";
import { Card, CardHeader, Badge, EmptyState } from "../components/ui";
import { IconKey, IconExternal, IconSystem } from "../components/icons";

/** Capability-placeholder page: this version has no API-key backend. Honest about it. */
export default function ApiKeysPage() {
  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="API Keys"
          subtitle="程序化访问凭证管理"
          icon={<IconKey width={18} height={18} />}
          actions={<Badge tone="warning">当前版本未启用</Badge>}
        />
        <div className="mt-5">
          <EmptyState
            icon={<IconKey />}
            title="API Key 管理尚未启用"
            hint="当前版本不提供 API Key 的创建、轮转与撤销。所有 REST 接口暂无鉴权，仅适用于单机 / 内网部署。"
          />
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="当前如何访问 API" icon={<IconExternal width={18} height={18} />} />
          <div className="mt-4 space-y-3 text-[13px]">
            <p className="text-muted">
              直接调用 REST 接口即可，无需 Key。完整端点列表见「系统状态 · API 接口」。
            </p>
            <div className="space-y-1 rounded-xl border border-line bg-surface-2/50 p-3">
              <p className="font-mono text-[12px] text-ink">{`curl http://127.0.0.1:8000/kbs`}</p>
              <p className="font-mono text-[12px] text-ink">
                {`curl -X POST http://127.0.0.1:8000/kbs/1/query \\`}
              </p>
              <p className="pl-4 font-mono text-[12px] text-ink">{`-H 'Content-Type: application/json' \\`}</p>
              <p className="pl-4 font-mono text-[12px] text-ink">{`-d '{"method":"local","query":"..."}'`}</p>
            </div>
            <Link to="/system" className="inline-flex items-center gap-1 text-[13px] text-brand hover:underline">
              <IconSystem width={14} height={14} /> 查看全部接口
            </Link>
          </div>
        </Card>

        <Card>
          <CardHeader title="后续路线" icon={<IconKey width={18} height={18} />} />
          <ul className="mt-4 space-y-3 text-[13px]">
            {[
              { t: "访问凭证", d: "签发 / 轮转 / 撤销 API Key，按 Key 计量调用" },
              { t: "限流与配额", d: "按 Key / 按知识库的速率与用量限制" },
              { t: "多租户隔离", d: "租户边界、权限范围（只读 / 读写）" },
              { t: "计费", d: "按 token / 成本汇总出账" },
            ].map((x) => (
              <li key={x.t} className="flex gap-3">
                <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-line-strong" />
                <span>
                  <span className="font-medium text-ink">{x.t}</span>
                  <span className="ml-1 text-muted">— {x.d}</span>
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-[12px] text-muted">
            以上属于 SaaS 化阶段能力，不在当前版本范围（原始设计文档已明确「多租户 / SaaS / 鉴权计费」为非目标）。
          </p>
        </Card>
      </div>
    </div>
  );
}
