import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { cancelJob, getEvidence } from "../api";
import { useApi } from "../hooks/useApi";
import { useJob } from "../hooks/useJob";
import { useEventStream } from "../hooks/useEventStream";
import { terminal } from "../types";
import { Stepper } from "../components/Stepper";
import { EventLog } from "../components/EventLog";
import { CandidatesTable } from "../components/CandidatesTable";
import { EvidenceCard } from "../components/EvidenceCard";
import { Badge } from "../components/Badge";
import { ErrorBanner } from "../components/ErrorBanner";
import { Button, PageTitle, Skeleton, EmptyState } from "../components/UI";
export default function RunDetail() {
  const { jobId = "" } = useParams();
  const job = useJob(jobId),
    stream = useEventStream(jobId),
    cards = useApi((signal) => getEvidence(jobId, signal), [jobId]);
  const [shape, setShape] = useState("");
  const [selected, setSelected] = useState("");
  const [error, setError] = useState<Error>();
  const [cancelling, setCancelling] = useState(false);
  useEffect(() => {
    if (terminal(stream.status) || terminal(job.data?.status)) {
      cards.reload();
      job.reload();
    }
  }, [stream.status, job.data?.status]);
  const shapeIds = job.data?.params.shape_ids ?? [];
  const active = shapeIds.includes(shape) ? shape : shapeIds[0];
  const card = cards.data?.cards.find((c) => c.shape_id === active);
  const candidates =
    job.data?.candidates?.filter((c) => c.shape_id === active) ??
    card?.candidates ??
    [];
  const candidate =
    candidates.find((c) => c.id === selected) ??
    candidates.find((c) => c.id === card?.decision.candidate_id) ??
    candidates[0];
  function refresh() {
    cards.reload();
    job.reload();
  }
  return (
    <>
      <PageTitle
        title="Run detail"
        description={jobId}
        action={
          <div className="actions">
            <Badge
              tone={
                job.data?.status === "done"
                  ? "pass"
                  : job.data?.status === "failed"
                    ? "fail"
                    : "hold"
              }
            >
              {job.data?.status ?? "loading"}
            </Badge>
            {!terminal(job.data?.status) && (
              <Button
                variant="secondary"
                loading={cancelling}
                onClick={async () => {
                  setCancelling(true);
                  try {
                    await cancelJob(jobId);
                    refresh();
                  } catch (e) {
                    setError(e as Error);
                  } finally {
                    setCancelling(false);
                  }
                }}
              >
                Cancel run
              </Button>
            )}
          </div>
        }
      />
      {(job.error || error) && <ErrorBanner error={job.error ?? error} />}
      <Stepper events={stream.events} done={terminal(job.data?.status)} />
      <p className="mono">
        Event connection: {stream.connected ? "live" : stream.status}
      </p>
      {stream.error && <ErrorBanner error={stream.error} />}
      <div className="run-layout">
        <EventLog events={stream.events} />
        <div className="run-evidence">
          <div className="tabs" aria-label="Report shape">
            {shapeIds.map((id) => (
              <button
                key={id}
                className={active === id ? "active" : ""}
                onClick={() => {
                  setShape(id);
                  setSelected("");
                }}
              >
                {id.replaceAll("_", " ")}
              </button>
            ))}
          </div>
          {job.loading && !job.data ? (
            <Skeleton />
          ) : candidates.length ? (
            <CandidatesTable
              candidates={candidates}
              selected={candidate?.id}
              onSelect={(c) => setSelected(c.id)}
              delta={card?.settings.delta ?? 0.05}
            />
          ) : (
            <EmptyState
              title="Waiting for candidates"
              body="Safe proposals and their statuses appear here as the run progresses."
            />
          )}
          {card ? (
            <EvidenceCard
              card={card}
              candidate={candidate}
              onChanged={refresh}
            />
          ) : terminal(job.data?.status) ? (
            <EmptyState
              title="No evidence card saved"
              body="The job ended before shape evidence could be saved. Check the event log for the cause."
            />
          ) : (
            <p>
              Verification is running. The evidence card appears when this
              report receives a decision.
            </p>
          )}
        </div>
      </div>
    </>
  );
}
