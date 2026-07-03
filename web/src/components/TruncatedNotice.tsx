/** Amber row-cap notice shared by all result surfaces (rendered inside QueryResultView).
 * Copy is fixed and deliberately decoupled from the backend ROW_CAP constant. */
export function TruncatedNotice() {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-warning-soft px-3 py-2 text-[12px] text-[#b26b00]">
      <span>结果已达行数上限，已截断。可缩小范围或调整上限。</span>
    </div>
  );
}
