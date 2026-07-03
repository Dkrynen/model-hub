import { useEffect, useRef, useState } from "react";
import { Gauge } from "lucide-react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "@/lib/api";

interface RunFrame {
  run?: number;
  tps?: number;
  done?: boolean;
  median_tps?: number;
  error?: string;
}

export function BenchmarkDialog({ onDone }: { onDone?: () => void }) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [repeat, setRepeat] = useState(2);
  const [running, setRunning] = useState(false);
  const [runs, setRuns] = useState<number[]>([]);
  const [median, setMedian] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!open) return;
    api
      .installed()
      .then((ms) => {
        const names = ms.map((m) => m.name);
        setModels(names);
        setModel((cur) => cur || names[0] || "");
      })
      .catch(() => setModels([]));
  }, [open]);

  useEffect(() => () => abortRef.current?.abort(), []);

  async function start() {
    if (!model || running) return;
    setRunning(true);
    setRuns([]);
    setMedian(null);
    abortRef.current = new AbortController();
    try {
      for await (const frame of api.benchmark(model, { repeat }, abortRef.current.signal) as AsyncGenerator<RunFrame>) {
        if (frame.error) throw new Error(String(frame.error));
        if (frame.done) {
          setMedian(frame.median_tps ?? null);
          toast.success(`${model}: ${(frame.median_tps ?? 0).toFixed(1)} tok/s (median of ${repeat})`);
          onDone?.();
        } else if (typeof frame.tps === "number") {
          setRuns((prev) => [...prev, frame.tps as number]);
        }
      }
    } catch (e) {
      toast.error(`Benchmark failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRunning(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!running) setOpen(o); }}>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary">
          <Gauge className="mr-1.5 h-3.5 w-3.5" /> Benchmark
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle className="text-sm font-semibold">Benchmark a model</DialogTitle>
        <p className="mt-1 text-[12px] text-fg-muted">
          Runs a deterministic generation and logs real tok/s — recommendations recalibrate from it.
        </p>
        <div className="mt-4 flex items-end gap-3">
          <div className="flex-1">
            <label className="mb-1.5 block text-[12px] font-medium text-fg-muted">Model</label>
            <Select value={model} onValueChange={setModel} disabled={running}>
              <SelectTrigger className="h-9 w-full"><SelectValue placeholder="Pick installed model" /></SelectTrigger>
              <SelectContent>
                {models.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="mb-1.5 block text-[12px] font-medium text-fg-muted">Runs</label>
            <Select value={String(repeat)} onValueChange={(v) => setRepeat(Number(v))} disabled={running}>
              <SelectTrigger className="h-9 w-[70px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                {[1, 2, 3, 5].map((n) => <SelectItem key={n} value={String(n)}>{n}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </div>
        {(running || runs.length > 0) && (
          <div className="mt-4">
            <Progress value={median !== null ? 100 : Math.min(95, (runs.length / repeat) * 100)} variant="iris" className="h-1.5" />
            <div className="mt-2 flex flex-wrap gap-2 font-mono text-[12px] text-fg-muted">
              {runs.map((t, i) => <span key={i}>run {i + 1}: {t.toFixed(1)} tok/s</span>)}
              {median !== null && <span className="font-semibold text-fg">median: {median.toFixed(1)} tok/s</span>}
            </div>
          </div>
        )}
        <div className="mt-5 flex justify-end gap-2">
          {running ? (
            <Button size="sm" variant="secondary" onClick={() => abortRef.current?.abort()}>Cancel</Button>
          ) : (
            <Button size="sm" onClick={start} disabled={!model}>Run benchmark</Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
