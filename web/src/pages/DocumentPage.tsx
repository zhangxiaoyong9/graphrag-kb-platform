import { useKb } from "./kb-context";
import { useAsync } from "../hooks/useAsync";
import { listDocuments } from "../api/client";
import { Card, CardHeader } from "../components/ui";
import { DocumentManager } from "../components/DocumentManager";
import { IconDoc } from "../components/icons";

/** Documents tab: list / upload / paste / delete (deletion auto-rebuilds the graph). */
export default function DocumentPage() {
  const { kbId } = useKb();
  const docs = useAsync(() => listDocuments(kbId), [kbId]);
  return (
    <Card>
      <CardHeader
        title="文档管理"
        subtitle="上传文件或粘贴文本；删除文档将自动重建图谱（增量）"
        icon={<IconDoc width={18} height={18} />}
      />
      <div className="mt-5">
        <DocumentManager kbId={kbId} docs={docs.data ?? []} reload={docs.reload} />
      </div>
    </Card>
  );
}
