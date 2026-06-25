import { useKb } from "./kb-context";
import { Card, CardHeader } from "../components/ui";
import { GraphView } from "../components/GraphView";
import { IconGraph } from "../components/icons";

/** Graph tab: interactive force-directed visualization of entities + relations. */
export default function GraphPage() {
  const { kbId } = useKb();
  return (
    <Card>
      <CardHeader
        title="图谱可视化"
        subtitle="按 degree 取 Top-N 实体，或搜索邻域；颜色按社区聚类"
        icon={<IconGraph width={18} height={18} />}
      />
      <div className="mt-5">
        <GraphView kbId={kbId} limit={120} />
      </div>
    </Card>
  );
}
