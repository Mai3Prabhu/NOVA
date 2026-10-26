import { useState } from "react";
import "./App.css";
import Header from "./components/Header.jsx";
import Hero from "./components/Hero.jsx";
import RequestPanel from "./components/RequestPanel.jsx";
import RobotMascot from "./components/RobotMascot.jsx";
import StatusPanel from "./components/StatusPanel.jsx";
import DocumentCard from "./components/DocumentCard.jsx";
import ErrorPanel from "./components/ErrorPanel.jsx";
import HowItWorks from "./components/HowItWorks.jsx";
import About from "./components/About.jsx";
import Footer from "./components/Footer.jsx";
import { useDocumentWorkflow } from "./hooks/useDocumentWorkflow.js";
import { useProgressStages } from "./hooks/useProgressStages.js";

const BUSY_BUTTON_LABELS = ["PLANNING…", "GATHERING CONTEXT…", "WRITING CONTENT…", "GENERATING DOCX…"];

export default function App() {
  const [requestText, setRequestText] = useState("");
  const { result, isSubmitting, error, generate, retry, clear } = useDocumentWorkflow();
  const activeStageIndex = useProgressStages(isSubmitting);

  const robotStatus = isSubmitting ? "loading" : result ? "success" : error ? "error" : "idle";

  function handleClear() {
    clear();
    setRequestText("");
  }

  return (
    <>
      <div className="grid-backdrop" aria-hidden="true" />
      <Header />

      <main>
        <section id="home" className="hero">
          <div className="hero-grid">
            <div className="hero-col hero-col--left">
              <Hero />
            </div>

            <div className="hero-col hero-col--center">
              <RobotMascot status={robotStatus} />
            </div>

            <div className="hero-col hero-col--right">
              <RequestPanel
                value={requestText}
                onChange={setRequestText}
                onSubmit={generate}
                busy={isSubmitting}
                busyLabel={BUSY_BUTTON_LABELS[Math.min(activeStageIndex, BUSY_BUTTON_LABELS.length - 1)]}
                onClear={handleClear}
                canClear={Boolean(requestText || result || error)}
              />

              {!result && isSubmitting && <StatusPanel mode="stages" activeStageIndex={activeStageIndex} />}

              {!result && !isSubmitting && error && <ErrorPanel onRetry={retry} />}

              {result && (
                <>
                  {isSubmitting ? (
                    <StatusPanel mode="stages" activeStageIndex={activeStageIndex} />
                  ) : (
                    <StatusPanel mode="log" log={result.execution_log} />
                  )}
                  <DocumentCard result={result} />
                </>
              )}
            </div>
          </div>
        </section>

        <HowItWorks />
        <About />
      </main>

      <Footer />
    </>
  );
}
