import { useEffect, useState } from "react";

// Cumulative milliseconds at which the *informational* stage advances.
// These are estimates for UI narration only - no stage is ever marked
// "complete" from this timer. The real per-task result (execution_log)
// is the only thing that can mark anything as actually done or failed.
// The pipeline is sequential (see app/executor.py), so most real wall-clock
// time goes into writing sections/tables - the timer reflects that by
// holding on stage 2 ("write") the longest, and never auto-advances past
// stage 3 ("build") - it waits there for the real response.
const STAGE_SCHEDULE = [1800, 3200, 9000];

/** Returns an index into a 0..3 stage list that advances on a timer while
 * `isActive` is true, resetting to 0 each time a new run starts. */
export function useProgressStages(isActive) {
  const [stageIndex, setStageIndex] = useState(0);

  useEffect(() => {
    setStageIndex(0);

    if (!isActive) return undefined;

    const ids = STAGE_SCHEDULE.map((delay, i) => setTimeout(() => setStageIndex(i + 1), delay));
    return () => {
      ids.forEach(clearTimeout);
    };
  }, [isActive]);

  return stageIndex;
}
