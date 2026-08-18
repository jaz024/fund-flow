import Link from "next/link";
import StrategyLab from "../components/StrategyLab";

export default function StrategyPage() {
  return (
    <main className="app-shell strategy-page">
      <header className="topbar">
        <Link className="brand" href="/"><span className="brand-mark"><i /><i /><i /></span><span><strong>资金脉络</strong><small>A股策略实验室</small></span></Link>
        <nav aria-label="主导航"><Link href="/">今日总览</Link><Link href="/trends">板块趋势</Link><Link href="/stocks">个股异动</Link><Link className="active" href="/strategy">策略模拟</Link></nav>
        <div className="top-actions"><span className="source-pill"><i />本地观察性模拟</span></div>
      </header>

      <section className="strategy-page-hero">
        <div><span className="date-kicker">QUANT STRATEGY LAB · NO REAL ORDERS</span><h1>把一个想法，拆成<em>可验证的策略。</em></h1><p>从趋势、均值回归、波动突破或原追涨模板开始，再组合信号、成交、仓位与退出规则。没有足够真实数据的模型不会生成结果。</p></div>
        <div className="strategy-hero-principles"><span>POINT-IN-TIME</span><strong>模型可以逆势买入</strong><small>信号与执行分离 · T+1 · 版本化 · 成本与滑点</small></div>
      </section>

      <StrategyLab />

      <footer><span>资金脉络 · 策略实验室</span><p>模拟结果不代表未来表现，不连接券商，不构成任何投资建议。</p></footer>
    </main>
  );
}
