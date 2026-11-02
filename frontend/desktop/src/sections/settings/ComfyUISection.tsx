/* ComfyUI — the local image-generation engine's connection panel and a
 * manual generation form. The chat-side generate_image tool shares the
 * same connection: it is advertised only while the connection is enabled,
 * so the toggle here is also the tool's on/off switch.
 */

import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, Play, WandSparkles } from 'lucide-react';
import { toast } from 'sonner';
import {
  comfyCheckpoints,
  comfyGenerate,
  comfyHealth,
  getComfyConfig,
  updateComfyConfig,
  type ComfyConfig,
  type ComfyHealth,
} from '@/api/api-client/comfyui';
import { WorkspaceToggle } from '@/components/workspace/WorkspaceToggle';
import { Button } from '@/components/ui/button';

const SIZES = [
  { label: '512×512', w: 512, h: 512 },
  { label: '768×768', w: 768, h: 768 },
  { label: '1024×1024', w: 1024, h: 1024 },
  { label: '1024×576 (wide)', w: 1024, h: 576 },
  { label: '576×1024 (tall)', w: 576, h: 1024 },
];

export function ComfyUISection() {
  const qc = useQueryClient();
  const cfgQ = useQuery({ queryKey: ['comfyui-config'], queryFn: () => getComfyConfig() });
  const checkpointsQ = useQuery({
    queryKey: ['comfyui-checkpoints'],
    queryFn: () => comfyCheckpoints(),
    enabled: Boolean(cfgQ.data?.enabled),
    retry: false,
  });

  const [cfg, setCfg] = useState<ComfyConfig | null>(null);
  const [workflowDraft, setWorkflowDraft] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [health, setHealth] = useState<ComfyHealth | null>(null);

  const [prompt, setPrompt] = useState('');
  const [negative, setNegative] = useState('');
  const [sizeIdx, setSizeIdx] = useState(2);
  const [steps, setSteps] = useState(25);
  const [checkpoint, setCheckpoint] = useState('');
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  useEffect(() => {
    if (cfgQ.data && cfg === null) {
      setCfg(cfgQ.data);
      setWorkflowDraft(cfgQ.data.workflowJson);
      setCheckpoint(cfgQ.data.defaultCheckpoint);
    }
  }, [cfgQ.data, cfg]);

  const workflowDirty = workflowDraft !== null && workflowDraft !== (cfg?.workflowJson ?? '');

  async function patch(body: Partial<ComfyConfig>) {
    try {
      const next = await updateComfyConfig(body);
      setCfg(next);
      setWorkflowDraft(next.workflowJson);
      await qc.invalidateQueries({ queryKey: ['comfyui-config'] });
      toast.success('ComfyUI settings saved');
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Save failed');
    }
  }

  async function test() {
    setTesting(true);
    setHealth(null);
    try {
      const h = await comfyHealth();
      setHealth(h);
      if (!h.ok) toast.error(h.error ?? 'ComfyUI did not answer');
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Test failed');
    } finally {
      setTesting(false);
    }
  }

  async function generate() {
    if (!prompt.trim()) return;
    setGenerating(true);
    setResult(null);
    try {
      const size = SIZES[sizeIdx];
      const res = await comfyGenerate({
        prompt: prompt.trim(),
        negativePrompt: negative.trim(),
        checkpoint: checkpoint || null,
        width: size.w,
        height: size.h,
        steps,
      });
      setResult(res.path);
      toast.success('Image generated');
      await qc.invalidateQueries({ queryKey: ['workspace-files'] });
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Generation failed');
    } finally {
      setGenerating(false);
    }
  }

  if (cfgQ.isLoading || cfg === null) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground p-3">
        <Loader2 className="size-3.5 animate-spin" /> Loading ComfyUI settings…
      </div>
    );
  }

  const enabled = Boolean(cfg.enabled);

  return (
    <div className="space-y-3 h-full flex flex-col overflow-auto">
      {/* Connection */}
      <div className="rounded-xl border border-border/60 bg-card/60 p-5 space-y-4">
        <div>
          <p className="text-sm font-semibold">ComfyUI connection</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            Point August at your own ComfyUI. While enabled, the generate_image tool is offered in
            chat; disabled, it is not advertised at all.
          </p>
        </div>

        <div className="flex items-center justify-between p-3 rounded-lg border border-border/60 bg-black/10">
          <div>
            <p className="text-xs font-semibold">Enable ComfyUI</p>
            <p className="text-2xs text-muted-foreground">
              {cfg.toolAdvertised ? 'generate_image is advertised in chat' : 'generate_image is not advertised'}
            </p>
          </div>
          <WorkspaceToggle enabled={enabled} onToggle={(checked) => void patch({ enabled: checked })} />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3 items-end">
          <label className="block">
            <span className="text-2xs text-muted-foreground">Base URL</span>
            <input
              value={cfg.baseUrl}
              onChange={(e) => setCfg({ ...cfg, baseUrl: e.target.value })}
              onBlur={(e) => {
                if (e.target.value !== cfg.baseUrl) void patch({ baseUrl: e.target.value });
              }}
              placeholder="http://127.0.0.1:8188"
              className="mt-1 h-8 w-full rounded-md border border-border/60 bg-card/60 px-2.5 text-xs font-mono outline-none focus:border-primary"
            />
          </label>
          <Button size="sm" variant="outline" onClick={() => void test()} disabled={testing || !enabled}>
            {testing ? <Loader2 className="size-3 animate-spin" /> : <Play className="size-3" />}
            Test connection
          </Button>
        </div>

        {health && (
          <p className={`text-2xs ${health.ok ? 'text-success-fg' : 'text-destructive'}`}>
            {health.ok
              ? `Connected — ComfyUI ${health.comfyuiVersion || 'unknown version'}${health.devices?.[0]?.name ? ` · ${health.devices[0].name}` : ''}`
              : `Failed: ${health.error}`}
          </p>
        )}

        {enabled && (checkpointsQ.data?.checkpoints?.length ?? 0) > 0 && (
          <label className="block">
            <span className="text-2xs text-muted-foreground">Default checkpoint</span>
            <select
              value={checkpoint}
              onChange={(e) => void patch({ defaultCheckpoint: e.target.value })}
              className="mt-1 h-8 w-full rounded-md border border-border/60 bg-card/60 px-2 text-xs"
            >
              <option value="">(first available)</option>
              {(checkpointsQ.data?.checkpoints ?? []).map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
        )}
        {enabled && checkpointsQ.error && (
          <p className="text-2xs text-destructive">
            Could not list checkpoints — is ComfyUI running? ({String(checkpointsQ.error)})
          </p>
        )}

        <label className="block">
          <span className="text-2xs text-muted-foreground">
            Custom workflow (API format) — leave empty for the built-in txt2img graph
          </span>
          <textarea
            value={workflowDraft ?? ''}
            onChange={(e) => setWorkflowDraft(e.target.value)}
            rows={5}
            placeholder='{"3": {"class_type": "KSampler", "inputs": {"seed": "%SEED%", …}}}'
            className="mt-1 w-full rounded-md border border-border/60 bg-black/20 p-2.5 text-2xs font-mono outline-none focus:border-primary resize-y"
          />
          <span className="text-2xs text-muted-foreground">
            Markers: %PROMPT% %NEGATIVE% %CHECKPOINT% %WIDTH% %HEIGHT% %STEPS% %CFG% %SEED%
          </span>
        </label>
        {workflowDirty && (
          <div className="flex items-center justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setWorkflowDraft(cfg?.workflowJson ?? '')}
            >
              Cancel
            </Button>
            <Button size="sm" onClick={() => void patch({ workflowJson: workflowDraft ?? '' })}>
              <Check className="size-3" /> Save workflow
            </Button>
          </div>
        )}
      </div>

      {/* Manual generation */}
      <div className="rounded-xl border border-border/60 bg-card/60 p-5 space-y-3">
        <div>
          <p className="text-sm font-semibold">Generate an image</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            The same path chat uses. The result lands in the workspace and opens as an artifact.
          </p>
        </div>

        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={2}
          placeholder="Describe the image…"
          disabled={!enabled}
          className="w-full rounded-md border border-border/60 bg-black/20 p-2.5 text-xs outline-none focus:border-primary resize-y"
        />
        <input
          value={negative}
          onChange={(e) => setNegative(e.target.value)}
          placeholder="Negative prompt (optional)"
          disabled={!enabled}
          className="h-8 w-full rounded-md border border-border/60 bg-black/20 px-2.5 text-xs outline-none focus:border-primary"
        />

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <label className="block">
            <span className="text-2xs text-muted-foreground">Size</span>
            <select
              value={sizeIdx}
              onChange={(e) => setSizeIdx(Number(e.target.value))}
              disabled={!enabled}
              className="mt-1 h-8 w-full rounded-md border border-border/60 bg-card/60 px-2 text-xs"
            >
              {SIZES.map((s, i) => (
                <option key={s.label} value={i}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-2xs text-muted-foreground">Steps</span>
            <input
              type="number"
              min={1}
              max={100}
              value={steps}
              onChange={(e) => setSteps(Math.max(1, Math.min(100, Number(e.target.value) || 25)))}
              disabled={!enabled}
              className="mt-1 h-8 w-full rounded-md border border-border/60 bg-card/60 px-2 text-xs"
            />
          </label>
          <label className="block md:col-span-2">
            <span className="text-2xs text-muted-foreground">Checkpoint</span>
            <select
              value={checkpoint}
              onChange={(e) => setCheckpoint(e.target.value)}
              disabled={!enabled}
              className="mt-1 h-8 w-full rounded-md border border-border/60 bg-card/60 px-2 text-xs"
            >
              <option value="">(default)</option>
              {(checkpointsQ.data?.checkpoints ?? []).map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex items-center gap-3">
          <Button size="sm" onClick={() => void generate()} disabled={generating || !enabled || !prompt.trim()}>
            {generating ? <Loader2 className="size-3 animate-spin" /> : <WandSparkles className="size-3" />}
            {generating ? 'Generating…' : 'Generate'}
          </Button>
          {result && <p className="text-2xs font-mono text-muted-foreground truncate">{result}</p>}
        </div>
        {!enabled && (
          <p className="text-2xs text-muted-foreground">Enable the connection above to generate.</p>
        )}
      </div>
    </div>
  );
}
