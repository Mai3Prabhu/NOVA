const STEPS = [
  {
    num: "01",
    title: "Describe",
    body: "You write one plain-language request. No forms, no templates to fill in.",
  },
  {
    num: "02",
    title: "Plan & execute",
    body: "An LLM planner breaks the request into a task plan; an executor runs each task through a fixed set of tools - gathering context, writing sections, building tables.",
  },
  {
    num: "03",
    title: "Build",
    body: "The results are assembled into a real .docx file with proper headings, styles, and tables - ready to download.",
  },
];

export default function HowItWorks() {
  return (
    <section id="how-it-works" className="section">
      <p className="eyebrow">· FIG.001</p>
      <h2 className="section-title">HOW IT WORKS</h2>
      <div className="step-grid">
        {STEPS.map((step) => (
          <div className="panel step-card" key={step.num}>
            <span className="step-num">{step.num}</span>
            <h3>{step.title}</h3>
            <p>{step.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
