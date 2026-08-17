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
        <div><span className="date-kicker">STRATEGY LAB · NO REAL ORDERS</span><h1>不写代码，也能验证<em>自己的规则。</em></h1><p>选择买入信号、行业过滤、成交方式、仓位和退出条件；先回放今日真实数据，再决定是否让本地模拟账户持续运行。</p></div>
        <div className="strategy-hero-principles"><span>POINT-IN-TIME</span><strong>只看当时数据</strong><small>策略版本化 · T+1 · 按交易所申报单位 · 成本与滑点</small></div>
      </section>

      <StrategyLab />

      <footer><span>资金脉络 · 策略实验室</span><p>模拟结果不代表未来表现，不连接券商，不构成任何投资建议。</p></footer>
    </main>
  );
}
