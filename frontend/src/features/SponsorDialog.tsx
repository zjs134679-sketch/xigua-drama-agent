import { Heart, X } from "lucide-react";

export default function SponsorDialog({ onClose }: { onClose: () => void }) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="sponsor-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sponsor-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <button className="dialog-close" type="button" aria-label="关闭" onClick={onClose}><X size={17} /></button>
        <Heart size={30} color="var(--red-t)" />
        <h2 id="sponsor-title">喜欢就请作者吃瓜🍉</h2>
        <p>如果这个工具帮到了你，可以自愿支持后续开发。不赞助也不影响任何功能。</p>
        <div className="payment-placeholder" role="img" aria-label="收款码图片占位">
          <span>收款码</span>
          <small>图片占位</small>
        </div>
        <button className="btn-secondary sponsor-dismiss" type="button" onClick={onClose}>关闭，继续使用</button>
      </section>
    </div>
  );
}
